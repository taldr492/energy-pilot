import sys
import unittest
from datetime import datetime
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import main


def sensor(entity_id, state, unit="", device_class="", friendly_name=""):
    return {
        "entity_id": entity_id,
        "state": str(state),
        "attributes": {
            "unit_of_measurement": unit,
            "device_class": device_class,
            "friendly_name": friendly_name or entity_id,
        },
        "last_updated": "2026-07-29T10:00:00+00:00",
    }


class SetupDiscoveryTests(unittest.TestCase):
    def test_device_prefix_does_not_affect_semantic_sensor_discovery(self):
        states = [
            sensor("sensor.elektrijaam_pv_power", 7200, "W", "power"),
            sensor("sensor.elektrijaam_load_power", 640, "W", "power"),
            sensor("sensor.elektrijaam_grid_power", 82, "W", "power"),
            sensor("sensor.elektrijaam_battery_power", -3100, "W", "power"),
            sensor("sensor.elektrijaam_battery_soc", 63, "%", "battery"),
            sensor("sensor.elektrijaam_battery_capacity", 45.1, "kWh", "energy"),
            sensor("sensor.elektrijaam_total_battery_life_cycles", 412, ""),
            sensor("sensor.elektrijaam_battery_temperature", 29, "°C", "temperature"),
            sensor("sensor.elektrijaam_inverter_temperature", 47, "°C", "temperature"),
        ]
        expected = {
            "pv_power": "sensor.elektrijaam_pv_power",
            "load_power": "sensor.elektrijaam_load_power",
            "grid_power": "sensor.elektrijaam_grid_power",
            "battery_power": "sensor.elektrijaam_battery_power",
            "battery_soc": "sensor.elektrijaam_battery_soc",
            "battery_capacity": "sensor.elektrijaam_battery_capacity",
            "battery_cycles": "sensor.elektrijaam_total_battery_life_cycles",
            "battery_temperature": "sensor.elektrijaam_battery_temperature",
            "inverter_temperature": "sensor.elektrijaam_inverter_temperature",
        }
        for kind, entity_id in expected.items():
            with self.subTest(kind=kind):
                candidates = main.connector_candidates(kind, states)
                self.assertTrue(candidates)
                self.assertEqual(candidates[0]["entity_id"], entity_id)

    def test_forecast_and_limit_power_sensors_are_not_preferred(self):
        states = [
            sensor("sensor.solar_forecast_power", 8000, "W", "power"),
            sensor("sensor.pv_export_limit", 15000, "W", "power"),
            sensor("sensor.power_station_pv_power", 6200, "W", "power"),
        ]
        candidates = main.connector_candidates("pv_power", states)
        self.assertEqual(candidates[0]["entity_id"], "sensor.power_station_pv_power")

    def test_price_source_requires_today_and_tomorrow_slot_arrays(self):
        official = main.normalize_ha_item({
            "entity_id": "sensor.nord_pool_official",
            "state": "0.08",
            "attributes": {"unit_of_measurement": "EUR/kWh"},
        })
        self.assertFalse(main.price_item_compatibility(official)[0])

        custom = main.normalize_ha_item({
            "entity_id": "sensor.nordpool_kwh_ee_eur_3_10_024",
            "state": "0.08",
            "attributes": {
                "unit_of_measurement": "EUR/kWh",
                "raw_today": [
                    {"start": f"2026-07-29T{index // 4:02d}:{(index % 4) * 15:02d}:00+03:00", "value": 0.08}
                    for index in range(96)
                ],
                "raw_tomorrow": [],
                "current_price": 0.08,
            },
        })
        compatible, slot_count, timestamped = main.price_item_compatibility(custom)
        self.assertTrue(compatible)
        self.assertEqual(slot_count, 96)
        self.assertTrue(timestamped)

    def test_hacs_nordpool_entity_encodes_source_vat(self):
        self.assertEqual(
            main.nordpool_entity_vat_percent(
                "sensor.nordpool_kwh_ee_eur_3_10_024"
            ),
            24.0,
        )
        self.assertEqual(
            main.nordpool_entity_vat_percent(
                "sensor.nordpool_kwh_ee_eur_3_10_02"
            ),
            20.0,
        )
        self.assertIsNone(
            main.nordpool_entity_vat_percent("sensor.renamed_market_price")
        )

    def test_vat_inclusive_nordpool_slot_matches_tariff_breakdown(self):
        config = main.EnergyPilotConfig(
            price_connector=main.PriceConnectorConfig(
                entity="sensor.nordpool_kwh_ee_eur_3_10_024",
                source_includes_vat=True,
                include_vat=True,
                vat_percent=24.0,
            )
        )
        item = main.normalize_ha_item({
            "entity_id": config.price_connector.entity,
            "state": "0.2029508",
            "attributes": {
                "unit_of_measurement": "EUR/kWh",
                "raw_today": [{
                    "start": "2026-07-29T21:45:00+03:00",
                    "end": "2026-07-29T22:00:00+03:00",
                    "value": 0.2029508,
                }],
                "raw_tomorrow": [],
            },
        })
        slot = main.parse_price_slots(item, config)[0]
        self.assertEqual(slot["spot_cents_kwh"], 16.367)
        self.assertEqual(slot["import_cents_kwh"], 27.57512)
        self.assertEqual(slot["export_cents_kwh"], 15.09448)
        self.assertEqual(slot["components"]["spot"], 16.367)
        self.assertEqual(slot["components"]["grid_fee"], 3.69)
        self.assertEqual(slot["components"]["vat"], 5.33712)
        self.assertEqual(slot["components"]["export_balancing_vat"], -0.08952)

    def test_version_14_config_enables_encoded_nordpool_vat_once(self):
        raw = main.EnergyPilotConfig().model_dump(mode="json")
        raw["version"] = 14
        raw["price_connector"]["entity"] = (
            "sensor.nordpool_kwh_ee_eur_3_10_024"
        )
        raw["price_connector"]["source_includes_vat"] = False
        migrated = main.migrate_legacy(raw)
        self.assertTrue(main.apply_detected_price_vat(migrated))
        self.assertEqual(migrated["version"], 15)
        self.assertTrue(migrated["price_connector"]["source_includes_vat"])
        self.assertEqual(migrated["price_connector"]["vat_percent"], 24.0)

    def test_new_install_starts_in_onboarding_mode(self):
        config = main.EnergyPilotConfig()
        self.assertFalse(config.setup.completed)
        self.assertEqual(config.power_connector.pv_power_entity, "")
        self.assertEqual(config.battery_connector.cycle_count_entity, "")
        self.assertEqual(config.price_connector.entity, "")
        self.assertEqual(config.qilowatt.mode, "disabled")

    def test_qilowatt_entities_are_detected_with_any_device_prefix(self):
        states = [
            sensor("sensor.elektrijaam_qw_mode", "frrup"),
            sensor("sensor.elektrijaam_qw_source", "fusebox"),
            sensor("sensor.elektrijaam_qw_powerlimit", 12.5, "kW"),
            sensor("binary_sensor.elektrijaam_qw_connected", "on"),
        ]
        expected = {
            "mode": "sensor.elektrijaam_qw_mode",
            "source": "sensor.elektrijaam_qw_source",
            "power_limit": "sensor.elektrijaam_qw_powerlimit",
            "connected": "binary_sensor.elektrijaam_qw_connected",
        }
        for kind, entity_id in expected.items():
            with self.subTest(kind=kind):
                candidates = main.qilowatt_entity_candidates(kind, states)
                self.assertTrue(candidates)
                self.assertEqual(candidates[0]["entity_id"], entity_id)

    def test_mfrr_qilowatt_dispatch_gets_mandatory_priority(self):
        states = [
            sensor("sensor.site_qw_mode", "frrup"),
            sensor("sensor.site_qw_source", "kratt"),
            sensor("sensor.site_qw_powerlimit", 15, "kW"),
            sensor("binary_sensor.site_qw_connected", "on"),
        ]
        config = main.EnergyPilotConfig(
            qilowatt=main.QilowattConfig(mode="ha_dispatch")
        )
        snapshot = main.qilowatt_snapshot(config, states)
        self.assertTrue(snapshot["connected"])
        self.assertEqual(snapshot["action"], "SELL")
        self.assertEqual(snapshot["power_limit_kw"], 15)
        self.assertTrue(snapshot["mandatory"])
        self.assertEqual(snapshot["priority"], "mandatory")

    def test_legacy_physical_controller_mode_migrates_to_disabled(self):
        raw = main.EnergyPilotConfig().model_dump(mode="json")
        raw["version"] = 13
        raw["qilowatt"]["mode"] = "physical_monitor"
        migrated = main.migrate_legacy(raw)
        self.assertEqual(migrated["version"], 15)
        self.assertEqual(migrated["qilowatt"]["mode"], "disabled")

    def test_ha_dispatch_overrides_displayed_planner_action(self):
        config = main.EnergyPilotConfig(
            qilowatt=main.QilowattConfig(mode="ha_dispatch")
        )
        energy = {
            "health": {
                "status": "ok",
                "critical_missing": [],
                "critical_stale": [],
            },
            "battery": {"soc_pct": {"value": 60.0}},
            "flow": {"pv": "idle", "summary": "Home supplied normally"},
        }
        external = {
            "integration_mode": "ha_dispatch",
            "connected": True,
            "action": "SELL",
            "mandatory": True,
            "source_raw": "fusebox",
            "power_limit_kw": 15.0,
        }
        planner = main.planner_snapshot(config, energy, {}, external)
        self.assertEqual(planner["action"], "SELL")
        self.assertEqual(planner["execution"], "Qilowatt HA/MQTT dispatch")
        self.assertIn("mandatory mFRR dispatch", planner["reason"])
        self.assertIn("15 kW", planner["reason"])

    def test_physical_controller_mode_is_no_longer_valid(self):
        with self.assertRaises(ValueError):
            main.QilowattConfig(mode="physical_monitor")


if __name__ == "__main__":
    unittest.main()
