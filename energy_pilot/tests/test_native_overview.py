import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import main


class NativeOverviewTests(unittest.TestCase):
    def test_overview_exposes_site_name_and_api_version(self):
        cfg = main.EnergyPilotConfig(site={"name": "Kuldkinga"})
        snapshot = {"observed_at": "2026-08-06T12:00:00+00:00"}
        history = {
            "slots": [],
            "retention_days": 7,
            "recorder": {"available": True},
            "storage_file": "/config/history.json",
        }
        with (
            patch.object(main, "load_config", return_value=cfg),
            patch.object(main, "state_snapshot", return_value=snapshot),
            patch.object(main, "price_snapshot", return_value={"slots": []}),
            patch.object(main, "measurement_history", return_value=history),
            patch.object(main, "apply_slot_plan"),
            patch.object(main, "record_insights"),
            patch.object(main, "qilowatt_snapshot", return_value={}),
            patch.object(main, "monthly_benefits", return_value={}),
            patch.object(main, "insights_summary", return_value={}),
            patch.object(main, "update_learning"),
            patch.object(main, "planner_snapshot", return_value={}),
            patch.object(main, "overview_metrics", return_value={}),
            patch.object(main, "pv_curtailment_notification", return_value={}),
        ):
            result = main.get_overview()

        self.assertEqual(result["api_version"], 5)
        self.assertEqual(result["site"]["name"], "Kuldkinga")
        self.assertEqual(result["price"]["history_slots"], [])
        self.assertIn("energy_value", result)

    def test_measurement_history_keeps_yesterday_slots_inside_rolling_window(self):
        cfg = main.EnergyPilotConfig(site={"timezone": "UTC"})
        observed = main.datetime.fromisoformat("2026-08-07T00:15:00+00:00")

        def slot(start: str, pv_kw: float) -> dict:
            return {
                "start": start,
                "end": (main.datetime.fromisoformat(start) + main.timedelta(minutes=15)).isoformat(),
                "metrics": {
                    "pv_kw": {"weighted_sum": pv_kw * 900, "weight_seconds": 900},
                    "load_kw": {"weighted_sum": 1.0 * 900, "weight_seconds": 900},
                },
                "sources": {"pv_kw": "energy_pilot_storage", "load_kw": "energy_pilot_storage"},
                "sample_count": 1,
            }

        data = {
            "slots": {
                "visible-yesterday": slot("2026-08-06T23:45:00+00:00", 2.0),
                "inside-24h": slot("2026-08-06T01:00:00+00:00", 1.0),
                "too-old": slot("2026-08-05T23:45:00+00:00", 3.0),
                "future": slot("2026-08-07T00:30:00+00:00", 4.0),
            }
        }

        result = main.public_measurement_history(cfg, data, observed)

        self.assertEqual(
            [item["start"] for item in result],
            ["2026-08-06T01:00:00+00:00", "2026-08-06T23:45:00+00:00"],
        )

    def test_energy_value_contains_avoided_grid_value_and_investment(self):
        cfg = main.EnergyPilotConfig(
            planning={"horizon_hours": 24},
            battery_policy={"system_cost_eur": 12000},
            economics={"pv_system_cost_eur": 18000},
        )
        snapshot = {"observed_at": "2026-08-06T12:00:00+00:00"}
        price = {
            "slots": [
                {
                    "action": "LIMIT EXPORT",
                    "load_forecast_kw": 4.0,
                    "grid_import_kw": 1.0,
                    "grid_export_kw": 2.0,
                    "import_cents_kwh": 20.0,
                }
            ],
            "plan": {
                "horizon_financial": {
                    "import_cost_cents": 5.0,
                    "export_revenue_cents": 40.0,
                    "wear_cost_cents": 5.0,
                }
            },
        }
        measured = {
            "period": "2026-08",
            "tracking_since": "2026-08-01T00:00:00+00:00",
            "owner_tracking_since": "2026-08-05T00:00:00+00:00",
            "bill_result_cents": 500.0,
            "export_revenue_cents": 600.0,
            "export_kwh": 40.0,
            "import_cost_cents": 100.0,
            "import_kwh": 5.0,
            "owner_load_kwh": 20.0,
            "owner_self_supplied_kwh": 15.0,
            "owner_grid_to_battery_kwh": 2.0,
            "owner_baseline_grid_cost_cents": 400.0,
            "owner_avoided_import_savings_cents": 300.0,
            "owner_grid_charging_cost_cents": 40.0,
            "owner_wear_cost_cents": 50.0,
            "owner_value_before_wear_cents": 860.0,
            "owner_value_after_wear_cents": 810.0,
            "negative_price_protection_minutes": 30.0,
        }
        owner_history = {
            "owner_tracking_since": "2026-08-01T00:00:00+00:00",
            "totals": {"owner_value_after_wear_cents": 900.0},
            "data_quality": {"owner_coverage_hours": 120.0},
        }

        value = main.energy_value_payload(cfg, snapshot, price, measured, owner_history)

        self.assertEqual(value["measured"]["invoice_amount_cents"], -500.0)
        self.assertEqual(value["measured"]["avoided_import_savings_cents"], 300.0)
        # 4 kW load for 15 min at 20 c/kWh means 20 c baseline cost.
        self.assertEqual(value["planning_horizon"]["baseline_grid_cost_cents"], 20.0)
        # 3 kW is self supplied for 15 min => 15 c avoided grid cost.
        self.assertEqual(value["planning_horizon"]["avoided_import_savings_cents"], 15.0)
        self.assertEqual(value["planning_horizon"]["owner_value_after_wear_cents"], 50.0)
        self.assertEqual(value["planning_horizon"]["price_protection_minutes"], 15.0)
        self.assertEqual(value["investment"]["total_system_cost_eur"], 30000.0)
        self.assertTrue(value["investment"]["includes_avoided_grid_purchases"])
        self.assertIsNotNone(value["investment"]["projected_payback_months"])

    def test_payback_uses_owner_value_not_only_energy_bill_cashflow(self):
        cfg = main.EnergyPilotConfig(
            planning={"horizon_hours": 24},
            battery_policy={"system_cost_eur": 6000},
            economics={"pv_system_cost_eur": 18000},
        )
        snapshot = {"observed_at": "2026-08-06T12:00:00+00:00"}
        slots = [
            {
                "load_forecast_kw": 4.0,
                "grid_import_kw": 0.0,
                "grid_export_kw": 0.0,
                "import_cents_kwh": 20.0,
            }
            for _ in range(96)
        ]
        price = {
            "slots": slots,
            "plan": {"horizon_financial": {"import_cost_cents": 0.0, "export_revenue_cents": 0.0, "wear_cost_cents": 0.0}},
        }
        measured = {"period": "2026-08", "bill_result_cents": 0.0}

        value = main.energy_value_payload(cfg, snapshot, price, measured, {})

        self.assertEqual(value["planning_horizon"]["bill_result_cents"], 0.0)
        self.assertEqual(value["planning_horizon"]["avoided_import_savings_cents"], 1920.0)
        self.assertGreater(value["investment"]["annualized_owner_value_eur"], 0.0)
        self.assertIsNotNone(value["investment"]["projected_payback_months"])


    def test_payback_is_stable_inside_the_same_fifteen_minute_bucket(self):
        cfg = main.EnergyPilotConfig(
            revision=7,
            planning={"horizon_hours": 24},
            battery_policy={"system_cost_eur": 6000},
            economics={"pv_system_cost_eur": 18000},
        )
        main.PAYBACK_CACHE.update({"bucket": None, "signature": None, "result": None})
        with patch.object(main, "completed_owner_daily_samples", return_value=([500.0] * 7, 168.0)):
            first = main.stable_payback_projection(
                cfg, main.datetime.fromisoformat("2026-08-06T12:02:00+00:00"), 200.0
            )
            same_bucket = main.stable_payback_projection(
                cfg, main.datetime.fromisoformat("2026-08-06T12:14:00+00:00"), 1200.0
            )
            next_bucket = main.stable_payback_projection(
                cfg, main.datetime.fromisoformat("2026-08-06T12:16:00+00:00"), 1200.0
            )

        self.assertEqual(first["annualized_owner_value_eur"], same_bucket["annualized_owner_value_eur"])
        self.assertNotEqual(first["annualized_owner_value_eur"], next_bucket["annualized_owner_value_eur"])
        self.assertEqual(first["projection_update_interval_minutes"], 15)

    def test_legacy_slot_derives_direct_pv_self_consumption_savings(self):
        slot = {
            "solar_self_consumed_kwh": 2.0,
            "load_kwh": 5.0,
            "baseline_grid_cost_cents": 100.0,
        }
        self.assertEqual(main.insight_metric_value(slot, "solar_self_consumption_savings_cents"), 40.0)


    def test_owner_backfill_uses_original_tracking_start_and_recorder_prices(self):
        cfg = main.EnergyPilotConfig(
            battery_policy={"degradation_cost_cents_kwh": 0.2},
            price_connector={"entity": "sensor.nordpool"},
        )
        observed = main.datetime.fromisoformat("2026-08-06T12:15:00+00:00")
        start = "2026-08-03T00:00:00+00:00"
        bucket = "2026-08-03T10:00:00+00:00"
        data = {
            "tracking_since": start,
            "owner_value_version": 2,
            "owner_tracking_since": "2026-08-06T12:00:00+00:00",
            "last_observed_at": "2026-08-06T12:00:00+00:00",
            "slots": {},
        }
        recorder = {
            bucket: {
                "load_kw": {"weighted_sum": 4.0 * 900, "weight_seconds": 900},
                "pv_kw": {"weighted_sum": 2.0 * 900, "weight_seconds": 900},
                "grid_import_kw": {"weighted_sum": 1.0 * 900, "weight_seconds": 900},
                "grid_export_kw": {"weighted_sum": 0.0, "weight_seconds": 900},
                "battery_kw": {"weighted_sum": 1.0 * 900, "weight_seconds": 900},
                "import_price_cents_kwh": {"weighted_sum": 20.0 * 900, "weight_seconds": 900},
                "export_price_cents_kwh": {"weighted_sum": 10.0 * 900, "weight_seconds": 900},
            }
        }
        snapshot = {"battery": {}}
        with (
            patch.object(main, "recorder_history", return_value=(recorder, None)),
            patch.object(main, "atomic_write"),
        ):
            changed = main.backfill_owner_ledger(cfg, data, snapshot, observed)

        self.assertTrue(changed)
        self.assertEqual(data["owner_value_version"], 3)
        self.assertEqual(data["owner_tracking_since"], start)
        slot = data["slots"][bucket]
        self.assertAlmostEqual(slot["load_kwh"], 1.0)
        self.assertAlmostEqual(slot["grid_to_load_kwh"], 0.25)
        self.assertAlmostEqual(slot["self_supplied_kwh"], 0.75)
        self.assertAlmostEqual(slot["avoided_import_savings_cents"], 15.0)
        self.assertAlmostEqual(slot["owner_value_after_wear_cents"], 14.95)
        self.assertEqual(data["owner_backfill"]["exact_price_slot_count"], 1)

    def test_month_benefits_use_backfilled_insights_totals(self):
        cfg = main.EnergyPilotConfig(site={"timezone": "Europe/Tallinn"})
        benefits = {"owner_self_supplied_kwh": 0.22, "period": "2026-08"}
        summary = {
            "period_start": "2026-07-31T21:00:00+00:00",
            "tracking_since": "2026-08-03T13:14:00+00:00",
            "owner_tracking_since": "2026-08-03T13:14:00+00:00",
            "totals": {
                "load_kwh": 30.0,
                "grid_import_kwh": 8.0,
                "grid_export_kwh": 12.0,
                "grid_to_load_kwh": 6.0,
                "grid_to_battery_kwh": 2.0,
                "self_supplied_kwh": 24.0,
                "battery_throughput_kwh": 5.0,
                "import_cost_cents": 160.0,
                "export_revenue_cents": 240.0,
                "baseline_grid_cost_cents": 600.0,
                "avoided_import_savings_cents": 480.0,
                "grid_charging_cost_cents": 40.0,
                "battery_wear_cents": 20.0,
                "owner_value_before_wear_cents": 680.0,
                "owner_value_after_wear_cents": 660.0,
                "negative_price_protection_minutes": 15.0,
                "bill_result_cents": 80.0,
            },
            "data_quality": {"owner_backfill": {"slot_count": 100}},
        }

        merged = main.merge_benefits_with_insights(cfg, benefits, summary)

        self.assertEqual(merged["owner_self_supplied_kwh"], 24.0)
        self.assertEqual(merged["owner_avoided_import_savings_cents"], 480.0)
        self.assertEqual(merged["owner_tracking_since"], summary["tracking_since"])
        self.assertEqual(merged["owner_backfill"]["slot_count"], 100)

    def test_version_15_config_migrates_pv_economics(self):
        raw = {"version": 15, "site": {}, "planning": {}, "grid_policy": {}, "battery_policy": {}}
        migrated = main.migrate_legacy(raw)
        self.assertEqual(migrated["version"], 16)
        self.assertIn("economics", migrated)


if __name__ == "__main__":
    unittest.main()
