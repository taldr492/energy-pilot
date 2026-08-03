import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import main


class LiveEnergyUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        static_dir = Path(main.__file__).resolve().parent / "static"
        cls.html = (static_dir / "index.html").read_text()
        cls.css = (static_dir / "styles.css").read_text()
        cls.js = (static_dir / "app.js").read_text()

    def test_refresh_failure_has_explicit_error_state(self):
        self.assertIn(".top-status.refresh-failed", self.css)
        self.assertIn("function setRefreshFailed(failed)", self.js)
        self.assertIn("setRefreshFailed(false)", self.js)
        self.assertIn("showRefreshFailure", self.js)

    def test_energy_ribbons_are_route_driven_and_accessible(self):
        self.assertEqual(self.html.count('class="energy-route"'), 4)
        self.assertIn("data-energy-route=", self.html)
        self.assertEqual(self.html.count('class="energy-aurora"'), 4)
        self.assertEqual(self.html.count('class="energy-wave"'), 4)
        self.assertEqual(self.html.count('class="energy-wave secondary"'), 4)
        self.assertEqual(self.html.count('class="energy-ribbon"'), 4)
        self.assertEqual(self.html.count('class="energy-ribbon secondary"'), 4)
        self.assertEqual(self.html.count('class="energy-glint"'), 4)
        self.assertNotIn("<use href=", self.html)
        self.assertIn("@media(prefers-reduced-motion:reduce)", self.css)
        self.assertIn("route.classList.toggle('reverse',reverse)", self.js)

    def test_redundant_stats_block_is_replaced_by_battery_stored_energy(self):
        self.assertNotIn('class="card stats"', self.html)
        self.assertNotIn("capacityValue", self.js)
        self.assertNotIn("statSoc", self.js)
        self.assertNotIn("routeCount", self.js)
        self.assertIn('id="energyStored" class="stored-energy"', self.html)
        self.assertIn("kWh stored", self.js)

    def test_cards_are_borderless_and_planner_gain_uses_euros(self):
        self.assertRegex(self.css, r"\.card\s*\{\s*border:\s*0;")
        self.assertRegex(
            self.css,
            r"\.group,\s*\.stat,\s*\.benefit-total,\s*\.benefit-item",
        )
        source = Path(main.__file__).read_text()
        self.assertIn(
            "Horizon value improvement versus NORMAL: {savings / 100:.2f} €.",
            source,
        )
        self.assertNotIn(
            "Horizon value improvement versus NORMAL: {savings:.2f} c.",
            source,
        )

    def test_frontend_is_served_from_separate_static_assets(self):
        self.assertNotIn("<style>", self.html)
        self.assertNotIn("<script>", self.html)
        self.assertIn('href="static/styles.css?v=0.2.87-shared-metrics"', self.html)
        self.assertIn('src="static/app.js?v=0.2.87-invoice-sign"', self.html)

    def test_custom_energy_icons_and_favicon_are_used(self):
        self.assertIn('href="static/house.svg?v=0.2.87"', self.html)

    def test_price_breakdown_popovers_are_available(self):
        self.assertIn("price-breakdown-trigger", self.js)
        self.assertIn("global-price-tooltip", self.js)
        self.assertIn("priceBreakdownMarkup", self.js)
        self.assertIn("renderOverviewPriceBreakdowns", self.js)
        for icon in ("battery", "grid", "house", "inverter", "solar"):
            self.assertIn(f'class="icon-shape icon-{icon}"', self.html)
            self.assertIn(f'url("{icon}.svg")', self.css)
        self.assertNotIn("<svg viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\"", self.html)
        self.assertTrue(main.STATIC_DIR.is_dir())

    def test_current_plan_slot_has_progress_treatment(self):
        self.assertIn("function decorateCurrentPlanSlot", self.js)
        self.assertIn("current-slot-progress", self.js)
        self.assertIn(".plan-row.current-slot", self.css)
        self.assertIn("--slot-progress", self.css)

    def test_slot_economics_use_user_facing_signs_and_euros(self):
        self.assertIn("function slotEconomicsText(slot)", self.js)
        self.assertIn("Revenue", self.js)
        self.assertIn("Energy cost", self.js)
        self.assertIn("Battery wear", self.js)

    def test_all_planner_money_uses_user_facing_signs_and_euros(self):
        self.assertIn("function cashFlowFromCostCents(value)", self.js)
        self.assertIn("function expenseMoneyFromCents(value)", self.js)
        self.assertIn("Cash flow ${signedMoneyFromCents(cashFlowFromCostCents(day.cost_cents))}", self.js)
        self.assertIn("Optimized cash flow ${signedMoneyFromCents(cashFlowFromCostCents(plan.projected_cash_cost_cents))}", self.js)
        self.assertIn("function todayFinanceMarkup(financial)", self.js)
        self.assertNotIn("<span>Cash ${day.cost_cents} c</span>", self.js)
        self.assertNotIn("Optimized cash ${Number(plan.projected_cash_cost_cents||0).toFixed(2)} c", self.js)
        self.assertNotIn("Slot ${Number(slot.slot_cash_cost_cents||0).toFixed(2)} c", self.js)

    def test_insights_separates_bill_result_from_battery_wear(self):
        self.assertIn('id="insightsPage"', self.html)
        self.assertIn('data-insights-period="month"', self.html)
        self.assertIn("Energy bill result", self.html)
        self.assertIn(
            "Battery wear is deliberately excluded from the energy bill result",
            self.html,
        )
        self.assertIn("function loadInsights()", self.js)
        self.assertIn("expenseMoneyFromCents(t.battery_wear_cents)", self.js)
        source = Path(main.__file__).read_text()
        self.assertIn(
            'bill_result = totals["export_revenue_cents"] - totals["import_cost_cents"]',
            source,
        )
        self.assertIn(
            'after_wear = bill_result - totals["battery_wear_cents"]',
            source,
        )

    def test_insights_chart_has_explicit_series_colors_and_mobile_containment(self):
        for key, color in (
            ("pv_kwh", "#ffb54a"),
            ("load_kwh", "#f5f8fb"),
            ("grid_import_kwh", "#57aef0"),
            ("grid_export_kwh", "#65df9a"),
        ):
            self.assertIn(f"{key}:'{color}'", self.js)
        self.assertIn('class="insights-bar insights-bar-${key}"', self.js)
        self.assertRegex(self.css, r"\.insights-page\s*\{[^}]*min-width:\s*0;")
        self.assertRegex(self.css, r"\.insights-chart\s*\{[^}]*max-width:\s*100%;")

    def test_overview_planner_card_uses_current_slot_details(self):
        for element_id in (
            "plannerImportPrice",
            "plannerExportPrice",
            "plannerSlotTime",
            "plannerSlotRemaining",
            "plannerSlotProgress",
            "plannerNetResult",
            "plannerEconomicsDetail",
        ):
            self.assertIn(f'id="{element_id}"', self.html)
        self.assertIn("function currentPlannerSlot(o)", self.js)
        self.assertIn("function renderOverviewPlanner(o)", self.js)
        self.assertIn("Net slot result", self.js)
        self.assertIn("Net slot result", self.js)
        self.assertIn("cashFlow=-Number(slot.slot_cash_cost_cents||0)", self.js)
        self.assertIn("net=cashFlow-wear", self.js)

    def test_installation_wizard_is_available(self):
        self.assertIn('id="setupBackdrop"', self.html)
        self.assertIn('id="setupWizardButton"', self.html)
        self.assertIn("Installation wizard", self.html)
        self.assertIn("api/setup/discovery", self.js)
        self.assertIn("api/setup/complete", self.js)
        self.assertIn('id="setupPriceEntity" class="planner-range"', self.js)
        self.assertIn('id="setupWeatherEntity" class="planner-range"', self.js)
        self.assertIn('class="planner-range" data-setup-kind=', self.js)

    def test_qilowatt_integration_is_configurable_and_explainable(self):
        self.assertIn('id="qilowattMode"', self.html)
        self.assertIn('id="qilowattModeEntity"', self.html)
        self.assertIn('id="qilowattSourceEntity"', self.html)
        self.assertIn('id="qilowattPowerLimitEntity"', self.html)
        self.assertIn('id="qilowattConnectedEntity"', self.html)
        self.assertIn('data-explain="qilowatt"', self.html)
        self.assertIn('id="setupQilowattMode" class="planner-range"', self.js)
        self.assertIn("Mandatory mFRR priority", self.js)
        self.assertNotIn("physical_monitor", self.html)
        self.assertNotIn("physical_monitor", self.js)
        self.assertNotIn("Physical controller", self.html)
        self.assertNotIn("Physical controller", self.js)


    def test_manual_capacity_override_uses_active_value_and_hides_entity(self):
        self.assertIn('id="capacityEntityWrap"', self.html)
        self.assertIn("$('capacityEntityWrap').classList.toggle('hidden',manual)", self.js)
        self.assertIn('const activeCapacity=b.capacity_kwh.value', self.js)
        self.assertIn("Configured by manual override", self.js)
        self.assertNotIn("b.capacity_kwh.detected_value.toFixed(2)", self.js)

    def test_battery_wear_keeps_sub_cent_precision_and_shows_effective_rate(self):
        self.assertIn('function preciseWearFromCents(value)', self.js)
        self.assertIn('preciseWearFromCents(wear)', self.js)
        self.assertIn('id="batteryBaseWear"', self.html)
        self.assertIn('id="batteryWearMultiplier"', self.html)
        self.assertIn('Effective Planner wear', self.html)
        self.assertIn('battery_wear_base_rate_cents_kwh', self.js)
        self.assertIn('battery_wear_rate_cents_kwh', self.js)

if __name__ == "__main__":
    unittest.main()
