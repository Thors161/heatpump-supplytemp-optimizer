# Changelog

## 2.0.0-beta.1

- Convert the advisory package into the manufacturer-independent
  `heatpump_optimizer` custom integration.
- Add local UI source mappings, arbitrary climate/separate-sensor rooms,
  room allowances, model import and reconfiguration.
- Replace internal input-helper bookkeeping with one saved runtime snapshot.
- Preserve quarter-hour held integer advice, 25-minute steps, independent price
  and efficiency requests, room protection, bounded holds and PV fallback.
- Preserve signed-power, DHW-excluded full-cycle COP and matched-quarter power history.
- Add a six-tab dashboard export using registered entity IDs, native graphs
  outside Forecast, per-room shortfalls and fixed ±10°C return-gap axes.
- Keep all equipment commands outside the integration.
