# Heat Pump Supply Temperature Optimizer

A configurable Home Assistant integration that advises how far to lower a heat
pump's weather-compensated supply temperature. It starts with a price request,
lets Efficiency add trim, then applies shared comfort, capacity, hydraulic and timing
limits. **It does not send commands to the heat pump, pumps, TRVs or relays.**

The integration is independent of heat-pump manufacturer. Source entities,
heating-mode values, curve rounding, equipment limits, room list and model
coefficients are configured locally. Repository updates never contain a user's
room names, entity mappings, history or calibration profile.

## Concept and how it works

A weather-compensated heating curve sets the baseline supply-water temperature
from the outdoor temperature. Cooler water can make a heat pump more efficient,
provided the heating system can still deliver enough heat to keep rooms comfortable.
The optimizer uses the configured curve as its starting point and recommends a
downward adjustment when conditions allow.

1. **Electricity cost.** With a calibrated heat-demand and efficiency model,
   the optional price method uses tariffs, weather and any configured solar
   forecast to compare the estimated cost of producing heat now with the typical
   cost over the next six hours. It can request a reduction when heat is relatively
   expensive now.
2. **Efficiency.** Once the current reduction is in effect, Efficiency checks
   room shortfall and the return-temperature gap. It can add a degree above the
   current reduction when the readings support it. It can withdraw its own trim
   while retaining the Price request.
3. **Final advice.** Every quarter hour, the optimizer applies room comfort,
   heat-pump capacity, hydraulic readiness and timing checks to the combined
   request. Changes normally happen in 1°C steps and respect the configured
   minimum target. Home Assistant shows the **Final reduction**, **Advised target**
   and the reason for the advice. Any equipment control is handled by an external
   automation.

## Requirements and status

- Home Assistant **2026.11.0 or newer**; this beta is tested with 2026.11.0 and Python 3.14.
- A calibrated heat-output power sensor and a dedicated heat-pump electricity sensor.
- Primary flow and both return-temperature measurements for efficiency trim.
- At least one room with a setpoint and actual temperature.
- Price optimization additionally needs a configured demand/COP model, current
  tariff, price forecast, weather forecast and household import power.
- PV and other heating sources are optional. If PV is configured, its forecast
  must be available to complete price forecasts.
- The generated **Forecast** tab uses the existing ApexCharts Card. Every graph
  outside Forecast uses native HA history/statistics cards.

This is **2.0.0-beta.2**.

## Installation

### Add the repository manually in HACS

1. In HACS, add `https://github.com/Thors161/heatpump-supplytemp-optimizer` under
   **Custom repositories** and select **Integration** as the category.
2. Download the integration and restart Home Assistant.
3. Open **Settings → Devices & services → Add integration** and search for
   **Heat Pump Supply Temperature Optimizer**.
4. Configure source entities and rooms, or choose **Import local profile**.

### Copy the integration files manually

1. Copy `custom_components/heatpump_supplytemp_optimizer/` to the HA configuration directory,
   keeping that directory structure. No package YAML or custom Jinja files are needed.
2. Restart Home Assistant, then go to **Settings → Devices & services → Add integration**
   and search for **Heat Pump Supply Temperature Optimizer**.
3. Leave equipment-command automations unchanged while reviewing the advice.

The integration starts with its master and price switches **off** on first setup.
Later choices are restored across restart. There is no direct equipment-control
service, MQTT publisher or hidden actuator path in this integration.

If you installed an earlier beta under the `heatpump_optimizer` domain, set up a
new integration entry after installing `heatpump_supplytemp_optimizer`. Update
any automations and dashboards that reference the earlier entry's entities.

## Configuration

Setup groups inputs into Heat pump, Hydraulics, Environment and Electricity.
Optional selectors may be left empty. Missing configured inputs remain visible
in diagnostic reasons; startup sensor availability is retried automatically.
On update, Home Assistant removes the retired secondary-flow mapping from saved
integration entries. Remove that mapping from older profiles before importing them.

| Group | Inputs |
|---|---|
| Heat pump | Heat output, electrical power, optional backup-heater power, supply, primary return, primary flow, compressor frequency, heating-mode state, optional actual target |
| Hydraulics | Secondary return temperature |
| Environment | Separate curve and local outside temperatures; optional weather, ventilation heat-pump electricity and stove temperature |
| Electricity | Optional household import/export power, PV power and current tariff; required when their price-forecast function is used |

Supported units are checked explicitly:

- Temperature: °C, °F or K, normalized to °C.
- Power: W, kW or MW, normalized to kW. Signed thermal power is preserved.
- Flow: L/min, m³/h, L/h or L/s, normalized to L/min.
- Electricity price: EUR/kWh, €/kWh, EUR/MWh, €/MWh or ct/kWh.
- Compressor frequency: Hz.

Unknown units are not guessed. Climate temperatures use the climate unit, or
HA's configured temperature unit when the climate state omits it.

Configure the cold and warm outside/water points, minimum target, maximum
reduction and capacity threshold. Curve rounding can be `ceil`, `nearest` or
`none` in Advanced parameters. The output reduction remains an integer and the
advised target reflects the achievable integer shift without crossing the floor.
The curve uses its dedicated outside sensor; forecasts and COP use the local
outside sensor. No offset between those two probes is added to weather forecasts.

Configure explicit values meaning space heating and DHW, for example `Room` and
`Tank`. An optional fallback assumes DHW if the outlet exceeds 40°C **only when
the explicit mode is unknown**. It is off by default.

### Rooms

Use the integration's **Configure** options to add, edit or remove any number of
rooms. Each room has a stable internal ID, display name, inclusion switch,
extra allowed shortfall and maximum shortfall rise. Select either:

- one climate entity with `current_temperature` and `temperature` attributes; or
- two separate actual-temperature and setpoint entities.

The room count is the length of this list. There is no separate count to keep in
sync. Names can change without changing a room's identity or recorded history.

`adjusted shortfall = max(0, setpoint − actual − allowance)`

Allowances range from 0–2°C in 0.1°C steps and apply all day. The optional
maximum shortfall rise ranges from 0–10°C/h in 0.1°C/h steps. With the default
of 0, the adjusted shortfall passes through as calculated. At 1°C/h it can
rise by up to 0.5°C in 30 minutes, whether a setpoint increased or a room cooled.
Decreases apply immediately. The last limited value is restored after restart
without counting downtime toward allowed growth.

The largest limited shortfall among included rooms feeds the efficiency checks,
shortfall trend and shared comfort protection. The demand model still uses actual
room temperatures. The Comfort dashboard shows both values for each room.
An off climate room contributes zero shortfall. A missing included room invalidates
comfort/forecast inputs; an excluded room does not veto comfort checks. Available
actual temperatures of all configured rooms remain eligible for the indoor mean.

### Model calibration

The Model page accepts a JSON object. Included defaults are **uncalibrated
engineering examples**, not a model fitted to a typical home. Price reduction
cannot be enabled until `confirmed` is true. Efficiency-only operation does not
need a confirmed price model.

House heat demand is:

```
max(0, heat_loss * (indoor - outside)
       - solar_gain * PV_kW - stove_gain * stove_proxy - background_gain)
```

COP is an effective-Carnot estimate with configurable `cop_b0`…`cop_b3` and
training bounds. The model's load limits and equipment comparison load can be
configured. Extrapolation is flagged. Optional ventilation heat is allocated
once, to reduce the heat pump's share; stove gains affect total house demand.
Wind is optional forecast metadata; the current model does not add a wind/rain
term unless a future separately validated model implements it.

Existing local calibration can be imported; do not reuse another home's fitted
coefficients or call the forecast an equal-comfort savings measurement.

### Forecast adapters

**Zonneplan**: the selected tariff entity's `forecast` attribute has intervals
with `start_date`, `end_date` and `price_tax_included.amount`; the amount is divided
by 10,000,000 to obtain EUR/kWh. The numeric current tariff is converted using its
reported unit independently. Quarter-hour and hourly covering intervals both work.

**Generic prices**: select `generic` and configure the attribute/key names in
Advanced parameters. Default format (prices already EUR/kWh):

```json
[{"start":"2026-01-15T12:00:00+00:00", "end":"2026-01-15T12:15:00+00:00", "price":0.30}]
```

**Forecast.Solar**: select one forecast sensor from each installation. Its config
entry is resolved through HA's entity registry and queried via the read-only
`forecast_solar.get_forecast` action. Responses use hourly `wh_period` values.

**Generic solar**: select `generic` and select source entities whose `forecast`
attribute contains hourly power intervals:

```json
[{"start":"2026-01-15T12:00:00+00:00", "end":"2026-01-15T13:00:00+00:00", "power_kw":1.5}]
```

For generic solar, provide explicit zero-power intervals for known night hours;
a completely empty provider is incomplete. Current PV has a ten-minute fallback
using its last real reading and original timestamp. At night an unavailable PV
reading is treated as zero without refreshing the cache timestamp.

Import/export household power is used to estimate background load and PV
available to the heat pump. It is not substituted for dedicated compressor
electricity in COP. Export value is import tariff minus the configured discount.
Negative prices and export opportunity costs are retained.

## Decisions and restart handling

- Normal decisions run at :00/:15/:30/:45. Only the three enable switches cause
  immediate decisions. Source/forecast/number updates do not move the held output.
- Reductions progress by one degree, with a minimum 25 minutes between steps.
- The combined request starts with the price reduction and adds any Efficiency
  trim. A falling Price request carries over only the remaining Efficiency
  addition. A rising Price request can absorb an existing addition, leaving
  the combined request at the larger of its previous total and the new Price request.
  The forecast still compares the original heating curve with an Efficiency
  curve. The Efficiency curve uses only the addition from the last decision.
- Efficiency uses eligible observations at the current advised reduction. When
  the actual heat-pump target is configured, it must match the expected target
  within 0.5°C for gap observations to count. The eligible gap window starts
  afresh after a reduction change, a compressor pause or an unconfirmed target.
- Shared comfort protection requests zero after the shortfall used by the optimizer is ≥0.5°C for
  30 minutes; release needs ten minutes below that level. Missing rooms are
  also explicitly rejected by the decision rules.
- Efficiency backs off its own trim when the current return gap is at or below its low
  limit. More trim requires the lowest eligible gap over 30 minutes to reach
  the high limit.
- Hydraulic hold is bounded to 30 minutes and is not extended by repeated checks.
- Settings, method levels, held targets, change times, cache timestamps and matched
  pre-quarter predictions are stored in one integration-owned HA storage record.
- There are no extra `input_*` helpers or legacy cumulative-COP statistics.
- Observations use bounded in-memory windows, with selected samples restored.
  Power integration never bridges an outage; restart/DHW partial quarters are
  excluded. Observation/comfort timers rebuild after restart, as the package did.

Measured COP is the ratio of paired signed thermal/electrical power integrals
over 30 minutes, with 95% coverage and at least 0.025 kWh electricity. It includes
negative defrost heat and compressor pauses, and excludes DHW. Compressor
frequency gates efficiency observations, not measured full-cycle COP.

## Dashboard

Press the integration's **Export dashboard** button. It writes
`heatpump_supplytemp_optimizer_dashboard_<entry_id>.yaml` inside the HA configuration folder.
The button's attributes show the exact filename. Create a dedicated dashboard,
open its raw configuration editor and paste the file contents. The exported JSON
is valid YAML. The export does not overwrite an existing dashboard.

Tabs: **Advice · Price · Efficiency · Comfort · Forecast · History · Settings**.
Advice shows the shared protection states. Comfort shows per-room and maximum
shortfalls alongside the comfort protection status.
The Advice tab shows Price at the last decision, the Efficiency addition, the
combined request, the reduction after protections and the final advice. The
four totals share a history chart. Heating-target tiles show the original,
advised and actual heat-pump targets when their readings are available.
The exporter resolves actual registered entity IDs, including user renames and
multiple instances. Re-export after adding/removing rooms or renaming entities.
Large room graphs are split into groups of eight for native HA card limits.
Return gap is fixed at −10°C to +10°C. Forecast charts retain ApexCharts; all
other graphs are native. Graph history starts when the entities are installed.

## Migrating an existing package

1. Keep the old package files and configuration as a rollback. The integration
   does not uninstall the package or alter an existing command automation.
2. Export a **private** profile outside this repository:
   `python tools/export_package_profile.py --package-root /path/to/package --output /private/profile.json`
   An optional `--state-file` accepts a local JSON map of helper entity IDs to
   values, so current thresholds and allowances can be imported.
3. Install this integration and choose **Import local profile**. The existing
   mappings, curve and fitted coefficients stay local. The integration's source
   repository never includes this generated file.
4. Compare the new advisory sensors. New entities use the generic
   `heatpump_supplytemp_optimizer` domain. Update any references to old helper IDs.
5. When deliberately switching an external command automation, use the actual
   registered **Final reduction** sensor and **Advisor enabled** switch. A positive
   reduction still means lowering temperature; the MQTT topic/sign is equipment
   specific and remains outside this integration.

The new integration is advisory. Installing it does not itself migrate, enable or
retarget the existing equipment-control automation. Removing the integration and
restoring the old files/automation references provides rollback.

## Development and GitHub releases

```bash
python3.14 -m venv .venv
.venv/bin/pip install -r requirements-test.txt
.venv/bin/python -m pytest
```

For offline testing, `pip download` the requirements on a matching platform,
then install with `--no-index --find-links /path/to/wheels`. A source-only Python
dependency may also need setuptools and wheel available during the offline build.

If you fork or transfer the repository, run `python tools/prepare_repository.py YOUR_GITHUB_USER`
(or use the organization name with `--maintainer YOUR_GITHUB_USER`) to update the
manifest's repository URLs and code owners. Before tagging a GitHub release, run
`python tools/check_repository.py --release`, then the test suite. GitHub Actions
uses Hassfest to validate the integration and HACS to validate the custom repository.

The repository intentionally omits private profiles, HA `.storage`, source data,
reports, logs and user-specific calibration. Diagnostics expose input-role names,
counts and coarse state only, not raw room names or entity IDs.

License: MIT.
