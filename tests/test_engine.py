from copy import deepcopy
import json
import pytest
from custom_components.heatpump_supplytemp_optimizer.engine import Engine
from custom_components.heatpump_supplytemp_optimizer.inputs import read_inputs
from custom_components.heatpump_supplytemp_optimizer.forecast import predict,valid_forecast
from custom_components.heatpump_supplytemp_optimizer.model import curve,heat_cost,price_level
from custom_components.heatpump_supplytemp_optimizer.rolling import Rolling
from custom_components.heatpump_supplytemp_optimizer.schema import validate_config
from custom_components.heatpump_supplytemp_optimizer.units import convert
from conftest import NOW


def prepare(config,states,forecasts):
    engine=Engine(config,now=NOW-4000)
    for seconds in range(-3600,1,60):
        engine.sample(states,NOW+seconds)
    engine.set_forecast(predict(config,engine.data,**forecasts,now=NOW))
    engine.state.update(enabled=True,price_enabled=True)
    return engine


@pytest.mark.parametrize("count",[1,2,9,20])
def test_dynamic_rooms(config,states,count):
    raw=deepcopy(config)
    raw["rooms"]=[{"id":f"room_{i}","name":f"Room {i}","actual":f"sensor.room_{i}_actual","setpoint":f"number.room_{i}_target","allowance":.2} for i in range(count)]
    config=validate_config(raw)
    for i in range(count):
        states[f"sensor.room_{i}_actual"]={"state":"19.3","attributes":{"unit_of_measurement":"°C"}}
        states[f"number.room_{i}_target"]={"state":"20","attributes":{"unit_of_measurement":"°C"}}
    d=read_inputs(config,states,NOW,{})
    assert len(d["rooms"])==count
    assert d["max_deficit"]==.5
    assert d["indoor"]==pytest.approx(19.3)


def test_missing_room_and_disabled_room_semantics(config,states):
    config["rooms"] += [{"id":"excluded","name":"Excluded","actual":"sensor.excluded","setpoint":"sensor.excluded_target","enabled":False,"allowance":0}]
    config=validate_config(config)
    d=read_inputs(config,states,NOW,{})
    assert d["rooms_ok"]
    states["climate.demo_a"]["state"]="unavailable"
    assert not read_inputs(config,states,NOW,{})["rooms_ok"]


def test_room_allowance_changes_guard_without_changing_model_temperature(config,states):
    states["climate.demo_a"]["attributes"]["current_temperature"]=18.5
    a=read_inputs(config,states,NOW,{}, {"room_a":0})
    b=read_inputs(config,states,NOW,{}, {"room_a":1})
    assert a["max_deficit"]==1.5 and b["max_deficit"]==.5
    assert a["indoor"]==b["indoor"]


def test_shortfall_rises_at_each_rooms_rate_and_drops_immediately(config, states):
    config["rooms"][0].update(max_shortfall_rise=1, allowance=.2)
    config["rooms"].append({"id": "room_b", "climate": "climate.demo_b", "max_shortfall_rise": .5})
    config = validate_config(config)
    states["climate.demo_b"] = deepcopy(states["climate.demo_a"])
    room_a = states["climate.demo_a"]["attributes"]
    room_b = states["climate.demo_b"]["attributes"]
    e = Engine(config, now=NOW)
    e.sample(states, NOW)

    room_a["temperature"] = 22
    room_b["current_temperature"] = 18
    for minute in range(1, 31):
        e.sample(states, NOW+minute*60)
    assert e.data["rooms"]["room_a"]["adjusted"] == 1.8
    assert e.data["rooms"]["room_a"]["limited"] == pytest.approx(.5)
    assert e.data["rooms"]["room_b"]["adjusted"] == 2
    assert e.data["rooms"]["room_b"]["limited"] == pytest.approx(.25)
    assert e.data["max_deficit"] == 2
    assert e.data["limited_max_deficit"] == .5
    assert e.data["indoor"] == 19
    assert e.observations["deficit"]["change"] == pytest.approx(.5)
    assert not e.comfort

    for minute in range(31, 61):
        e.sample(states, NOW+minute*60)
        assert e.comfort is (minute == 60)
    room_a["temperature"] = 20
    states["climate.demo_b"]["state"] = "off"
    e.sample(states, NOW+3601)
    assert e.data["rooms"]["room_a"]["limited"] == 0
    assert e.data["rooms"]["room_b"]["limited"] == 0
    assert e.data["limited_max_deficit"] == 0


def test_shortfall_limiter_does_not_credit_restart_or_missing_readings(config, states):
    e = Engine(config, now=NOW)
    e.state["shortfall_rise_limits"]["room_a"] = 1
    room = states["climate.demo_a"]
    e.sample(states, NOW)
    room["attributes"]["temperature"] = 22
    e.sample(states, NOW+60)
    previous = e.data["rooms"]["room_a"]["limited"]
    assert previous == pytest.approx(1/60)

    resumed = Engine(config, e.serialize(), now=NOW+3600)
    resumed.sample(states, NOW+3600)
    assert resumed.data["rooms"]["room_a"]["limited"] == previous
    room["state"] = "unavailable"
    resumed.sample(states, NOW+3630)
    assert not resumed.data["rooms_ok"]
    assert resumed.data["rooms"]["room_a"]["limited"] is None
    room["state"] = "heat"
    resumed.sample(states, NOW+3660)
    assert resumed.data["rooms"]["room_a"]["limited"] == previous
    resumed.sample(states, NOW+3720)
    assert resumed.data["rooms"]["room_a"]["limited"] == pytest.approx(2/60)
    resumed.sample(states, NOW+4500)
    assert resumed.data["rooms"]["room_a"]["limited"] == pytest.approx(2/60)


def test_existing_rooms_pass_through_adjusted_shortfall(config, states):
    e = Engine(config, now=NOW)
    e.sample(states, NOW)
    states["climate.demo_a"]["attributes"]["temperature"] = 22
    e.sample(states, NOW+60)
    assert e.data["limited_max_deficit"] == e.data["max_deficit"] == 2


@pytest.mark.parametrize("value,unit,kind,expected",[(1000,"W","power",1),(.6,"m³/h","flow",10),(68,"°F","temperature",20),(200,"EUR/MWh","price",.2)])
def test_units(value,unit,kind,expected):
    assert convert(value,unit,kind)==pytest.approx(expected)
    assert convert(value,"unsupported",kind) is None


def test_pv_cache_age_does_not_extend(config,states):
    cache={}
    read_inputs(config,states,NOW,cache)
    states[config["inputs"]["pv_power"]]["state"]="unavailable"
    assert read_inputs(config,states,NOW+300,cache)["pv_source"]=="last_valid"
    assert cache["at"]==NOW
    assert read_inputs(config,states,NOW+601,cache)["pv_power"] is None
    states["sun.sun"]["state"]="below_horizon"
    assert read_inputs(config,states,NOW+1000,cache)["pv_power"]==0
    assert cache["at"]==NOW


def test_forecast_complete_and_no_curve_probe_offset(config,states,forecasts):
    d=read_inputs(config,states,NOW,{})
    a=predict(config,d,**forecasts,now=NOW)
    assert a["complete"] and len(a["quarters"])==25
    assert a["reference_cost"]<a["current_cost"]
    states[config["inputs"]["curve_outdoor"]]["state"]="-5"
    b=predict(config,read_inputs(config,states,NOW,{}),**forecasts,now=NOW)
    assert a==b


def test_missing_forecasts_are_not_silently_filled(config,states,forecasts):
    d=read_inputs(config,states,NOW,{})
    forecasts["prices"]=forecasts["prices"][:3]
    f=predict(config,d,**forecasts,now=NOW)
    assert not f["complete"] and any("forecast_gap:price" in x for x in f["problems"])


def test_negative_prices_and_export_opportunity():
    assert heat_cost(.3,10,1,1,4,.11)==pytest.approx(.19/4)
    assert heat_cost(-.1,0,1,1,4,.11)==pytest.approx(-.1/4)
    assert heat_cost(-.1,10,1,1,4,.11)==pytest.approx(-.21/4)


def test_only_boundary_and_enable_changes_move_held_output(config,states,forecasts):
    e=prepare(config,states,forecasts)
    assert e.decide(NOW)
    old=deepcopy(e.state)
    states[config["inputs"]["curve_outdoor"]]["state"]="-5"
    e.sample(states,NOW+300)
    assert not e.decide(NOW+300)
    assert e.state["target"]==old["target"]
    assert e.state["effective_reduction"]==old["effective_reduction"]
    e.state["enabled"]=False
    assert e.decide(NOW+301,immediate=True)
    assert e.state["effective_reduction"]==0


def test_25_minute_guard_does_not_skip_half_hour(config,states,forecasts):
    e=prepare(config,states,forecasts)
    e.state["efficiency_enabled"]=False
    e.state["changed"]=NOW-1
    e.decide(NOW)
    assert e.state["reduction"]==0
    e.state["changed"]=NOW-1501
    e.last_decision_quarter=None
    e.decide(NOW)
    assert e.state["reduction"]==1


def test_comfort_overrides_price_request(config,states,forecasts):
    e=prepare(config,states,forecasts)
    e.state.update(reduction=2, effective_reduction=2, changed=NOW-60)
    e.comfort=True
    assert e.price_request(NOW)>0
    e.decide(NOW)
    assert e.state["reason"]=="persistent_room_shortfall; waiting for 25-minute minimum interval"
    assert e.state["effective_reduction"]==2
    assert e.state["combined_reduction"] > e.state["protected_reduction"] == 0


@pytest.mark.parametrize(("gap", "minimum", "shortfall", "trim", "reason"), [
    (.49, .49, 0., 2, "efficiency_low_gap_recovery"),
    (.50, .49, 0., 2, "efficiency_low_gap_recovery"),
    (.51, .49, 0., 3, "efficiency_margin_inconclusive"),
    (3.10, 2.90, 0., 3, "efficiency_margin_inconclusive"),
    (3.10, 3.00, 0., 4, "efficiency_margin_available"),
    (3.10, 3.00, .4, 2, "efficiency_room_shortfall_recovery"),
])
def test_efficiency_gap_limit_changes_only_efficiency_trim(
        config, states, forecasts, gap, minimum, shortfall, trim, reason):
    e = prepare(config, states, forecasts)
    e.state.update(efficiency=3, reduction=3, effective_reduction=3, decision_price_reduction=2)
    e.state["settings"].update(gap_low=.5, gap_high=3.)
    e.state["forecast"]["premium"] = .015
    e.data["return_gap"] = gap
    e.data["limited_max_deficit"] = shortfall
    e.observations["eligible_gap"]["minimum"] = minimum
    assert e.hydraulics_ready and e.observations_ready
    assert e.decide(NOW)
    assert e.state["efficiency"] == trim
    assert e.state["efficiency_reason"] == reason
    assert e.state["decision_price_reduction"] == 2
    assert e.efficiency_addition == trim-2
    assert e.state["combined_reduction"] == e.state["protected_reduction"] == e.state["reduction"] == trim


def test_efficiency_waits_for_samples_at_price_reduction_and_keeps_only_its_extra_trim(
        config, states, forecasts):
    e = prepare(config, states, forecasts)
    e.state["settings"].update(gap_low=.5, gap_high=3.)
    e.state["forecast"].update(premium=.015, valid_until=NOW+9000)

    def sample_through(start, end):
        for minute in range(start, end+1):
            e.sample(states, NOW+minute*60)

    assert e.decide(NOW)
    assert e.state["decision_price_reduction"] == e.state["combined_reduction"] == 2
    assert e.state["effective_reduction"] == 1
    assert e.state["efficiency_reason"] == "efficiency_waiting_for_reduction"
    sample_through(1, 30)
    assert e.decide(NOW+1800)
    assert e.state["effective_reduction"] == 2
    assert not e.observations_ready
    sample_through(31, 45)
    assert e.decide(NOW+2700)
    assert e.efficiency_addition == 0
    assert e.state["efficiency_reason"] == "efficiency_observations_warming_up"
    sample_through(46, 60)
    assert e.decide(NOW+3600)
    assert e.efficiency_addition == 1
    assert e.state["effective_reduction"] == 3

    e.state["forecast"]["premium"] = 0
    sample_through(61, 90)
    assert e.decide(NOW+5400)
    assert e.state["decision_price_reduction"] == 0
    assert e.efficiency_addition == e.state["combined_reduction"] == 1
    assert e.state["effective_reduction"] == 2


def test_efficiency_uses_advised_shift_through_reported_target_changes(config, states, forecasts):
    config["inputs"]["actual_target"] = "sensor.actual_target"
    states["sensor.actual_target"] = {"state": "40", "attributes": {"unit_of_measurement": "°C"}}
    e = prepare(config, states, forecasts)
    e.state["price_enabled"] = False
    e.state["settings"].update(gap_low=.5, gap_high=3.)
    assert e.decide(NOW)
    assert e.efficiency_addition == e.state["effective_reduction"] == 1
    assert not e.observations_ready

    for minute in range(1, 31):
        if minute == 8:
            states["sensor.actual_target"]["state"] = "unavailable"
        elif minute == 15:
            states["sensor.actual_target"]["state"] = "31"
        e.sample(states, NOW+minute*60)
    assert e.observations_ready
    assert e.decide(NOW+1800)
    assert e.efficiency_addition == e.state["effective_reduction"] == 2

    states[config["inputs"]["compressor_frequency"]]["state"] = "0"
    e.sample(states, NOW+2700)
    assert e.decide(NOW+2700)
    assert e.state["efficiency_reason"] == "efficiency_heating_paused"
    assert e.efficiency_addition == e.state["effective_reduction"] == 2


def test_higher_price_absorbs_earlier_addition_without_leapfrogging(config, states, forecasts):
    e = prepare(config, states, forecasts)
    e.state.update(efficiency=3, reduction=3, effective_reduction=3, decision_price_reduction=2)
    e.state["forecast"]["premium"] = .04
    e.data["return_gap"] = .2

    assert e.decide(NOW)
    assert e.state["efficiency_reason"] == "efficiency_low_gap_no_addition"
    assert e.state["decision_price_reduction"] == 4
    assert e.efficiency_addition == 0
    assert e.state["combined_reduction"] == e.state["protected_reduction"] == 4
    assert e.state["effective_reduction"] == 4

def test_estimated_capacity_status_matches_shared_protection(config, states, forecasts):
    e = prepare(config, states, forecasts)
    forecast = e.state["forecast"]
    heat_demand = forecast["quarters"][0]
    capacity = config["parameters"]["capacity_kw"]
    heat_demand["heat_demand_kw"] = capacity - .1
    assert e.estimated_capacity_reached(NOW) is False
    heat_demand["heat_demand_kw"] = capacity
    assert e.estimated_capacity_reached(NOW) is True
    assert e.decide(NOW)
    assert e.state["reason"] == "little_estimated_capacity_margin"
    assert e.estimated_capacity_reached(forecast["valid_until"]) is None


@pytest.mark.parametrize(("inactive", "active"), [("Inactive", "Active"), ("off", "on")])
def test_defrost_protection_uses_observed_start_intervals_and_expires(config, states, inactive, active):
    config["inputs"]["defrost_state"] = "sensor.defrost"
    config["settings"]["minimum_defrost_interval"] = 30
    e = Engine(config, now=NOW)

    def report(value, elapsed, engine=e):
        states["sensor.defrost"] = {"state": value}
        engine.sample(states, NOW+elapsed)
        return engine.defrost_protection(NOW+elapsed)

    assert not report(active, 0)  # A sensor that is already active at startup is not a new start.
    assert not report(inactive, 30)
    assert not report(active, 60)
    assert not report(active, 120)
    assert not report(inactive, 180)
    assert not report(active, 1860)  # Exactly the configured minimum.
    assert not report("unavailable", 1890)
    assert not report(active, 1920)  # An unknown interval cannot establish a start.
    assert not report(inactive, 1950)
    assert report(active, 1980)

    restored = Engine(config, e.serialize(), now=NOW+2000)
    assert report(active, 2000, restored)
    assert report(inactive, 2100, restored)
    assert report(active, 2700, restored)
    assert not restored.defrost_protection(NOW+4500)
    restored.state["settings"]["minimum_defrost_interval"] = 0
    assert not restored.defrost_protection(NOW+2700)


def test_frequent_defrost_backs_off_combined_request_in_normal_steps(config, states, forecasts):
    config["inputs"]["defrost_state"] = "sensor.defrost"
    config["settings"]["minimum_defrost_interval"] = 90
    states["sensor.defrost"] = {"state": "Inactive"}
    e = prepare(config, states, forecasts)
    e.state.update(efficiency=3, reduction=3, effective_reduction=3, decision_price_reduction=2)
    e.state["forecast"].update(premium=.015, valid_until=NOW+8000)
    for elapsed, defrost in [(30, "Active"), (60, "Inactive"), (600, "Active"), (660, "Inactive")]:
        states["sensor.defrost"]["state"] = defrost
        e.sample(states, NOW+elapsed)
        assert e.state["effective_reduction"] == 3

    for elapsed, held in [(900, 2), (1800, 2), (2700, 1), (3600, 1), (4500, 0)]:
        e.sample(states, NOW+elapsed)
        assert e.decide(NOW+elapsed)
        assert e.state["reason"].startswith("defrost_interval_too_short")
        assert e.state["decision_price_reduction"] == 2
        assert e.efficiency_addition == 1
        assert e.state["combined_reduction"] == 3
        assert e.state["protected_reduction"] == 0
        assert e.state["effective_reduction"] == held


def test_dhw_excluded_from_cop_and_pause_negative_heat_included():
    r=Rolling()
    for i in range(31):
        r.add(NOW+i*60,{"heat":-1 if i<5 else 0 if i<10 else 4,"electric":1})
    assert r.cop(NOW+1800)==pytest.approx(75/30)
    r.add(NOW+1801,{"heat":None,"electric":None})
    r.add(NOW+1861,{"heat":4,"electric":1})
    assert r.cop(NOW+1861) is None


def test_snapshot_roundtrip_holds_command_and_does_not_bridge_restart(config,states,forecasts):
    e=prepare(config,states,forecasts)
    e.decide(NOW)
    restored=Engine(config,json.loads(json.dumps(e.serialize())),NOW+30)
    assert restored.state["target"]==e.state["target"]
    assert restored.state["effective_reduction"]==e.state["effective_reduction"]
    assert restored.state["changed"]==e.state["changed"]
    restored.sample(states,NOW+30)
    assert not restored.observations_ready
    assert restored.state["combined_reduction"] == e.state["combined_reduction"]
    assert restored.power.cop(NOW+30) is None
    assert not restored.state["power_history"]["interval"]["valid"]


@pytest.mark.parametrize("bad",[{"rooms":[]},{"parameters":{"curve_cold_outdoor":20,"curve_warm_outdoor":10}},{"settings":{"gap_low":8,"gap_high":2}}])
def test_invalid_profiles_are_rejected(config,bad):
    config.update(bad)
    with pytest.raises(ValueError):validate_config(config)


def test_curve_rounding_configurable(config):
    p=config["parameters"]
    p.update(curve_cold_outdoor=-6,curve_warm_outdoor=14,curve_cold_water=40,curve_warm_water=28)
    assert curve(5,p,integer=True)==34
    p["curve_rounding"]="nearest"
    assert curve(5,p,integer=True)==33
    p["curve_rounding"]="none"
    assert curve(5,p,integer=True)==pytest.approx(33.4)


def test_fractional_floor_reports_an_achievable_integer_shift(config,states,forecasts):
    e=prepare(config,states,forecasts)
    e.config['parameters']['curve_rounding']='none'
    e.config['parameters']['target_floor']=30.5
    e.state.update(efficiency_enabled=False,reduction=3,changed=NOW-1501)
    e.decide(NOW)
    assert e.state['target']>=30.5
    assert e.state['target']==pytest.approx(e.state['baseline']-e.state['effective_reduction'])

