# Energy Pilot 0.2.99

## Frontend files

- `app/static/index.html` — page structure
- `app/static/styles.css` — all visual styles and animations

First-time setup is handled by the in-app installation wizard. It verifies Home
Assistant access, the HACS Nord Pool price source and hourly weather, discovers
energy sensors by their meaning and units, asks for confirmation when matches are
ambiguous, and saves the initial site, solar and connector configuration. The
optional Qilowatt step supports the Qilowatt Home Assistant/MQTT integration as
the external dispatch authority.
- `app/static/app.js` — browser-side behavior and API calls
- `app/main.py` — backend API and static-file serving

Local Home Assistant add-on with live State, Flow and transparent Planner foundations.

Endpoints:
- `GET /api/health`
- `GET /api/config`
- `GET /api/state`
- `GET /api/flow`
- `GET /api/overview`
- `GET /api/price`
- `GET /api/insights`

## Insights and estimated statements

Energy Pilot keeps a durable 15-minute local energy ledger from version 0.2.80
onward. The Insights page can summarize the current week, month, year, a custom
period or the complete retained history.

The **Energy bill result** is export revenue minus import cost. Estimated
battery wear is shown separately because it is an ownership cost estimate, not
an item on the electricity bill. A separate result after wear is available for
long-term owner economics.

## Qilowatt integration

Energy Pilot auto-detects `*_qw_mode`, `*_qw_source`, `*_qw_powerlimit` and
`*_qw_connected` entities regardless of the device prefix.

- **Home Assistant / MQTT dispatch** reads the desired mode, command source and
  power limit exposed by the Qilowatt HACS integration. Active Qilowatt dispatch
  is shown as the current Energy Pilot recommendation while the native plan
  remains available for comparison.
- Fusebox and Kratt mFRR sources receive mandatory external-dispatch priority.

The Qilowatt Home Assistant integration publishes desired state only; inverter
write automations remain on the Qilowatt/Home Assistant side.
