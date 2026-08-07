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
                self.assertEqual(config.version, 16)
                self.assertEqual(config.planning.behavior_profile, "balanced")
                self.assertEqual(config.qilowatt.mode, "disabled")
                self.assertTrue(config.setup.completed)
                stored = json.loads(main.CONFIG_FILE.read_text(encoding="utf-8"))
                self.assertEqual(stored["version"], 16)
        finally:
            main.CONFIG_DIR, main.CONFIG_FILE = original_dir, original_file


    def test_native_planner_chart_slots_merge_plan_and_history(self):
        start = self.start.isoformat()
        price = {
            "market_slots": [{"start": start, "import_cents_kwh": 10.0}],
            "slots": [{"start": start, "action": "SELL", "soc_after_percent": 70.0}],
            "history_slots": [{"start": start, "actual": True, "pv_actual_kw": 4.2}],
        }
        merged = main.native_planner_chart_slots(price)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["action"], "SELL")
        self.assertEqual(merged[0]["pv_actual_kw"], 4.2)
        self.assertTrue(merged[0]["actual"])

    def test_native_planner_payload_exposes_parity_fields(self):
        cfg = main.EnergyPilotConfig()
        start = self.start.isoformat()
        original = (
            main.state_snapshot, main.price_snapshot, main.measurement_history,
            main.apply_slot_plan, main.qilowatt_snapshot, main.planner_snapshot,
        )
        try:
            main.state_snapshot = lambda _cfg: {"observed_at": start}
            main.price_snapshot = lambda _cfg: {
                "import_cents_kwh": 12.3,
                "export_cents_kwh": 7.8,
                "market_slots": [{"start": start, "import_cents_kwh": 12.3, "export_cents_kwh": 7.8}],
                "slots": [{"start": start, "import_cents_kwh": 12.3, "export_cents_kwh": 7.8}],
            }
            main.measurement_history = lambda _cfg, _snapshot: {"slots": [{"start": start, "actual": True, "pv_actual_kw": 1.2}]}
            def apply(_cfg, _snapshot, price):
                price["slots"][0].update({
                    "action": "NORMAL",
                    "slot_cash_cost_cents": 1.0,
                    "slot_wear_cost_cents": 0.2,
                })
                price["plan"] = {
                    "quality": "forecast",
                    "initial_soc_percent": 50.0,
                    "final_soc_percent": 55.0,
                    "projected_cash_cost_cents": 25.0,
                    "normal_cash_cost_cents": 40.0,
                    "projected_savings_cents": 15.0,
                    "manual_override_count": 0,
                    "today_financial": {"net_cents": 10.0},
                    "horizon_financial": {"net_cents": 20.0, "wear_cost_cents": 2.0},
                    "daily_summary": [],
                }
            main.apply_slot_plan = apply
            main.qilowatt_snapshot = lambda _cfg: {}
            main.planner_snapshot = lambda _cfg, _snapshot, _price, _q: {
                "action": "NORMAL",
                "reason": "Test",
                "confidence": 90,
                "execution": "Simulation only",
                "behavior_label": "Balanced",
            }
            payload = main.native_planner_payload(cfg)
        finally:
            (
                main.state_snapshot, main.price_snapshot, main.measurement_history,
                main.apply_slot_plan, main.qilowatt_snapshot, main.planner_snapshot,
            ) = original
        self.assertEqual(payload["api_version"], 2)
        self.assertEqual(payload["summary"]["quality_label"], "Forecast connected")
        self.assertEqual(payload["summary"]["projected_gain_cents"], 15.0)
        self.assertEqual(payload["today_financial"]["net_cents"], 10.0)
        self.assertEqual(payload["chart_slots"][0]["pv_actual_kw"], 1.2)
        self.assertEqual(payload["limits"]["max_export_kw"], cfg.grid_policy.max_export_kw)

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


    def test_grid_overhead_is_forced_to_grid_and_costed(self):
        slots = [
            {"import_cents_kwh": 10.0, "export_cents_kwh": 5.0},
            {"import_cents_kwh": 10.0, "export_cents_kwh": 5.0},
        ]
        plan = {
            "path": [
                {"grid_import_kwh": 0.0, "grid_export_kwh": 0.0, "cash_cost_cents": 0.0},
                {"grid_import_kwh": 0.0, "grid_export_kwh": 0.1, "cash_cost_cents": -0.5},
            ],
            "cash_cost_cents": -0.5,
            "objective_cents": -0.5,
        }
        result = main.apply_grid_overhead_to_plan(slots, plan, [0.08, 0.08], 0.25)
        self.assertAlmostEqual(result["path"][0]["grid_import_kwh"], 0.02, places=6)
        self.assertAlmostEqual(result["path"][0]["cash_cost_cents"], 0.2, places=6)
        self.assertAlmostEqual(result["path"][1]["grid_export_kwh"], 0.08, places=6)
        self.assertAlmostEqual(result["path"][1]["cash_cost_cents"], -0.4, places=6)
        self.assertAlmostEqual(result["cash_cost_cents"], -0.2, places=6)

    def test_grid_overhead_bootstraps_from_existing_measurement_history(self):
        original = main.load_measurement_history
        try:
            samples = [0.06, 0.07, 0.08, 0.09, 0.10, 0.20]
            slots = {}
            for index, value in enumerate(samples):
                start = (self.start + timedelta(minutes=15 * index)).isoformat()
                slots[start] = {
                    "metrics": {
                        "grid_import_kw": {"weighted_sum": value * 900, "weight_seconds": 900},
                        "grid_export_kw": {"weighted_sum": 0.0, "weight_seconds": 900},
                    }
                }
            main.load_measurement_history = lambda: {"slots": slots}
            value, count = main.measurement_grid_overhead_estimate()
        finally:
            main.load_measurement_history = original
        self.assertEqual(count, len(samples))
        self.assertIsNotNone(value)
        self.assertGreaterEqual(value, 0.06)
        self.assertLessEqual(value, 0.10)

if __name__ == "__main__":
    unittest.main()

class PlannerManualOverrideEndpointTests(unittest.TestCase):
    def setUp(self):
        self.start = datetime.now(timezone.utc).replace(second=0, microsecond=0) + timedelta(minutes=15)

    def test_preview_uses_payload_slots_without_server_error(self):
        cfg = main.EnergyPilotConfig()
        stamp = self.start.isoformat()
        originals = (
            main.load_config,
            main.load_planner_overrides,
            main.state_snapshot,
            main.price_snapshot,
            main.apply_slot_plan,
        )
        try:
            main.load_config = lambda: cfg
            main.load_planner_overrides = lambda: {}
            main.state_snapshot = lambda _cfg: {}
            main.price_snapshot = lambda _cfg: {}

            def apply(_cfg, _snapshot, price, overrides=None):
                manual = overrides is not None
                price["slots"] = [{
                    "start": stamp,
                    "slot_cash_cost_cents": -12.0 if manual else -10.0,
                    "slot_wear_cost_cents": 1.0,
                }]
                price["plan"] = {
                    "horizon_financial": {
                        "net_cents": 11.0 if manual else 9.0,
                        "export_revenue_cents": 12.0 if manual else 10.0,
                        "import_cost_cents": 0.0,
                        "wear_cost_cents": 1.0,
                    },
                    "final_soc_percent": 50.0,
                }

            main.apply_slot_plan = apply
            result = main.preview_planner_overrides({
                "slots": [stamp],
                "action": "SELL",
                "power_kw": 5.0,
                "target_soc_percent": 40.0,
            })
        finally:
            (
                main.load_config,
                main.load_planner_overrides,
                main.state_snapshot,
                main.price_snapshot,
                main.apply_slot_plan,
            ) = originals

        self.assertEqual(result["selected_slot_impact"]["candidate"]["slot_count"], 1)
        self.assertEqual(result["assessment"], "better")


    def test_saved_utc_override_matches_local_timezone_plan_slot(self):
        original_file = main.OVERRIDES_FILE
        try:
            with tempfile.TemporaryDirectory() as directory:
                main.OVERRIDES_FILE = Path(directory) / "overrides.json"
                utc_stamp = self.start.astimezone(timezone.utc)
                local_stamp = utc_stamp.astimezone(timezone(timedelta(hours=3)))
                result = main.put_planner_overrides({
                    "slots": [utc_stamp.isoformat().replace("+00:00", "Z")],
                    "action": "SELL",
                    "power_kw": 5.0,
                    "target_soc_percent": 40.0,
                })
                overrides = main.load_planner_overrides()
                cfg = main.EnergyPilotConfig()
                slots = [{
                    "start": local_stamp.isoformat(),
                    "action": "NORMAL",
                    "pv_forecast_kw": 0.0,
                    "load_forecast_kw": 0.0,
                    "import_cents_kwh": 10.0,
                    "export_cents_kwh": 10.0,
                    "charge_kw": 0.0,
                    "discharge_kw": 0.0,
                }]
                main.apply_manual_overrides(
                    cfg, slots, 40.0, 30.0, 6.0, 40.0, 0.25, 0.95, 1.0, overrides,
                )
        finally:
            main.OVERRIDES_FILE = original_file

        self.assertEqual(result["saved_count"], 1)
        self.assertTrue(slots[0]["manual_override"])
        self.assertEqual(slots[0]["action"], "SELL")

    def test_normal_reason_does_not_compare_current_normal_slot_to_normal(self):
        reason = main.planner_recommendation_reason({
            "action": "NORMAL",
            "action_reason": "Solar surplus supplies the battery.",
            "soc_before_percent": 99.0,
            "soc_after_percent": 99.0,
        }, 99.0, 32.0)
        self.assertIn("This slot stays in NORMAL", reason)
        self.assertIn("other slots", reason)
        self.assertNotIn("versus NORMAL", reason)
    def test_put_override_persists_command(self):
        original_file = main.OVERRIDES_FILE
        try:
            with tempfile.TemporaryDirectory() as directory:
                main.OVERRIDES_FILE = Path(directory) / "overrides.json"
                result = main.put_planner_overrides({
                    "slots": [self.start.isoformat()],
                    "action": "NORMAL",
                    "power_kw": None,
                    "target_soc_percent": None,
                })
                stored = main.load_planner_overrides()
        finally:
            main.OVERRIDES_FILE = original_file

        self.assertEqual(result["status"], "saved")
        self.assertEqual(result["saved_count"], 1)
        self.assertEqual(len(stored), 1)
        self.assertEqual(next(iter(stored.values()))["action"], "NORMAL")
