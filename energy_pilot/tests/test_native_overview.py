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

        self.assertEqual(result["api_version"], 2)
        self.assertEqual(result["site"]["name"], "Kuldkinga")
        self.assertEqual(result["price"]["history_slots"], [])


if __name__ == "__main__":
    unittest.main()
