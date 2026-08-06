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
            patch.object(main, "update_learning"),
            patch.object(main, "planner_snapshot", return_value={}),
            patch.object(main, "overview_metrics", return_value={}),
            patch.object(main, "pv_curtailment_notification", return_value={}),
        ):
            result = main.get_overview()

        self.assertEqual(result["api_version"], 3)
        self.assertEqual(result["site"]["name"], "Kuldkinga")
        self.assertEqual(result["price"]["history_slots"], [])
        self.assertIn("energy_value", result)

    def test_energy_value_contains_measured_horizon_and_investment(self):
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
                    "grid_import_kw": 1.0,
                    "grid_export_kw": 2.0,
                }
            ],
            "plan": {
                "horizon_financial": {
                    "import_cost_cents": 10.0,
                    "export_revenue_cents": 40.0,
                    "wear_cost_cents": 5.0,
                }
            },
        }
        measured = {
            "period": "2026-08",
            "tracking_since": "2026-08-01T00:00:00+00:00",
            "bill_result_cents": 500.0,
            "result_after_wear_cents": 450.0,
            "export_revenue_cents": 600.0,
            "export_kwh": 40.0,
            "import_cost_cents": 100.0,
            "import_kwh": 5.0,
            "wear_cost_cents": 50.0,
            "negative_price_protection_minutes": 30.0,
        }

        value = main.energy_value_payload(cfg, snapshot, price, measured)

        self.assertEqual(value["measured"]["invoice_amount_cents"], -500.0)
        self.assertEqual(value["planning_horizon"]["bill_result_cents"], 30.0)
        self.assertEqual(value["planning_horizon"]["price_protection_minutes"], 15.0)
        self.assertEqual(value["investment"]["total_system_cost_eur"], 30000.0)
        self.assertIsNotNone(value["investment"]["projected_payback_months"])

    def test_version_15_config_migrates_pv_economics(self):
        raw = {"version": 15, "site": {}, "planning": {}, "grid_policy": {}, "battery_policy": {}}
        migrated = main.migrate_legacy(raw)
        self.assertEqual(migrated["version"], 16)
        self.assertIn("economics", migrated)


if __name__ == "__main__":
    unittest.main()
