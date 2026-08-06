## 0.3.4

- Canonicalized manual Planner slot timestamps to UTC so commands saved by iOS match plan slots returned in the site timezone.
- Verify persisted override keys after every save and return the actual persisted count.
- Reworded NORMAL recommendations so full-horizon gains are attributed to other optimized slots instead of comparing NORMAL with itself.
- Removed the duplicated no-material-effect preview warning.

## 0.3.3

- Fixed the native Planner manual-command preview returning HTTP 500 because selected slots were referenced outside their payload scope.
- Added shared validation for preview and save requests so both endpoints accept the same actions, timestamps, power and target SOC values.
- Added explicit save errors and regression coverage for preview and override persistence.

## 0.3.2

- Added the configured site name and an explicit API version to `/api/overview` for the native iOS Overview.
- Prepared the existing live state, measured history and Planner forecast payload for the animated Past / Now / Future flow timeline.
- Kept the desktop Overview unchanged and bumped frontend cache-busting metadata for the synchronized release.

## 0.3.1

- Expanded the native Planner API with current prices, forecast quality, projected gain, NORMAL comparison and remaining-today financials.
- Added merged market, optimized-plan and measured-history chart slots for native Today and Tomorrow views.
- Kept Planner calculations in the backend while exposing richer typed data to SwiftUI.

## 0.3.0

- Added typed native Planner API at `/api/planner`.
- Added slot-level revenue, energy cost, wear, net result, and native detail fields.
- Added selected-slot impact alongside full-horizon manual override preview.

# Changelog

## 0.2.99

- Matched mobile Planner price typography to the existing desktop price style: regular weight, muted color and the same interactive dotted underline.
- Showed inline revenue only for revenue-generating SELL, PV SELL and upward-dispatch slots.
- Kept PV/load forecasts, confidence, battery wear, net slot result and other diagnostics behind the existing info icon on mobile.
- Preserved the corrected single mobile time label and the global 20 px `about.svg` info icon.

## 0.2.94

- Reworked only the smallest Planner breakpoint while preserving the existing desktop markup and component styling.
- Reused existing action badges, typography, spacing and button dimensions in the responsive mobile slot layout.
- Replaced text-based information glyphs globally with the existing Energy Pilot about SVG.
- Moved mobile-hidden PV, load, confidence and slot economics into the slot information sheet.

## 0.2.91

- Removed the recently added duplicate page headings from Overview, Planner, Insights and Settings.
- Kept each page’s existing content heading as the single visible title.
- Bumped frontend asset cache-busting so the corrected layout loads immediately.

## 0.2.90

- Added configuration revision metadata to the settings API.
- Exposed the Energy Pilot server version and configuration revision to native clients.
- Increment configuration revision after successful settings changes.

## 0.2.88

- Adjusted CSS styling.

## 0.2.87

- Fixed manual battery capacity override so the configured capacity is shown instead of the underlying sensor value.
- Hid the Capacity entity field while manual capacity override is enabled.
- Preserved sub-cent battery wear in Planner slot displays instead of rounding it to 0.00 €.
- Split Battery economics into base wear, Planner multiplier and effective Planner wear.
- Synchronized runtime, asset and add-on version metadata.

## 0.2.85

- Added matching icons to the horizontal web navigation.
- Added a stronger blue-green native Liquid Glass navigation treatment to the iOS app.
- Made the iOS active navigation lens draggable between sections while preserving tap navigation.

## 0.2.84

- Fixed Insights chart series colors so PV, home, import and export bars never fall back to black.
- Contained the Insights layout and chart scroller so mobile pages no longer exceed the viewport.
- Improved compact Insights spacing and horizontally scrollable period controls on narrow screens.

## 0.2.83

- Made import and export prices explainable on the Overview Planner card and
  every 15-minute Planner slot.
- Added hover, keyboard-focus and tap-friendly price breakdown popovers.
- Added exact per-slot tariff components, including time-based grid fees, VAT,
  margins, regulated charges and effective totals.

## 0.2.82

- Added a background Insights recorder that continues collecting live energy
  measurements while no browser or Energy Pilot page is open.
- Replaced the ambiguous tracked-hours badge with Complete, Partial or Limited
  history status based on actual sample coverage.
- Added exact sampled-versus-expected coverage details to the history status.

## 0.2.81

- Fixed Insights chart series colors so PV, home, import and export bars match
  their legend instead of falling back to black.
- Replaced the ambiguous fractional measured-hours badge with readable tracked
  minutes, hours or days and an exact tracking-start tooltip.

## 0.2.80

- Added a dedicated Insights page with week, month, year, lifetime and custom
  period views.
- Added a persistent local 15-minute energy ledger for production,
  consumption, self-consumption, grid import/export, financial values and
  battery throughput.
- Added estimated monthly energy statements and lifetime totals.
- Defined the energy bill result as export revenue minus import cost.
- Kept estimated battery wear outside the bill result and exposed a separate
  result-after-wear figure for owner economics.
- Added a responsive measured-energy history chart and data-coverage status.
- Updated the Overview month summary to use the same invoice-aligned financial
  convention.

## 0.2.79

- Standardized all Planner financial summaries to the user's perspective:
  income and gains are positive, while import cost and battery wear are
  negative.
- Replaced internal signed-cost values in daily and horizon summaries with
  clear cash-flow labels and euro amounts.
- Updated manual-plan comparisons and remaining-today totals to use the same
  revenue, cost, wear and net-result convention as individual slots.

## 0.2.78

- Expanded the Overview Planner card with current-slot import and export prices,
  the active 15-minute period, time remaining and a live progress indicator.
- Added the current action's net slot result, gross revenue or energy cost and
  battery-wear deduction directly to the Overview.
- Kept the complete card keyboard-accessible and linked to the full Planner
  page.

## 0.2.77

- Replaced the internal signed slot-cost value with user-facing revenue or
  energy-cost wording in euros.
- Added the battery-wear deduction and net slot result to every Planner slot.
- Clarified that revenue is shown before battery wear, while the net result
  includes the configured wear cost.

## 0.2.76

- Fixed HACS Nord Pool VAT detection so VAT-inclusive slot values are converted
  back to the exchange price before import and export tariffs are applied.
- Existing HACS Nord Pool entity IDs ending in an encoded VAT rate, such as
  `_024`, migrate automatically without requiring a Settings change.
- The installation wizard now preserves the detected Nord Pool VAT treatment
  for new installations.

## 0.2.75

- Removed the misleading physical-controller Qilowatt mode, which had no usable
  Home Assistant entities or control path.
- Existing physical-controller selections migrate safely to Disabled.
- Kept the functional Qilowatt Home Assistant/MQTT dispatch integration and its
  automatic entity discovery.
- Added the Energy Pilot application icon to the native iOS project.
- Updated the supplied Live Energy icon set to the refined 1.5 px line weight.

## 0.2.74

- Added Qilowatt integration modes for a physical controller and the Home Assistant/MQTT integration.
- Added prefix-independent discovery for Qilowatt mode, source, power-limit and connection entities.
- Added Qilowatt to the installation wizard, Settings, System Health, diagnostics and the Overview API.
- Kept physical-controller installations monitoring-only so Energy Pilot never competes for inverter control.
- Added external dispatch priority for Qilowatt HA/MQTT commands, including mandatory Fusebox and Kratt mFRR handling.
- Normalized Qilowatt's watt-based power limit to kW in Energy Pilot.

## 0.2.73

- Replaced the Live Energy Solar, Grid, Home, Battery and Inverter symbols with the supplied SVG icon set.
- Kept the existing per-node live colors by rendering the new vector shapes through CSS masks.
- Reused the supplied Home symbol as the Energy Pilot brand mark and browser favicon.

## 0.2.72

- Overview Planner recommendation now uses the currently active 15-minute slot instead of skipping to the next slot.
- Planner’s slot list now begins with the active slot so it can be overridden immediately.
- Added a live elapsed-time progress bar, percentage and remaining minutes to the active slot row.

## 0.2.71

- Exposed the Energy Pilot Web UI directly on port 8099 for native and kiosk clients.
- Kept Home Assistant ingress available while allowing the iOS app to render Energy Pilot without the Home Assistant shell.

## 0.2.70

- Unified every installation-wizard dropdown with the shared Planner dropdown class and styling.
- Applied the same dark select appearance to dependency, weather and sensor-confirmation steps.

## 0.2.69

- Added generalized Home Assistant sensor discovery based on semantic suffixes, units, device classes and friendly names instead of vendor-specific entity prefixes.
- Added automatic discovery for PV, load, grid and battery power, battery SOC and capacity, cycle count, battery temperature and inverter temperature.
- Added a first-run installation wizard with Home Assistant, dependency, sensor, site, solar and review steps.
- Added explicit `Ready`, `Needs confirmation` and `Missing dependency` states with manual sensor selection for ambiguous matches.
- Added compatibility checks that require the HACS Nord Pool sensor’s complete today and tomorrow slot data instead of accepting the limited official integration.
- Preserved existing installations through configuration schema 12 migration and kept the server-side `styles.css` as the visual base.

## 0.2.68

- Moved the frontend out of the Python source into `app/static/index.html`, `styles.css` and `app.js`.
- Added dedicated static-file serving while preserving Home Assistant ingress-compatible relative URLs.
- Added versioned asset URLs to prevent stale CSS and JavaScript after add-on updates.
- Kept the API, UI behavior and Planner logic unchanged.

## 0.2.67

- Changed Planner horizon value explanations from cents to euros.
- Removed card borders throughout the interface and aligned Overview with the shared dark blue surface palette.
- Moved stored energy into the Battery node and removed the redundant four-card statistics block.
- Added extra asymmetric blue-to-green animated energy ribbons to the Live Energy view.

## 0.2.66

- Rebuilt active Live Energy connections as clearly visible multi-layer light fibers instead of subtle single-path highlights.
- Added a broad colored aura, independently shaped moving ribbons, a luminous route core and fast white glints.
- Fixed SVG styling so the animated layers retain their intended gradients instead of inheriting the dim guide-path appearance.
- Increased the active-route contrast while keeping inactive connections quiet and preserving reduced-motion accessibility.

## 0.2.65

- Redesigned Live Energy with a deep plum visual layer and route-driven animated energy ribbons behind the cards.
- Linked ribbon direction, strength and speed to the active live routes while preserving the measured route summary.
- Added a reduced-motion presentation that keeps route state visible without continuous animation.
- Changed the top live-status indicator to an explicit red failure state and restored it automatically after the next successful refresh.

## 0.2.64

- Added `Discard changes` to the fixed Settings action bar; it restores the last saved configuration without submitting the edited values.
- Added `Deselect all` to the fixed Planner selection bar and synchronized the selected-row and accessibility states when clearing the selection.

## 0.2.63

- Fixed the `/api/overview` runtime failure introduced in 0.2.62 by synchronizing the grid-charging reconciliation function signature with the new solar-window policy.
- Added a complete 104-slot Planner integration test so the real end-to-end planning path is exercised instead of only isolated behavior helpers.

## 0.2.62

- Added Value First, Balanced, Resilience First and Custom Planner behavior profiles.
- Added explicit profile controls for pre-solar buffer, forecast confidence, curtailment value and battery-wear importance.
- Anchored flexible battery value to the next reliable solar recharge window instead of only the end of the planning horizon.
- Added dynamic pre-solar SOC targets based on configured reserve, forecast surplus and forecast confidence.
- Allowed forecast-displaced battery energy to be sold in the best profitable slots while preserving the selected pre-solar target.
- Added forecast solar headroom to optimized and NORMAL plan valuation and exposed the complete behavior diagnostics through the Planner API.
- Synchronized manual-plan comparisons with solar-headroom value so an intentional pre-solar discharge is no longer reported as a worse plan merely because its final SOC is lower.
- Replaced the legacy Strategy status with the active Planner behavior and its next pre-solar target.

## 0.2.61

- Added real 15-minute PV, home load, battery SOC and grid import/export history to the Today graph.
- Backfilled elapsed-day measurements from Home Assistant Recorder with time-weighted slot averages.
- Added Energy Pilot's own persistent 30-day measurement history as a fallback when Recorder data is unavailable.
- Distinguished measured history from future forecasts in the chart legend and slot details.
- Added history source, slot coverage and power-balance derivation diagnostics to the graph tooltip.

## 0.2.60

- Made the Planner outlook graph fill the complete available card width without horizontal scrolling.
- Changed the Today range to a complete local 00:00–24:00 timeline with a live current-time marker.
- Added full-day market slots to the chart data while keeping future Planner decisions authoritative.
- Added matching series-color indicators to every slot-tooltip metric.
- Renamed the chart eyebrow from 15-minute outlook to Outlook graph.

## 0.2.59

- Added a dedicated Planner page between Overview and Settings.
- Changed the Overview Planner card to open the new page instead of the large details modal.
- Added an interactive 15-minute chart for import price, export price, PV forecast, load forecast and battery SOC.
- Added Planner action bands, current-time marker, per-slot hover details and toggleable chart series.
- Added period choices for the next 12 hours, today, tomorrow and the complete planning horizon.
- Moved the complete upcoming plan, financial summary, pagination and manual slot editing to the Planner page.

## 0.2.58

- Added a reusable global loading-indicator component with an animated dual-color spinner.
- Added the loader to the manual Planner calculation state.
- Improved loading-state text and exposed the calculation state with `aria-busy`.
- Added reduced-motion handling for the shared loader animation.

## 0.2.57

- Changed manual-command preview to compare the complete planning horizon instead of only today's result.
- Included import cost, export revenue, battery wear and terminal stored-energy value in the same comparison.
- Added stored-energy value change to the preview so SOC trade-offs are explicit.
- Synchronized Planner cash flow, wear and optimization-gain summaries with applied manual commands.
- Planner optimization issues are now flagged only when a manual plan materially improves the full-horizon result.

## 0.2.56

- Kept import and export prices on one line after adding negative-price highlighting.
- Added spaces around the Planner slot time-range separator (`17:30 – 17:45`).

## 0.2.55

- Prioritized negative export-price protection before battery charging classification.
- LIMIT EXPORT now remains active while solar finishes charging the battery and prevents the remaining unprofitable export.
- Changed displayed times to the 24-hour clock.
- Highlighted negative import and export prices in `#E29090` in the Planner slot list.

## 0.2.54

- Added a live financial preview before applying manual Planner commands.
- Compares Planner and manual projected result, revenue, import cost, battery wear and final SOC.
- Flags a material manual improvement as a Planner optimization issue instead of presenting it as a success.
- Preview recalculates when action, power or target SOC changes and never saves implicitly.

## 0.2.53

- Added a fixed Planner action dock whenever one or more plan slots are selected.
- The dock shows the current selection count and opens the existing manual-command editor.
- The dock disappears when the selection is cleared or the Planner details are closed.

## 0.2.52

- Removed stardust and pulsing from Live Energy flow markers.
- Kept a single steady-size moving marker to show flow direction.
- Removed visible borders from Solar, Grid, Home, Battery and Inverter cards.

## 0.2.51

- Moved inverter-side route anchors to the matching upper and lower card corners.
- Matched the steeper diagonal geometry from the annotated reference.
- Made stardust trails larger, brighter, more widely spaced and independently twinkling.

## 0.2.50

- Aligned every outer connection to the vertical center of its card's inner edge.
- Added a restrained stardust trail with a pulsing core and two fading particles.
- Preserved route direction, component colors and active-link glow.

## 0.2.49

- Rebuilt Live Energy around a true central inverter topology.
- Moved Solar and Grid to the left and Home and Battery to the right.
- Replaced curved and overlapping route geometry with four straight diagonal component links.
- Made the inverter use the same full-size card design as the other energy nodes.
- Preserved route colors, glow and direction-aware animation across the new links.

## 0.2.48

- Preferred the dedicated Deye inverter temperature entity over generic DC temperature candidates.

## 0.2.47

- Added a central inverter hub to the Live Energy topology.
- Added automatic inverter and battery temperature sensor discovery with manual overrides.
- Added Normal, Warm and High temperature classifications to the live view.
- Added both temperature sensors to connector diagnostics.

## 0.2.46

- Fixed charging-route allocation so remaining grid import is no longer attributed to solar.
- Added explicit live-meter balance diagnostics instead of hiding unmatched power in a physical route.
- Added the balance difference to Active routes details with a timestamp/measurement-point explanation.

## 0.2.45

- Added configurable minimum sell profit per kWh.
- Added configurable minimum total planning-horizon gain.
- Battery export below the required net margin is blocked.
- Planner falls back to NORMAL when the complete plan does not clear the horizon threshold.

## 0.2.44

- Added battery-charge reconciliation for momentary false-zero Deye readings.
- Uses the live power balance only when it clearly indicates at least 50 W of charging.
- Exposes measured and inferred battery power separately in diagnostics.

## 0.2.43

- Removed the duplicate Updated status chip from the footer.
- Made the header Live status open the existing last-update diagnostics.
- Kept exact and relative refresh timestamps inside the Live detail view.

## 0.2.42

- Included NORMAL battery wear in the Planner v3 comparison.
- Added a NORMAL fallback so automatic optimization cannot choose a lower-value plan.
- Moved System Health into centered, horizontal footer status chips.

## 0.2.41

- Added an Earnings & savings overview card inspired by benefit dashboards while using Energy Pilot's own planner data.
- Added This month and Planning horizon views with measured and projected value breakdowns.
- Added persistent local month-to-date import, export, battery-throughput, wear and negative-price-protection tracking.
- Added transparent measured/projected labeling and a visible first-tracked timestamp.
- Kept unsupported flexibility-market revenue out of the totals.

## 0.2.40

- Made complete Planner slot rows selectable without checkboxes and added a clear selected-row state.
- Added command-aware manual defaults for BUY and SELL power and target SOC.
- Added stored-energy value change to today's projected economic result.
- Clarified that the daily figures cover the remaining plan for today.

## 0.2.39

- Added automatic battery cycle-count sensor discovery.
- Prioritizes the configured entity, then `sensor.deye_total_battery_life_cycles`, then compatible numeric battery-cycle sensors.
- Added configured, automatic Deye, general auto-discovered and unavailable connector modes.
- Exposed the actual cycle-count source and discovery candidates in diagnostics.

## 0.2.38

- Added today's planned export revenue, import cost, battery wear and net result.
- Added multi-select checkboxes to upcoming plan slots.
- Added persistent manual overrides for NORMAL, BUY, SELL, SAVE BATTERY, LIMIT EXPORT and PV SELL.
- Added optional shared power and target-SOC values for selected slots.
- Added `M` badges and removal of selected manual commands.
- Manual overrides now recalculate subsequent SOC, grid flow, wear and financial forecast.

## 0.2.37

- Added battery-system cost, allowed full cycles and used-cycle entity settings.
- Added live used-cycle and remaining-cycle diagnostics.
- Added automatic wear cost based on system cost, usable capacity and remaining cycle life.
- Planner v3 now uses the automatic wear rate when all inputs are available and retains the manual rate as fallback.
- Migrated configuration schema to version 9.

## 0.2.36

- Preserved measured grid import as a visible Grid → Home route instead of proportionally shrinking it below the display threshold.
- Allocated only the remaining home demand between PV and battery sources.
- Prevented the same grid-import power from being assigned to both Home and Battery.

## 0.2.35

- Added a second global optimization pass that forbids grid charging during forecast PV surplus.
- Midday grid charging is selected only when it improves full-horizon value by at least 2 cents after losses and configured wear.
- Added the unrestricted-versus-PV-only advantage and decision threshold to Planner diagnostics.
- Clarified that `BUY` uses PV for forecast home demand first and assigns grid energy to remaining demand plus the explicit battery charge.

## 0.2.34

- Removed grid charging introduced only by discrete SOC optimization steps.
- Grid charging is now allowed only when later avoided import or export value covers the current import price, round-trip loss, battery wear and a minimum benefit margin.
- `BUY` now means exactly that the plan intentionally charges from the grid; PV-only charging remains `NORMAL`.
- Added planned power and target SOC below `BUY` and `SELL` commands.
- Added per-slot grid-charge energy and battery wear-rate diagnostics.
- Increased non-zero per-slot wear precision and labels zero-rate wear as disabled.

## 0.2.33

- Increased active Live Energy node borders to 3 px.
- Removed the duplicate live-flow sentence from the Live Energy header.
- Simplified the top status badge to `Live`.
- Centered active-route tags and added 24 px spacing below the energy visual.

## 0.2.32

- Added a power-balance fallback for a missing or false zero home-load reading.
- Home load is derived as `PV + battery + grid` using the configured Deye sign convention.
- Derived load readings remain healthy and expose their source, measured value and calculation in diagnostics.
- Planner learning and live routes now consume the reconciled home-load value.

## 0.2.31

- Added Planner v3 global optimization across the complete configured price horizon.
- Added battery efficiency, charge/discharge power, grid limits, reserve, wear cost and terminal stored-energy value to the objective.
- Added a comparable NORMAL self-consumption baseline and projected horizon value improvement.
- Added per-slot cash cost, wear cost and horizon-aware action explanations.
- Preserved Planner v2.1 learned PV calibration and load profiles as Planner v3 forecast inputs.

## 0.2.30

- Added Planner v2.1 persistent learning in `/config`.
- Added five-minute load samples and weekday/weekend 15-minute historical load profiles.
- Added hour-specific PV calibration learned from forecast-versus-actual production.
- Added per-slot forecast confidence and visible PV calibration factors.
- Added daily PV, load, import, export and SOC-range summaries to Upcoming Plan.
- Planner automatically falls back to learned load history once each slot has enough samples.

## 0.2.29

- Added Weather-adjusted PV Forecast v1 without an Energy Dashboard dependency.
- Added automatic discovery of Home Assistant weather entities and hourly forecasts.
- Added a 15-minute solar geometry model using Home Assistant latitude/longitude.
- Added cloud-cover, weather-condition and precipitation attenuation.
- Added solar array settings with 25.48 kWp, 22° tilt and 180° azimuth defaults.
- Planner v2 now uses the weather-adjusted PV curve for SOC and action simulation.
- Migrated configuration to schema version 8.

## 0.2.28

- Fixed SELL consuming energy that is still needed for forecast home demand later in the planning horizon.
- SELL power is now limited to energy remaining after battery reserve and forecast home consumption.
- Corrected SELL simulation so battery discharge covers home demand first and only the remaining planned power is counted as grid export.
- SAVE BATTERY now appears in the sequential plan only when the configured battery reserve is actually reached.
- Missing PV forecast values are displayed as unavailable instead of the misleading `0 kW`; a real zero forecast remains `0 kW`.

## 0.2.27

- Upcoming Plan now exposes every slot in the configured planning horizon.
- Added 10, 25, 50, 100 and All page-size choices.
- Added first, previous, numbered, next and last-page navigation.
- Added visible slot-range and total counters.
- Widened the Planner detail view and added responsive pagination for narrow screens.

## 0.2.26

- Fixed Planner v2 preserving the entire battery whenever a later export price was higher.
- Save Battery now activates only when usable energy cannot cover both home demand until the better slot and one planned sell window.
- Normal mode now lets a well-charged battery cover home consumption while retaining sufficient energy for later opportunities.
- Added precise Forecast.Solar diagnostics that distinguish an unselected Energy Dashboard forecast source from a missing provider.

## 0.2.25

- Added automatic Home Assistant Energy Dashboard solar-forecast discovery through the Supervisor WebSocket API.
- Planner v2 now consumes Forecast.Solar `wh_hours` data without requiring a forecast entity.
- Added multi-provider forecast aggregation and conversion from hourly energy to slot-average power.
- Added a 30-minute successful-result cache and a five-minute error backoff.
- Added a visible Connected/Unavailable forecast status in Settings.
- Kept the manual timestamped PV entity only as an optional fallback.
- Added the WebSocket client runtime dependency and synchronized the startup version log.

## 0.2.24

- Added Planner v2 with sequential 15-minute SOC simulation across the full price horizon.
- SELL now uses look-ahead and waits unless the slot is within 8% of the best remaining export price.
- BUY requires a real PV forecast and a projected six-hour energy deficit; it remains disabled when PV forecast data is unavailable.
- Added generic timestamped Home Assistant PV/load forecast connectors and a live-load fallback.
- Added battery charge/discharge power, round-trip efficiency and degradation-cost settings.
- Upcoming slots now show projected SOC, PV, load and the Planner v2 data-quality mode.
- Migrated configuration safely to schema version 7.

## 0.2.23

- Standardized Planner actions to Normal, Buy, Sell, Save Battery, Limit Export and PV Sell.
- Added one shared price-and-flexibility classifier for the current recommendation and future slots.
- Added per-slot action reasons and import/export percentiles to the overview API.
- Added an information button after every displayed Planner action with its operating description and slot-specific reason.
- PV Sell is recommended only when live PV availability supports the decision; future price-only slots do not assume solar production.

## 0.2.22

- Added the effective import price beside the export price in Planner details.
- Added independent, expandable calculations for both current prices.
- Added an Upcoming plan table for the next eight 15-minute market slots.
- Each slot shows effective import/export prices and a transparent simulated price-signal action.
- Expanded Planner details on desktop and added scrolling and responsive slot rows for smaller screens.

## 0.2.21

- Clarified the two VAT settings as separate input-price and calculated-import-price stages.
- Added concise descriptions explaining the effect of each VAT option.
- Added a fixed bottom-centre save bar that appears as soon as any setting changes.
- The save bar disappears after a successful save and reports validation failures in place.

## 0.2.20

- Fixed the Planner using a lagging sensor state instead of the active 15-minute market slot.
- Missing slot end times are now inferred from the next slot or the configured slot duration.
- Added the active slot, selected source and reported sensor-state price to Planner price diagnostics.
- Current price, effective tariffs and Planner reasoning now share the same active market slot.

## 0.2.19

- Added a live price information button to the Planner recommendation details.
- Added an expandable calculation showing every component used for the effective Planner price.
- The calculation identifies whether Planner is using import or export pricing and shows the applicable VAT rule.
- All displayed values come from the same runtime price payload used by the recommendation.

## 0.2.18

- Fixed Planner export price by always including VAT on the export balancing-cost component.
- Removed the optional export-balancing VAT toggle because this is a tariff rule.
- Aligned current price, horizon slots, Planner reasoning and `/api/price` export calculations.

## 0.2.17

- Split balancing costs into separate import and export components.
- Export price now deducts the export balancing cost.
- Export VAT is calculated only on the balancing-cost component.
- Import VAT continues to apply to the complete import subtotal.
- Added separate settings and price-breakdown fields for both directions.
- Migrated schema version 5 tariff settings safely to schema version 6.

## 0.2.16

- Added a complete import and export tariff model on top of Nord Pool spot prices.
- Added configurable import/export margins, grid day/night/weekend rates, excise and regulated per-kWh fees.
- Added VAT-aware import pricing and optional renewable-energy support for export.
- Added an explicit option for spot-price sensors whose source value already includes VAT.
- Added effective import/export prices and a transparent component breakdown to `/api/price`.
- Planner price ranking now uses the effective price relevant to the selected strategy.
- Migrated existing configuration safely to schema version 5.

## 0.2.15

- Rebuilt the route background as three clean, permanently visible horizontal lanes.
- Solar branches use the upper lane, Grid ↔ Home uses the centre lane and Battery branches use the lower lane.
- Removed overlapping full-route curves while keeping the inactive network visible.
- Preserved gradients, glow, particles and active node borders on measured routes above the 20 W threshold.
- Synchronized all release metadata for Home Assistant's local add-on update flow.
- Added Home Assistant container metadata labels so a local rebuild updates the installed version shown by Supervisor.

## 0.2.14

- Separated simultaneous Live Energy flows into distinct visual lanes.
- Grid → Home now uses its own straight horizontal route.
- Battery and solar routes use offset ports and independent rounded elbows.
- Preserved route gradients, glow, particles and active node borders.
- Synchronized the Supervisor, runtime, README and UI versions so local installs expose Home Assistant's Update action after an app-store refresh.
- Switched release metadata to plain semantic versioning so Supervisor can compare local releases reliably.

## 0.2.13-dev

- Rebuilt Live Energy routes as a symmetric orthogonal network.
- Added smooth, consistent 90-degree bends into the horizontal centre line.
- Removed diagonal route segments and irregular overlapping curves.
- Preserved active node borders, route gradients, glow and flow animations.
- Synchronized add-on, runtime, README and embedded UI version metadata.

## 0.2.12-dev

- Active Live Energy cards now use a full-strength border matching each node icon colour.
- Inactive cards no longer show a visible border.
- Active routes now use source-to-destination colour gradients with a restrained glow.
- Inactive route geometry remains visible as a low-contrast network.
- Updated the embedded UI version label.

## 0.2.11-dev
- Added an explainability layer for planner recommendation, confidence, autonomy, stored energy, capacity, SOC, active routes and update time.
- Expanded System Health modals with human-readable connector names, entity IDs, freshness and per-connector status.
- Expanded Export Value details with current price, best horizon price, planning horizon and current recommendation reason.
- A valid zero-watt PV reading no longer degrades critical system health merely because the sensor stops updating overnight.
- Added keyboard-accessible explanatory cards and mobile-friendly diagnostic detail rows.

## 0.2.10-dev

- Added tappable explanations for State engine, Connectors and Strategy statuses.
- Added an accessible detail modal that becomes a mobile bottom sheet on narrow screens.
- Explanations include the current live status, detected connector issues and the planner's current action and reason.
- Added keyboard Escape, backdrop and close-button dismissal behavior.

## 0.2.8-dev

- Fixed a frontend exception caused by missing market-price DOM elements.
- Live routes, active-route count, system health and updated time now complete on every successful overview refresh.
- Market diagnostics remain optional and no longer block the live-energy UI.
- Refresh failures are now logged and shown in the live-status badge instead of being silently ignored.

## 0.2.7-dev
- Fixed the startup race that left live-route chips, active-route count, health and updated time stuck in their loading state.
- Added a live route renderer with animated active paths and measured flow values.
- Added Grid → Battery visualization and normalized route labels to Solar, Home, Battery and Grid.
- Active routes now count only flows at or above the 20 W display threshold.
- Added a visible fallback state when live route refresh fails.

## 0.2.6-dev
- Added automatic Nord Pool entity discovery when the configured sensor is missing or incompatible.
- Added true automatic unit detection for EUR/kWh, c/kWh and EUR/MWh, including unitless fallback heuristics.
- Added visible price-source diagnostics to Overview and expanded `/api/price` diagnostics.
- Migrated price configuration to schema version 4 and synchronized all package version metadata.
- Reordered and corrected the changelog so it matches the shipped implementation.

## 0.2.5-dev
- Added attribute fallbacks for `current_price`, `current`, `price`, and `value`.
- Added active-slot fallback for the current 15-minute price.
- Added price source and source-unit fields to `/api/price`.

## 0.2.4-dev
- Reconciled asynchronous power meters so grid and battery routes remain visible.
- Fixed home autonomy and source-share calculations during simultaneous import/discharge.
- Added measurement-specific freshness windows for capacity, SOC and live power.
- Replaced generic OBSERVE fallback with actionable recommendations and data-driven confidence.
- Improved connector health, active-route count and updated-time states.

## 0.2.3-dev
- Added Nord Pool price connector with VAT-aware c/kWh normalization.
- Added `/api/price` and horizon price ranking.
- Made export-value recommendations react to current price percentile.
- Added live market price and rank to Overview plus price settings.

## 0.2.2-dev
- Added home autonomy and source contribution metrics.
- Planner recommendations now react to live flow state.
- Added planner tone states and stronger Overview hierarchy.

## 0.2.1-dev
- Rebuilt Overview around a route-aware live energy stage.
- Added animated particles only on interpreted active routes.
- Added transparent Planner foundation and `/api/overview`.
- Added strategy, confidence, execution mode and live freshness indicators.
- Improved visual hierarchy, responsive behavior and settings layout.
- Preserved the `energy_pilot` add-on identity and synchronized version metadata.

## 0.2.0-dev
- Renamed add-on slug and folder to `energy_pilot`.
- Added live State and Energy Flow overview.
