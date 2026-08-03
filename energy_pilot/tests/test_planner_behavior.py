import sys
import json
import math
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import main


class PlannerBehaviorTests(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2026, 7, 28, 22, tzinfo=timezone.utc)
        self.slots = [
            {
                "start": (self.start + timedelta(minutes=15 * index)).isoformat(),
                "import_cents_kwh": 10.0,
                "export_cents_kwh": 8.0 if index == 4 else 3.0,
            }
            for index in range(32)
        ]
        self.pv = [0.0] * 12 + [10.0] * 20
        self.load = [1.0] * 32
        self.capacity = 40.0
        self.reserve = 6.0
        self.maximum = 40.0
        self.initial = 36.0
        self.duration = 0.25
        self.efficiency = 0.95

    def policy(self, profile):
        cfg = main.EnergyPilotConfig()
        cfg.planning.behavior_profile = profile
        return cfg, main.next_solar_window_policy(
            cfg, self.slots, self.pv, self.load, self.capacity,
            self.reserve, self.maximum, self.duration, self.efficiency, 82,
        )

    def test_profiles_create_distinct_pre_solar_targets(self):
        _, value = self.policy("value_first")
        _, balanced = self.policy("balanced")
        _, resilience = self.policy("resilience_first")
        self.assertEqual(value["anchor_index"], 12)
        self.assertTrue(value["eligible"])
        self.assertLess(value["target_soc_percent"], balanced["target_soc_percent"])
        self.assertLess(balanced["target_soc_percent"], resilience["target_soc_percent"])

    def test_low_confidence_disables_forecast_headroom_rule(self):
        cfg = main.EnergyPilotConfig()
        cfg.planning.behavior_profile = "resilience_first"
        policy = main.next_solar_window_policy(
            cfg, self.slots, self.pv, self.load, self.capacity,
            self.reserve, self.maximum, self.duration, self.efficiency, 70,
        )
        self.assertFalse(policy["eligible"])

    def test_value_first_can_sell_forecast_displaced_energy_to_target(self):
        cfg, policy = self.policy("value_first")
        cfg.planning.minimum_sell_profit_cents_kwh = 2.0
        cfg.battery_policy.degradation_cost_cents_kwh = 0.5
        optimized = main._planner_v3_optimize(
            cfg, self.slots, self.pv, self.load, self.initial, self.reserve,
            self.maximum, self.capacity, self.duration, self.efficiency,
            solar_policy=policy,
        )
        optimized = main._planner_v3_reconcile_sell_profit(
            cfg, self.slots, self.pv, self.load, optimized, self.initial,
            self.reserve, self.maximum, self.duration, self.efficiency, policy,
        )
        energy_at_solar = optimized["path"][policy["anchor_index"] - 1]["after"]
        self.assertAlmostEqual(energy_at_solar, policy["target_kwh"], places=3)
        self.assertTrue(any(
            item.get("solar_displaced_energy") and item["grid_export_kwh"] > 0
            for item in optimized["path"][:policy["anchor_index"]]
        ))
        self.assertGreaterEqual(
            min(item["after"] for item in optimized["path"]),
            self.reserve,
        )

    def test_headroom_value_is_consistent_for_plan_and_financial_slots(self):
        _, policy = self.policy("balanced")
        energy = policy["target_kwh"] + 2.0
        path = [{"after": energy} for _ in range(policy["anchor_index"])]
        financial_slots = [
            {
                "soc_after_percent": energy / self.capacity * 100.0,
            }
            for _ in range(policy["anchor_index"])
        ]
        expected = 2.0 * policy["curtailment_penalty_cents_kwh"]
        self.assertAlmostEqual(main.solar_headroom_cost(path, policy), expected)
        self.assertAlmostEqual(
            main.solar_headroom_cost(financial_slots, policy),
            expected,
        )

    def test_version_10_configuration_migrates_to_behavior_schema(self):
        original_dir, original_file = main.CONFIG_DIR, main.CONFIG_FILE
        try:
            with tempfile.TemporaryDirectory() as directory:
                main.CONFIG_DIR = Path(directory)
                main.CONFIG_FILE = Path(directory) / "energy_pilot_config.json"
                main.CONFIG_FILE.write_text(
                    json.dumps({
                        "version": 10,
                        "planning": {
                            "slot_minutes": 15,
                            "horizon_hours": 36,
                            "strategy": "export_value",
                        },
                    }),
                    encoding="utf-8",
                )
                config = main.load_config()
                self.assertEqual(config.version, 15)
                self.assertEqual(config.planning.behavior_profile, "balanced")
                self.assertEqual(config.qilowatt.mode, "disabled")
                self.assertTrue(config.setup.completed)
                stored = json.loads(main.CONFIG_FILE.read_text(encoding="utf-8"))
                self.assertEqual(stored["version"], 15)
        finally:
            main.CONFIG_DIR, main.CONFIG_FILE = original_dir, original_file

    def test_complete_slot_plan_runs_with_behavior_policy(self):
        config = main.EnergyPilotConfig()
        start = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        slots = []
        for index in range(104):
            stamp = start + timedelta(minutes=15 * index)
            slots.append({
                "start": stamp.isoformat(),
                "end": (stamp + timedelta(minutes=15)).isoformat(),
                "import_cents_kwh": 8.0 + 2.0 * math.sin(index / 10),
                "export_cents_kwh": 3.0 + 4.0 * math.sin(index / 10),
            })
        price = {"slots": slots}
        snapshot = {
            "battery": {
                "capacity_kwh": {"value": 40.6},
                "soc_pct": {"value": 87.0},
                "cycle_count": {"value": 100},
            },
            "load": {"power_kw": {"value": 1.084}},
        }
        originals = (
            main.weather_adjusted_solar_points,
            main.forecast_points,
            main.pv_learning_factor,
        )
        try:
            main.weather_adjusted_solar_points = lambda _cfg, points: (
                [
                    max(0.0, 15.0 * math.sin((index - 28) / 30 * math.pi))
                    for index in range(len(points))
                ],
                {"available": True, "calibration_samples": 0},
            )
            main.forecast_points = lambda _entity, points, _kind: (
                [1.084] * len(points),
                {"available": True},
            )
            main.pv_learning_factor = lambda _cfg, _stamp: (1.0, 0)
            main.apply_slot_plan(config, snapshot, price)
        finally:
            (
                main.weather_adjusted_solar_points,
                main.forecast_points,
                main.pv_learning_factor,
            ) = originals
        self.assertTrue(price["plan"]["available"])
        self.assertEqual(price["plan"]["behavior"]["label"], "Balanced")
        self.assertEqual(len(price["slots"]), 104)


if __name__ == "__main__":
    unittest.main()
