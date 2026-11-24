"""Compare the same measured space heat in complete, matched quarters."""
from datetime import datetime, timedelta, timezone

from .const import MAX_SAMPLE_GAP
from .model import curve, electricity_cost_rate, predicted_cop

SCENARIOS = ("original", "combined", "actual")


def rates(data, config, state, defrost):
    """Instantaneous kW and EUR/hour. Integrate before calculating ratios."""
    d, p, model = data, config["parameters"], config["model"]
    if not d["cop_ok"] or not model["confirmed"] or d["pv_source"] == "last_valid":
        return None
    required = ("electric_power", "backup_power", "local_outdoor", "curve_outdoor",
                "electricity_price", "pv_power")
    if any(d[k] is None for k in required) or min(d["electric_power"], d["backup_power"]) < 0:
        return None
    other = d["other_power"]
    if other is None:
        if d["pv_power"] > 0:
            return None
        other = 0.  # With no solar, every marginal kWh is imported.

    advised = -state["effective_reduction"]
    shift = d["reported_shift"]
    if shift is None:
        if "reported_shift" in config["inputs"]:
            return None  # A lost readback cannot verify a previously applied shift.
        shift = advised
    if abs(shift) > .01 and (not state["enabled"] or abs(shift-advised) >= .01):
        return None  # Manual or mismatched shifts cannot earn optimizer credit.

    heat, backup, electric = d["heat_power"], d["backup_power"], d["electric_total"]
    original_water = curve(d["curve_outdoor"], p, integer=True)
    water = original_water + shift
    compressor_heat = max(0., heat-backup)
    baseline_power = electric
    powers = dict.fromkeys(SCENARIOS, electric)
    if compressor_heat > 0 and d["electric_power"] > 0 and not defrost:
        original = predicted_cop(d["local_outdoor"], original_water, compressor_heat, model)
        combined = predicted_cop(d["local_outdoor"], water, compressor_heat, model)
        baseline_power = backup + d["electric_power"] * (combined/original)
        powers["combined"] = backup + compressor_heat/combined
        # The original plotted curve is modeled; its daily counterfactual is
        # anchored to actual compressor electricity to cancel model bias.
        powers["original"] = backup + compressor_heat/original

    def cost(kw):
        return electricity_cost_rate(d["electricity_price"], d["pv_power"], other, kw, p["export_discount"])

    result = {"heat": heat, "baseline_kwh": baseline_power, "baseline_eur": cost(baseline_power)}
    for scenario, kw in powers.items():
        result[scenario+"_kwh"] = kw
        result[scenario+"_eur"] = cost(kw)
    return result


class Savings:
    def __init__(self, stored, tz=timezone.utc):
        self.stored = stored
        self.tz = tz
        self.previous = None
        self.previous_time = None
        self.quarter = None
        self.seconds = 0.
        self.energy = {}

    def observe(self, now, sample):
        start = self.previous_time
        if start is not None and now < start:
            return
        continuous = start is not None and now-start <= MAX_SAMPLE_GAP and self.previous is not None
        if continuous:
            while start < now:
                quarter = int(start//900)*900
                end = min(now, quarter+900)
                if quarter != self.quarter:
                    self.quarter, self.seconds, self.energy = quarter, 0., {}
                seconds = end-start
                self.seconds += seconds
                for key, rate in self.previous.items():
                    self.energy[key] = self.energy.get(key, 0.) + rate*seconds/3600
                if end == quarter+900 and self.seconds >= 900-1e-6:
                    self._complete(quarter)
                start = end
        self.previous_time, self.previous = now, sample

    def _complete(self, quarter):
        s, e = self.stored, self.energy
        if quarter <= s.get("last_quarter", -1):
            return
        s["last_quarter"] = quarter
        heat = e["heat"]
        ratios = {}
        for scenario in SCENARIOS:
            power = e[scenario+"_kwh"]
            ratios[scenario+"_cop"] = heat/power if heat > 0 and power > 0 else None
            ratios[scenario+"_unit_cost"] = e[scenario+"_eur"]/heat if heat > 0 else None
        s["ratios"] = ratios
        local = datetime.fromtimestamp(quarter, self.tz)
        date = local.date()
        days = s.setdefault("days", {})
        day = days.setdefault(date.isoformat(), {"start": local.replace(hour=0, minute=0, second=0).timestamp(),
                                                 "quarters": 0})
        day["quarters"] += 1
        for unit in ("kwh", "eur"):
            for side in ("baseline", "actual"):
                key = side+"_"+unit
                day[key] = day.get(key, 0.) + e[key]
            key = "saved_"+unit
            s[key] = s.get(key, 0.) + (e["baseline_"+unit] - e["actual_"+unit])
        cutoff = (date-timedelta(days=6)).isoformat()
        s["days"] = {k:v for k,v in days.items() if k >= cutoff}
