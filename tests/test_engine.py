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


def set_quarter_price(engine, now, premium):
    forecast = engine.state["forecast"]
    forecast.update(generated_at=now, valid_until=now+1200, premium=premium)
    forecast["quarters"][0]["start"] = int(now//900)*900


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


@pytest.mark.parametrize("value,unit,kind,expected",[(1000,"W","power",1),(.6,"m³/h","flow",10),(68,"°F","temperature",20),(-3.6,"°F","temperature_delta",-2),(200,"EUR/MWh","price",.2)])
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
    assert e.state["target"]==old["target"]
    assert e.state["effective_reduction"]==old["effective_reduction"]
    e.state["enabled"]=False
    assert e.decide(NOW+301)
    assert e.state["effective_reduction"]==0


def test_25_minute_guard_does_not_skip_half_hour(config,states,forecasts):
    e=prepare(config,states,forecasts)
    e.state["efficiency_enabled"]=False
    e.state["changed"]=NOW-1
    e.decide(NOW)
    assert e.state["effective_reduction"]==0
    e.state["changed"]=NOW-1501
    e.decide(NOW)
    assert e.state["effective_reduction"]==1


def test_comfort_overrides_price_request(config,states,forecasts):
    e=prepare(config,states,forecasts)
    e.state.update(effective_reduction=2, changed=NOW-60)
    e.comfort=True
    assert e.price_request(NOW)>0
    e.decide(NOW)
    assert e.state["reason"]=="persistent_room_shortfall; waiting for 25-minute minimum interval"
    assert e.state["effective_reduction"]==2
    assert e.state["combined_reduction"] > e.state["protected_reduction"] == 0
    assert e.state["decision_at"] == NOW
    assert e.state["next_step_at"] == NOW+1800


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
    e.state.update(efficiency_addition=1, effective_reduction=3, decision_price_reduction=2)
    e.state["settings"].update(gap_low=.5, gap_high=3.)
    e.state["forecast"]["premium"] = .015
    e.data["return_gap"] = gap
    e.data["limited_max_deficit"] = shortfall
    e.observations["eligible_gap"]["minimum"] = minimum
    assert e.hydraulics_ready and e.observations_ready
    assert e.decide(NOW)
    assert e.state["efficiency_reason"] == reason
    assert e.state["decision_price_reduction"] == 2
    assert e.efficiency_addition == trim-2
    assert e.state["combined_reduction"] == e.state["protected_reduction"] == e.state["effective_reduction"] == trim


def test_efficiency_keeps_observations_and_takes_over_released_price(
        config, states, forecasts):
    e = prepare(config, states, forecasts)
    e.state["settings"].update(gap_low=.5, gap_high=3.)
    set_quarter_price(e, NOW, .015)

    def sample_through(start, end):
        for minute in range(start, end+1):
            e.sample(states, NOW+minute*60)
        set_quarter_price(e, NOW+end*60, e.state["forecast"]["premium"])

    assert e.decide(NOW)
    assert e.state["decision_price_reduction"] == e.state["combined_reduction"] == 2
    assert e.state["effective_reduction"] == 1
    assert e.state["efficiency_reason"] == "efficiency_waiting_for_reduction"
    assert e.state["evaluated_reduction"] == 0
    sample_through(1, 30)
    assert e.decide(NOW+1800)
    assert e.state["effective_reduction"] == 2
    assert e.observations_ready
    sample_through(31, 45)
    assert e.decide(NOW+2700)
    assert e.efficiency_addition == 0
    assert e.state["efficiency_reason"] == "efficiency_margin_available; waiting for 25-minute minimum interval"
    assert e.state["next_step_at"] == NOW+3600
    sample_through(46, 60)
    assert e.decide(NOW+3600)
    assert e.efficiency_addition == 1
    assert e.state["effective_reduction"] == 3

    e.state["forecast"]["premium"] = 0
    sample_through(61, 90)
    assert e.decide(NOW+5400)
    assert e.state["decision_price_reduction"] == 0
    assert e.efficiency_addition == e.state["combined_reduction"] == 3
    assert e.state["effective_reduction"] == 3
    assert e.state["efficiency_reason"] == "efficiency_holds_released_price"


def test_efficiency_uses_advised_shift_through_reported_target_changes(config, states, forecasts):
    config["inputs"]["actual_target"] = "sensor.actual_target"
    states["sensor.actual_target"] = {"state": "40", "attributes": {"unit_of_measurement": "°C"}}
    e = prepare(config, states, forecasts)
    e.state["price_enabled"] = False
    e.state["settings"].update(gap_low=.5, gap_high=3.)
    assert e.decide(NOW)
    assert e.efficiency_addition == e.state["effective_reduction"] == 1
    assert e.observations_ready

    for minute in range(1, 31):
        if minute == 8:
            states["sensor.actual_target"]["state"] = "unavailable"
        elif minute == 15:
            states["sensor.actual_target"]["state"] = "31"
            states[config["inputs"]["curve_outdoor"]]["state"] = "6"
        e.sample(states, NOW+minute*60)
    assert e.observations_ready
    assert e.decide(NOW+1800)
    assert e.efficiency_addition == e.state["effective_reduction"] == 2

    states[config["inputs"]["compressor_frequency"]]["state"] = "0"
    e.sample(states, NOW+2700)
    assert e.decide(NOW+2700)
    assert e.state["efficiency_reason"] == "efficiency_heating_paused"
    assert e.efficiency_addition == e.state["effective_reduction"] == 2


def test_shift_readback_holds_efficiency_while_price_can_change(config, states, forecasts):
    config["inputs"]["reported_shift"] = "sensor.reported_shift"
    states["sensor.reported_shift"] = {"state": "-1", "attributes": {"unit_of_measurement": "°C"}}
    e = prepare(config, states, forecasts)
    e.state.update(efficiency_addition=1, effective_reduction=1, decision_price_reduction=0)
    for elapsed in range(0, 1801, 60):
        e.sample(states, NOW+elapsed)
    assert e.observations_ready
    set_quarter_price(e, NOW+1800, .015)
    assert e.decide(NOW+1800)
    assert e.state["effective_reduction"] == e.state["decision_price_reduction"] == 2
    assert e.shift_confirmation(NOW+1800) == "waiting"

    sample_count = len(e.efficiency_gap.samples)
    e.sample(states, NOW+1860)
    assert len(e.efficiency_gap.samples) == sample_count
    for elapsed in range(1920, 2701, 60):
        e.sample(states, NOW+elapsed)
    set_quarter_price(e, NOW+2700, .015)
    assert e.decide(NOW+2700)
    assert e.state["efficiency_reason"] == "efficiency_shift_not_applied"
    assert e.state["combined_reduction"] == e.state["protected_reduction"] == 2

    set_quarter_price(e, NOW+3600, .04)
    for elapsed in range(2760, 3601, 60):
        e.sample(states, NOW+elapsed)
    assert e.decide(NOW+3600)
    assert e.state["efficiency_reason"] == "efficiency_shift_not_applied"
    assert e.state["combined_reduction"] == e.state["protected_reduction"] == 4
    assert e.state["effective_reduction"] == 3
    assert e.shift_confirmation(NOW+3600) == "waiting"

    states[config["inputs"]["heating_mode"]]["state"] = "Tank"
    e.sample(states, NOW+3660)
    assert e.shift_confirmation(NOW+3660) == "inactive"
    states[config["inputs"]["heating_mode"]]["state"] = "Room"
    e.sample(states, NOW+3720)
    assert e.shift_confirmation(NOW+3720) == "waiting"
    states["sensor.reported_shift"]["state"] = "-3"
    e.sample(states, NOW+3780)
    assert e.shift_confirmation(NOW+3780) == "confirmed"


def prepare_price_handoff(config, states, forecasts, old_price, applied):
    config["inputs"]["reported_shift"] = "sensor.shift"
    states["sensor.shift"] = {"state": str(-applied), "attributes": {"unit_of_measurement": "°C"}}
    e = Engine(config, now=NOW-3600)
    e.state.update(enabled=True, price_enabled=True, decision_price_reduction=old_price,
                   effective_reduction=applied, changed=NOW-900)
    e.state["settings"].update(gap_low=.8, gap_high=1.5)
    for minute in range(-60, 1):
        e.sample(states, NOW+minute*60)
    e.set_forecast(predict(config,e.data,**forecasts,now=NOW))
    assert e.observations_ready
    return e


@pytest.mark.parametrize(("old_price", "applied", "premium", "new_price"), [
    (4, 4, .008, 1),  # Retaining the configured maximum is allowed.
    (4, 2, .008, 1),  # Price had requested more than was ever applied.
    (1, 3, 0., 0),    # The shift was still unwinding from an earlier Price drop.
])
def test_eligible_price_handoff_preserves_shift_and_timer(
        config, states, forecasts, old_price, applied, premium, new_price):
    e = prepare_price_handoff(config, states, forecasts, old_price, applied)
    set_quarter_price(e, NOW, premium)
    e.decide(NOW)
    assert e.state["decision_price_reduction"] == new_price
    assert e.efficiency_addition == applied-new_price, e.state["efficiency_reason"]
    assert e.state["combined_reduction"] == e.state["effective_reduction"] == applied
    assert e.state["changed"] == NOW-900 and e.state["efficiency_changed"] == 0
    assert e.state["efficiency_reason"] == "efficiency_holds_released_price"

    e.decide(NOW+1)
    assert e.state["effective_reduction"] == applied
    assert e.efficiency_addition == applied-new_price, e.state["efficiency_reason"]


@pytest.mark.parametrize(("shortfall", "trend", "old_price", "recovery", "addition"), [
    (.1, 0., 2, .5, 1),       # New trim at the margin.
    (.2, 0., 2, .5, 0),       # New trim above the margin.
    (.4, .1, 3, .5, 1),       # Hold the applied degree released by Price.
    (.5, 0., 3, .5, 1),       # The recovery threshold itself permits a handoff.
    (.500001, 0., 3, .5, 0),
    (.4, 0., 3, .3, 0),
    (.6, 0., 3, .6, 1),
    (.4, .11, 3, .5, 0),      # Rapidly growing shortfall still blocks a handoff.
])
def test_room_margin_for_price_handoff_and_new_trim(config, states, forecasts, shortfall, trend, old_price, recovery, addition):
    e = prepare_price_handoff(config, states, forecasts, old_price, old_price)
    e.state["settings"].update(room_margin=.1, room_recovery=recovery)
    e.state["changed"] = NOW-1800
    e.data["limited_max_deficit"] = shortfall
    e.observations["deficit"]["change"] = trend
    set_quarter_price(e, NOW, .015)
    e.decide(NOW)
    assert e.state["decision_price_reduction"] == 2
    assert e.efficiency_addition == addition
    assert e.state["effective_reduction"] == 2+addition


@pytest.mark.parametrize("blocker", ["shortfall", "gap", "comfort", "readback", "floor"])
def test_price_handoff_requires_eligible_shift(config, states, forecasts, blocker):
    e = prepare_price_handoff(config, states, forecasts, 3, 3)
    e.state["changed"] = NOW-1800
    states["sensor.shift"]["state"] = "-1" if blocker == "readback" else "-3"
    e.sample(states, NOW)
    if blocker == "shortfall":
        e.data["limited_max_deficit"] = e.state["settings"]["room_recovery"] + .01
    elif blocker == "gap":
        e.observations["eligible_gap"]["minimum"] = 1.4
    elif blocker == "comfort":
        e.comfort = True
    elif blocker == "floor":
        config["parameters"]["target_floor"] = curve(e.data["curve_outdoor"], config["parameters"], integer=True)-2
    set_quarter_price(e, NOW, 0.)
    e.decide(NOW)
    assert e.efficiency_addition == e.state["combined_reduction"] == 0
    assert e.state["effective_reduction"] == 2


def test_higher_price_absorbs_earlier_addition_without_leapfrogging(config, states, forecasts):
    e = prepare(config, states, forecasts)
    e.state.update(efficiency_addition=1, effective_reduction=3, decision_price_reduction=2)
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
    need = e.state["forecast"]["quarters"][0]["heat_demand_kw"]
    config["parameters"]["capacity_kw"] = need + .1
    assert e.estimated_capacity_reached(NOW) is False
    config["parameters"]["capacity_kw"] = need
    e.state["price_enabled"] = False
    forecasts["prices"].pop()
    e.set_forecast(predict(config, e.data, **forecasts, now=NOW))
    assert not valid_forecast(e.state["forecast"], NOW)
    assert e.estimated_capacity_reached(NOW) is True
    e.decide(NOW)
    assert e.state["reason"] == "little_estimated_capacity_margin"
    states[config["inputs"]["local_outdoor"]]["state"] = "unavailable"
    e.sample(states, NOW+60)
    assert e.estimated_capacity_reached(NOW+60) is None


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
    e.state.update(efficiency_addition=1, effective_reduction=3, decision_price_reduction=2)
    for elapsed, defrost in [(30, "Active"), (60, "Inactive"), (600, "Active"), (660, "Inactive")]:
        states["sensor.defrost"]["state"] = defrost
        e.sample(states, NOW+elapsed)
        assert e.state["effective_reduction"] == 3

    for elapsed, held in [(900, 2), (1800, 2), (2700, 1), (3600, 1), (4500, 0)]:
        e.sample(states, NOW+elapsed)
        set_quarter_price(e, NOW+elapsed, .015)
        assert e.decide(NOW+elapsed)
        assert e.state["reason"].startswith("defrost_interval_too_short")
        assert e.state["decision_price_reduction"] == 2
        assert e.efficiency_addition == 1
        assert e.state["combined_reduction"] == 3
        assert e.state["protected_reduction"] == 0
        assert e.state["effective_reduction"] == held


@pytest.mark.parametrize("resume_within_grace", [True, False])
def test_routine_defrost_preserves_efficiency_history_during_recovery(
        config, states, forecasts, resume_within_grace):
    config["inputs"]["defrost_state"] = "sensor.defrost"
    config["settings"]["minimum_defrost_interval"] = 50
    states["sensor.defrost"] = {"state": "Inactive"}
    compressor = states[config["inputs"]["compressor_frequency"]]
    e = prepare(config, states, forecasts)
    e.state.update(price_enabled=False, efficiency_addition=1, effective_reduction=1)
    for elapsed in range(60, 601, 60):
        e.sample(states, NOW+elapsed)
    first_eligible = e.learning_since
    last_gap = e.efficiency_gap.samples[-1]
    assert e.observations_ready

    states["sensor.defrost"]["state"] = "Active"
    compressor["state"] = "0"
    e.sample(states, NOW+640)
    for elapsed in range(660, 901, 60):
        e.sample(states, NOW+elapsed)
    assert e.decide(NOW+900)
    assert e.state["efficiency_reason"] == "efficiency_defrost_pause"
    assert e.state["effective_reduction"] == 1
    compressor["state"] = "30"
    e.sample(states, NOW+960)
    assert not e.data["learning_ok"]
    assert [row for row in e.efficiency_gap.samples if "gap" in row[1]][-1] == last_gap
    assert e.learning_since == first_eligible

    for elapsed in range(1020, 1141, 60):
        e.sample(states, NOW+elapsed)
    states["sensor.defrost"]["state"] = "Inactive"
    compressor["state"] = "0"
    e.sample(states, NOW+1180)  # Nine minutes after the defrost started.
    assert e.data["defrost_pause"]
    assert [row for row in e.efficiency_gap.samples if "gap" in row[1]][-1] == last_gap
    if resume_within_grace:
        compressor["state"] = "30"
        e.sample(states, NOW+1210)
        assert e.learning_since == first_eligible
        assert e.observations_ready
        assert not e.data["defrost_pause"]
        assert e.efficiency_gap.samples[-1][0] == NOW+1210
        compressor["state"] = "0"
        e.sample(states, NOW+1260)
        e.sample(states, NOW+1320)
    else:
        e.sample(states, NOW+1240)
    assert not e.data["defrost_pause"]
    assert not e.efficiency_gap.samples
    assert e.learning_since is None


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
    e.state.update(efficiency_enabled=False,effective_reduction=3,changed=NOW-1501)
    e.decide(NOW)
    assert e.state['target']>=30.5
    assert e.state['target']==pytest.approx(e.state['baseline']-e.state['effective_reduction'])


def test_cold_room_releases_shift_with_missing_curve(config, states, forecasts):
    e = prepare(config, states, forecasts)
    e.state.update(price_enabled=False, efficiency_enabled=True, effective_reduction=3)
    states[config["inputs"]["curve_outdoor"]]["state"] = "unavailable"
    states["climate.demo_a"]["attributes"]["current_temperature"] = 19
    for minute in range(31):
        e.sample(states, NOW+60*minute)
    assert e.comfort
    for elapsed, final in [(1800, 2), (2700, 2), (3600, 1), (5400, 0)]:
        e.decide(NOW+elapsed)
        assert e.state["protected_reduction"] == 0
        assert e.state["effective_reduction"] == final
        assert e.state["reason"].startswith("persistent_room_shortfall")
        assert e.state["target"] is None


def test_relaxing_floor_steps_from_last_final_shift(config, states, forecasts):
    e = prepare(config, states, forecasts)
    e.state.update(efficiency_enabled=False, effective_reduction=4)
    for minute, outside, expected in [(0, 15, 1), (15, 0, 1), (30, 0, 2)]:
        now = NOW + minute*60
        set_quarter_price(e, now, .04)
        for step in range(max(1, minute-14), minute):
            e.sample(states, NOW+step*60)
        states[config["inputs"]["curve_outdoor"]]["state"] = str(outside)
        e.sample(states, now)
        e.decide(now)
        assert e.state["effective_reduction"] == expected


def test_legacy_restart_retains_only_efficiency_share(config, states, forecasts):
    saved = prepare(config, states, forecasts).serialize()
    saved.pop("efficiency_addition", None)
    saved.update(efficiency=3, decision_price_reduction=2, effective_reduction=1,
                 reduction=3, changed=NOW-60)
    e = Engine(config, saved, NOW)
    assert e.efficiency_addition == 1
    assert e.state["effective_reduction"] == 1
    e.sample(states, NOW)
    e.state["forecast"]["premium"] = 0
    e.decide(NOW)
    assert e.state["combined_reduction"] == e.state["effective_reduction"] == 1
    resumed = Engine(config, e.serialize(), NOW+30)
    assert resumed.efficiency_addition == 1
    assert resumed.state["changed"] == NOW-60


def test_defrost_sensor_can_follow_compressor_stop(config, states, forecasts):
    config["inputs"]["defrost_state"] = "binary_sensor.defrost"
    states["binary_sensor.defrost"] = {"state": "off"}
    e = prepare(config, states, forecasts)
    compressor = states[config["inputs"]["compressor_frequency"]]
    compressor["state"] = "0"
    e.sample(states, NOW+1)
    assert not e.data["learning_ok"]
    states["binary_sensor.defrost"]["state"] = "on"
    e.sample(states, NOW+2)
    for minute in range(1, 6):
        e.sample(states, NOW+60*minute)
    states["binary_sensor.defrost"]["state"] = "off"
    compressor["state"] = "30"
    e.sample(states, NOW+360)
    assert e.observations_ready
    assert e.efficiency_gap.samples[0][0] < NOW

    compressor["state"] = "0"
    e.sample(states, NOW+400)
    e.sample(states, NOW+460)
    assert not e.observations_ready
    assert not e.efficiency_gap.samples


def test_sparse_gap_history_needs_observed_minutes(config, states, forecasts):
    config["inputs"]["reported_shift"] = "number.reported_shift"
    states["number.reported_shift"] = {"state": "0"}
    e = prepare(config, states, forecasts)
    e.state["price_enabled"] = False
    e.state["settings"].update(gap_low=.5, gap_high=3.)
    for minute in range(1, 32):
        states["number.reported_shift"]["state"] = "0" if minute in (1, 31) else "-1"
        e.sample(states, NOW+minute*60)
    e.decide(NOW+1860)
    assert len(e.efficiency_gap.samples) == 2
    assert not e.observations_ready
    assert e.state["effective_reduction"] == 0


def test_late_boundary_splits_measured_power(config, states):
    e = Engine(config, now=NOW-60)
    e.state["predictions"][str(int(NOW))] = {"kw": 4, "at": NOW-10}
    for seconds in range(0, 900, 60):
        e.sample(states, NOW+seconds)
    states[config["inputs"]["heat_power"]]["state"] = "6"
    e.sample(states, NOW+908)
    history = e.state["power_history"]
    assert history["measured"] == pytest.approx(4.)
    assert history["interval"]["area"] == pytest.approx(8*4.)


def test_dhw_holds_heating_decision_across_quarters_and_restart(config, states, forecasts):
    e = prepare_price_handoff(config, states, forecasts, old_price=2, applied=3)
    e.state.update(efficiency_addition=1, combined_reduction=3, protected_reduction=3,
                   baseline=34, target=31, next_step_at=NOW+900)
    keys = ("effective_reduction", "efficiency_addition", "decision_price_reduction",
            "combined_reduction", "protected_reduction", "baseline", "target", "changed", "efficiency_changed")
    held = {key: e.state[key] for key in keys}
    states[config["inputs"]["heating_mode"]]["state"] = "Tank"
    states["sensor.shift"]["state"] = "0"
    for quarter in range(1, 5):
        now = NOW+quarter*900
        if quarter == 3:
            e = Engine(config, e.serialize(), now)
        e.sample(states, now)
        set_quarter_price(e, now, 0.)
        assert e.decide(now)
        assert e.state["reason"] == e.state["efficiency_reason"] == "dhw_pause"
        assert {key: e.state[key] for key in keys} == held
        assert e.state["decision_at"] == now and e.state["next_step_at"] is None
        assert e.shift_confirmation(now) == "inactive"
        assert not e.observations_ready and not e.efficiency_gap.samples

    states[config["inputs"]["heating_mode"]]["state"] = "Room"
    e.sample(states, now+60)
    assert e.state["decision_at"] == now and e.state["effective_reduction"] == 3
    states["sensor.shift"]["state"] = "-3"
    for minute in range(2, 16):
        e.sample(states, now+minute*60)
    set_quarter_price(e, now+900, 0.)
    e.decide(now+900)
    assert e.state["decision_price_reduction"] == 0
    assert e.state["efficiency_reason"] == "efficiency_observations_warming_up"
    assert e.state["reason"] == "hydraulic_history_hold"
    assert e.state["effective_reduction"] == 3


def test_eligible_room_decision_can_take_over_price_released_during_dhw(config, states, forecasts):
    e = prepare_price_handoff(config, states, forecasts, old_price=3, applied=3)
    mode = states[config["inputs"]["heating_mode"]]
    mode["state"] = "Tank"
    e.sample(states, NOW)
    set_quarter_price(e, NOW, 0.)
    e.decide(NOW)
    mode["state"] = "Room"
    for minute in range(1, 46):
        e.sample(states, NOW+minute*60)
    # Exercise the decision with a fully rebuilt Room window.
    now = NOW+2700
    set_quarter_price(e, now, 0.)
    e.decide(now)
    assert e.state["efficiency_reason"] == "efficiency_holds_released_price"
    assert e.state["decision_price_reduction"] == 0
    assert e.efficiency_addition == e.state["effective_reduction"] == 3


def test_short_tank_cycle_does_not_trigger_a_decision(config, states, forecasts):
    e = prepare(config, states, forecasts)
    e.decide(NOW)
    before = dict(e.state)
    for minute, mode in [(2, "Tank"), (6, "Room")]:
        states[config["inputs"]["heating_mode"]]["state"] = mode
        e.sample(states, NOW+minute*60)
        for key in ("decision_at", "reason", "effective_reduction", "efficiency_addition", "changed"):
            assert e.state[key] == before[key]


@pytest.mark.parametrize(("mode","enabled","expected","final"), [
    ("Tank", False, "advice_disabled", 0),
    ("unknown", True, "invalid_water_measurements", 2),
    ("Room", True, "invalid_water_measurements", 2),
])
def test_dhw_hold_only_applies_to_enabled_advice_in_tank_mode(
        config, states, forecasts, mode, enabled, expected, final):
    e = prepare(config, states, forecasts)
    e.state.update(enabled=enabled, effective_reduction=3, efficiency_addition=1)
    states[config["inputs"]["heating_mode"]]["state"] = mode
    if mode == "Room":
        states[config["inputs"]["primary_return"]]["state"] = "unavailable"
    e.sample(states, NOW)
    e.decide(NOW)
    assert e.state["reason"].split(";")[0] == expected
    assert e.state["effective_reduction"] == final


def test_repeated_defrost_keeps_ready_window_while_protection_holds(config, states, forecasts):
    config["inputs"]["defrost_state"] = "sensor.defrost"
    config["settings"]["minimum_defrost_interval"] = 45
    states["sensor.defrost"] = {"state": "Inactive"}
    e = prepare(config, states, forecasts)
    e.state["price_enabled"] = False
    for minute in range(1, 93):
        active = (minute-1) % 42 < 8
        states["sensor.defrost"]["state"] = "Active" if active else "Inactive"
        states[config["inputs"]["compressor_frequency"]]["state"] = "0" if active else "30"
        now = NOW + minute*60
        e.sample(states, now)
        assert e.observations_ready, (minute, e.observations, e.learning_since)
        assert e.data["learning_ok"] == (not active)
        if minute >= 43 and minute % 15 == 0:
            e.decide(now)
            assert e.state["protected_reduction"] == 0
            assert e.state["reason"].startswith("defrost_interval_too_short")


def test_independent_protections_track_while_disabled(config, states, forecasts):
    config["inputs"]["defrost_state"] = "binary_sensor.defrost"
    states["binary_sensor.defrost"] = {"state": "off"}
    e = prepare(config, states, forecasts)
    e.state.update(comfort_enabled=False, defrost_enabled=False, price_enabled=False)
    e.state["settings"]["minimum_defrost_interval"] = 50
    states["climate.demo_a"]["attributes"]["current_temperature"] = 19
    for minute in range(61):
        now = NOW + 60*minute
        if minute == 29:
            e.state["settings"]["comfort_threshold"] = 1.
        states["binary_sensor.defrost"]["state"] = "on" if minute in (10, 50) else "off"
        e.sample(states, now)
        assert not e.comfort_protection and not e.defrost_protection(now)
        assert e.comfort == (minute >= 59)
        if minute in (10, 50):
            assert e.data["defrost_pause"]
    for comfort, defrost, reason in [(True, True, "persistent_room_shortfall"),
                                     (False, True, "defrost_interval_too_short"),
                                     (True, False, "persistent_room_shortfall")]:
        e.state.update(comfort_enabled=comfort, defrost_enabled=defrost)
        e.decide(now)
        assert e.state["reason"].startswith(reason)
        assert e.state["protected_reduction"] == 0
