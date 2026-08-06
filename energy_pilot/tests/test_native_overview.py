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

    def test_version_15_config_migrates_pv_economics(self):
        raw = {"version": 15, "site": {}, "planning": {}, "grid_policy": {}, "battery_policy": {}}
        migrated = main.migrate_legacy(raw)
        self.assertEqual(migrated["version"], 16)
        self.assertIn("economics", migrated)


if __name__ == "__main__":
    unittest.main()
