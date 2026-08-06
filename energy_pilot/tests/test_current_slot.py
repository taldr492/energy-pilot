import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import main


class FixedDateTime(datetime):
    current = datetime(2026, 7, 29, 13, 7, 30, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz=None):
        return cls.current if tz is None else cls.current.astimezone(tz)


class CurrentSlotTests(unittest.TestCase):
    def test_price_horizon_starts_with_active_slot(self):
        config = main.EnergyPilotConfig()
        active_start = FixedDateTime.current.replace(minute=0, second=0)
        slots = [
            {
                "start": active_start.isoformat(),
                "end": (active_start + timedelta(minutes=15)).isoformat(),
                "spot_cents_kwh": 4.0,
                "import_cents_kwh": 8.0,
                "export_cents_kwh": 2.0,
            },
            {
                "start": (active_start + timedelta(minutes=15)).isoformat(),
                "end": (active_start + timedelta(minutes=30)).isoformat(),
                "spot_cents_kwh": 5.0,
                "import_cents_kwh": 9.0,
                "export_cents_kwh": 3.0,
            },
        ]
        item = {
            "entity_id": "sensor.test_price",
            "state": "4.0",
            "last_updated": FixedDateTime.current.isoformat(),
            "attributes": {"unit_of_measurement": "c/kWh"},
        }
        original_datetime = main.datetime
        original_discover = main.discover_price_item
        original_parse = main.parse_price_slots
        try:
            main.datetime = FixedDateTime
            main.discover_price_item = lambda _entity: (item, "configured", [])
            main.parse_price_slots = lambda _item, _cfg: slots
            result = main.price_snapshot(config)
        finally:
            main.datetime = original_datetime
            main.discover_price_item = original_discover
            main.parse_price_slots = original_parse

        self.assertEqual(result["slots"][0]["start"], active_start.isoformat())
        self.assertTrue(result["slots"][0]["is_current"])
        self.assertEqual(result["slots"][0]["progress_percent"], 50.0)
        self.assertEqual(result["slots"][0]["remaining_minutes"], 7.5)
        self.assertFalse(result["slots"][1]["is_current"])
        self.assertEqual(result["slot_count"], 2)

    def test_overview_copy_has_no_redundant_planner_v3_prefix(self):
        reason = main.planner_recommendation_reason({
            "action": "SELL",
            "action_reason": "Sell before the next solar window.",
            "soc_before_percent": 60.0,
            "soc_after_percent": 52.0,
        }, 60.0, 30.0)
        self.assertNotIn("Planner v3", reason)
        self.assertIn("Projected full-horizon gain", reason)


if __name__ == "__main__":
    unittest.main()
