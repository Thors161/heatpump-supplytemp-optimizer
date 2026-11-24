from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from conftest import NOW
from custom_components.heatpump_supplytemp_optimizer.engine import Engine
from custom_components.heatpump_supplytemp_optimizer.model import curve, predicted_cop


def sample(engine, states, start=NOW, minutes=15):
    for i in range(minutes+1):
        engine.sample(states, start+60*i)
    return engine.state["savings"]


@pytest.mark.parametrize("price,efficiency", [(0, 0), (2, 0), (0, 1), (2, 1)])
def test_same_measured_heat_at_final_shift(config, states, price, efficiency):
    states["sensor.demo_backup_power"]["state"] = "200"
    engine = Engine(config, now=NOW)
    engine.state.update(enabled=True, effective_reduction=price+efficiency,
                        decision_price_reduction=price, efficiency_addition=efficiency)
    ledger = sample(engine, states)
    day = next(iter(ledger["days"].values()))
    baseline = curve(5, config["parameters"], integer=True)
    original = predicted_cop(5, baseline, 3.8, config["model"])
    combined = predicted_cop(5, baseline-price-efficiency, 3.8, config["model"])
    assert day["actual_kwh"] == pytest.approx(.25)
    assert day["baseline_kwh"] == pytest.approx((.8*combined/original + .2)/4)
    assert ledger["saved_kwh"] == pytest.approx(day["baseline_kwh"]-.25)
    assert ledger["ratios"]["actual_cop"] == pytest.approx(4)
    assert ledger["ratios"]["combined_cop"] == pytest.approx(4/(3.8/combined+.2))
    assert ledger["ratios"]["original_cop"] == pytest.approx(4/(3.8/original+.2))


def test_defrost_counts_net_heat_and_electricity_without_credit(config, states):
    config["inputs"]["defrost_state"] = "binary_sensor.defrost"
    engine = Engine(config, now=NOW)
    engine.state.update(enabled=True, effective_reduction=2)
    sample(engine, states, minutes=10)
    states["binary_sensor.defrost"] = {"state":"on"}
    states["sensor.demo_heat_power"]["state"] = "-2"
    ledger = sample(engine, states, NOW+600, minutes=5)
    ratios = ledger["ratios"]
    assert ratios["actual_cop"] == pytest.approx((4*10-2*5)/(.8*15))
    assert ratios["actual_unit_cost"] == pytest.approx(.2*.4/.5)
    fully_heating = Engine(config, now=NOW)
    fully_heating.state.update(enabled=True, effective_reduction=2)
    states["binary_sensor.defrost"]["state"] = "off"
    states["sensor.demo_heat_power"]["state"] = "4"
    assert ledger["saved_kwh"] == pytest.approx(sample(fully_heating, states)["saved_kwh"]*2/3)


def test_quarter_prices_include_export_and_signed_savings(config, states):
    states["sensor.demo_electricity_price"]["state"] = "-0.10"
    states["sensor.demo_import_power"]["state"] = "0"
    states["sensor.demo_export_power"]["state"] = "0.02"
    states["sensor.demo_pv_power"]["state"] = "1000"
    e = Engine(config, now=NOW)
    e.state.update(enabled=True, effective_reduction=3)
    ledger = sample(e, states)
    day = next(iter(ledger["days"].values()))
    assert day["actual_eur"] == pytest.approx(.2*(-.21))
    extra = ledger["saved_kwh"]
    assert extra > .02/4
    assert ledger["saved_eur"] == pytest.approx(.02/4*(-.21) + (extra-.02/4)*(-.10))
    previous = day["actual_eur"]
    states["sensor.demo_electricity_price"]["state"] = ".30"
    sample(e, states, NOW+900)
    assert day["actual_eur"]-previous == pytest.approx(.2*.19)


@pytest.mark.parametrize("entity,value", [("sensor.demo_heating_mode","Tank"),
                                           ("sensor.demo_heat_power","unknown"),
                                           ("sensor.demo_electricity_price","unavailable")])
def test_partial_quarter_excludes_only_invalid_time(config, states, entity, value):
    e = Engine(config, now=NOW)
    sample(e, states, minutes=3)
    old = states[entity]["state"]
    states[entity]["state"] = value
    e.sample(states, NOW+240)
    states[entity]["state"] = old
    ledger = sample(e, states, NOW+300, minutes=10)
    day = next(iter(ledger["days"].values()))
    assert day["quarters"] == pytest.approx(840/900)
    assert day["actual_kwh"] == pytest.approx(.8*840/3600)
    assert ledger["ratios"]["actual_cop"] == pytest.approx(5.)
    assert sample(e, states, NOW+900)["last_quarter"] == NOW+900


def test_readback_and_restart_across_local_midnight(config, states):
    tz = ZoneInfo("Europe/Amsterdam")
    start = datetime(2026,10,25,23,45,tzinfo=tz).timestamp()
    config["inputs"]["reported_shift"] = "sensor.shift"
    states["sensor.shift"] = {"state":"0", "attributes":{"unit_of_measurement":"°C"}}
    e = Engine(config, now=start, tz=tz)
    e.state.update(enabled=True, effective_reduction=2)
    ledger = sample(e, states, start)
    assert ledger["saved_kwh"] == 0
    assert ledger["days"]["2026-10-25"]["quarters"] == 1
    states["sensor.shift"]["state"] = "-1"
    assert sample(e, states, start+900)["last_quarter"] == start
    states["sensor.shift"]["state"] = "-2"
    sample(e, states, start+1800, minutes=6)
    restored = Engine(config, e.serialize(), start+2220, tz=tz)
    ledger = sample(restored, states, start+2220, minutes=23)
    assert ledger["days"]["2026-10-26"]["quarters"] == pytest.approx(1 + 840/900)
    assert ledger["last_quarter"] == start+2700
    assert ledger["saved_kwh"] > 0
    total = ledger["saved_kwh"]
    replay = Engine(config, restored.serialize(), start+2700, tz=tz)
    assert sample(replay, states, start+2700)["saved_kwh"] == total


@pytest.mark.parametrize(("role", "invalid", "gap"), [("reported_shift", "-1", 8),
                                                      ("pv_power", "unavailable", 10)])
def test_brief_dropout_reduces_coverage_instead_of_removing_graph_point(config, states, role, invalid, gap):
    config["inputs"]["reported_shift"] = "sensor.shift"
    states["sensor.shift"] = {"state": "-2", "attributes": {"unit_of_measurement": "°C"}}
    e = Engine(config, now=NOW)
    e.state.update(enabled=True, effective_reduction=2)
    sensor = states[config["inputs"][role]]
    good = sensor["state"]
    sample(e, states, minutes=5)
    sensor["state"] = invalid
    e.sample(states, NOW+300)
    sensor["state"] = good
    e.sample(states, NOW+300+gap)
    ledger = sample(e, states, NOW+360, minutes=9)
    day = next(iter(ledger["days"].values()))
    assert day["quarters"] == pytest.approx((900-gap)/900)
    assert day["actual_kwh"] == pytest.approx(.8*(900-gap)/3600)
    for unit in ("kwh", "eur"):
        assert ledger["saved_"+unit] == pytest.approx(day["baseline_"+unit]-day["actual_"+unit])
    assert all(value is not None for value in ledger["ratios"].values())


def test_partial_quarter_closes_during_tank_cycle(config, states):
    e = Engine(config, now=NOW)
    sample(e, states, minutes=6)
    states["sensor.demo_heating_mode"]["state"] = "Tank"
    ledger = sample(e, states, NOW+360, minutes=24)
    day = next(iter(ledger["days"].values()))
    assert day["quarters"] == pytest.approx(360/900)
    assert ledger["last_quarter"] == NOW
    assert day["actual_kwh"] == pytest.approx(.08)


@pytest.mark.parametrize("role", ["heat_power", "electric_power"])
def test_zero_heat_or_electricity_leaves_ratios_blank(config, states, role):
    states[config["inputs"][role]]["state"] = "0"
    e = Engine(config, now=NOW)
    ledger = sample(e, states, minutes=15)
    assert all(value is None for value in ledger["ratios"].values())
