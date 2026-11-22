# Changelog

## 2.0.0-beta.7

- Store Efficiency trim separately from Price and step from the last final shift.
- Use current-quarter prices and check capacity independently of future tariffs.
- Preserve useful observations through routine defrosts and require enough eligible readings before adding trim.
- Keep comfort protection effective with a missing curve reading and split measured heat at quarter boundaries.
- Show decision timing, overnight heating states, limiting rooms and protection details on the dashboard.

## 2.0.0-beta.6

- Pause Efficiency through routine defrosts without clearing its observation
  window. Allow up to 60 seconds after defrost for heating to resume.

## 2.0.0-beta.5

- Keep eligible return-gap observations across ordinary changes in advised shift.
- Add optional shift readback and a configurable confirmation grace period.
  Efficiency holds its addition while the applied shift is unconfirmed.

## 2.0.0-beta.4

- Add shared frequent-defrost protection with an optional defrost input and
  a configurable minimum interval between starts.

## 2.0.0-beta.3

- Use the held advised shift as the reference for Efficiency observations.
  Keep the reported heat-pump target available for display and history.

## 2.0.0-beta.2

- Make Price the starting point for the reduction decision. Efficiency can add
  trim after observing the current operating point, and shared protections
  can lower the combined request.
- Show the decision stages side by side on Advice and plot their totals in history.

## 2.0.0-beta.1

- Convert the advisory package into the manufacturer-independent
  `heatpump_supplytemp_optimizer` custom integration.
- Add local UI source mappings, arbitrary climate/separate-sensor rooms,
  room allowances, model import and reconfiguration.
- Replace internal input-helper bookkeeping with one saved runtime snapshot.
- Preserve quarter-hour held integer advice, 25-minute steps, independent price
  and efficiency requests, room protection, bounded holds and PV fallback.
- Preserve signed-power, DHW-excluded full-cycle COP and matched-quarter power history.
- Add a six-tab dashboard export using registered entity IDs, native graphs
  outside Forecast, per-room shortfalls and fixed ±10°C return-gap axes.
- Keep all equipment commands outside the integration.
