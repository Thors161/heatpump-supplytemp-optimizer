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
    e.comfort=True
    assert e.price_request(NOW)>0
    e.decide(NOW)
    assert e.state["reason"]=="persistent_room_shortfall"
    assert e.state["effective_reduction"]==0


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

