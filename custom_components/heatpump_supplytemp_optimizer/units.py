"""Normalize supported source units; reject unknown units instead of guessing."""
from .schema import numeric


def convert(value, unit, kind):
    value = numeric(value)
    if value is None:
        return None
    unit = str(unit or "").strip().replace("³", "3").replace("ℓ", "L")
    if kind == "temperature":
        if unit in ["°C", "C"]:
            return value
        if unit in ["°F", "F"]:
            return (value-32)*5/9
        if unit == "K":
            return value-273.15
    factors = {
        "temperature_delta": {"": 1., "°C": 1., "C": 1., "K": 1., "°F": 5/9, "F": 5/9},
        "power": {"W": .001, "kW": 1., "MW": 1000.},
        "flow": {"L/min": 1., "l/min": 1., "m3/h": 1000/60, "L/h": 1/60, "l/h": 1/60, "L/s": 60., "l/s": 60.},
        "frequency": {"Hz": 1.},
        "price": {"EUR/kWh": 1., "€/kWh": 1., "EUR/MWh": .001, "€/MWh": .001, "ct/kWh": .01, "cEUR/kWh": .01},
    }
    factor = factors.get(kind, {}).get(unit)
    return value*factor if factor is not None else None

