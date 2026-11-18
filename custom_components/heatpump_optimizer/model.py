"""Pure heat-demand, COP and incremental bill calculations."""
from math import ceil, floor
from .schema import numeric


def clamp(value, lo, hi):
    return max(lo, min(hi, value))


def curve(outdoor, p, *, integer=False):
    fraction = (outdoor-p["curve_cold_outdoor"])/(p["curve_warm_outdoor"]-p["curve_cold_outdoor"])
    water = p["curve_cold_water"] + clamp(fraction, 0, 1)*(p["curve_warm_water"]-p["curve_cold_water"])
    if integer and p["curve_rounding"] == "ceil":
        return ceil(round(water, 6))
    if integer and p["curve_rounding"] == "nearest":
        return floor(round(water, 6)+.5)
    return water


def predicted_cop(outdoor, water, heat_kw, m):
    q = clamp(heat_kw, m["cop_q_min"], m["cop_q_max"])
    a, b = (outdoor-5)/10, (q-3)/3
    eta = clamp(m["cop_b0"]+m["cop_b1"]*a+m["cop_b2"]*b+m["cop_b3"]*b*b, .25, .55)
    return clamp(eta*(273.15+water)/max(5., water-outdoor), 1.5, 8.)


def heat_demand(indoor, outdoor, pv_kw, stove, m):
    return max(0., m["heat_loss"]*(indoor-outdoor)-m["solar_gain"]*pv_kw
               -m["stove_gain"]*stove-m["background_gain"])


def heat_cost(price, pv_kw, other_kw, hp_kw, cop, discount):
    before = other_kw-pv_kw
    after = before+hp_kw
    def bill(net):
        return max(0., net)*price-max(0., -net)*(price-discount)
    return (bill(after)-bill(before))/(hp_kw*cop)


def price_level(premium):
    if numeric(premium) is None:
        return 0
    return 0 if premium <= .005 else 1 if premium <= .010 else 2 if premium <= .020 else 3 if premium <= .030 else 4

