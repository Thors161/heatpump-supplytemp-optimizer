"""Synthetic test homes only. No private entities, rooms or fitted models."""
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import sys
import pytest

sys.path.insert(0,str(Path(__file__).parents[1]))
from custom_components.heatpump_supplytemp_optimizer.const import REQUIRED_ROLES
from custom_components.heatpump_supplytemp_optimizer.schema import validate_config

NOW=datetime(2026,1,15,12,tzinfo=timezone.utc).timestamp()


@pytest.fixture
def config():
    inputs={key:"sensor.demo_"+key for key in REQUIRED_ROLES}
    inputs.update({k:"sensor.demo_"+k for k in ["backup_power","import_power","export_power","pv_power","electricity_price"]})
    inputs["weather"]="weather.demo"
    return validate_config({"inputs":inputs,"rooms":[{"id":"room_a","name":"Room A","climate":"climate.demo_a"}],
        "parameters":{"price_format":"generic","pv_forecast_format":"generic"},
        "model":{"confirmed":True,"heat_loss":.3,"solar_gain":.5,"background_gain":.2}})


@pytest.fixture
def states(config):
    values={"curve_outdoor":(5,"°C"),"local_outdoor":(5,"°C"),"heat_power":(4,"kW"),
        "electric_power":(800,"W"),"backup_power":(0,"W"),"supply_temperature":(34,"°C"),
        "primary_return":(31,"°C"),"secondary_return":(27,"°C"),"primary_flow":(20,"L/min"),
        "compressor_frequency":(30,"Hz"),"heating_mode":("Room",None),
        "import_power":(.8,"kW"),"export_power":(0,"kW"),"pv_power":(500,"W"),"electricity_price":(.4,"EUR/kWh")}
    result={config["inputs"][key]:{"state":str(value),"attributes":{"unit_of_measurement":unit}} for key,(value,unit) in values.items()}
    result["climate.demo_a"]={"state":"heat","attributes":{"current_temperature":20,"temperature":20,"temperature_unit":"°C"}}
    result["weather.demo"]={"state":"cloudy","attributes":{"temperature":5,"temperature_unit":"°C"}}
    result["sun.sun"]={"state":"above_horizon","attributes":{}}
    return result


@pytest.fixture
def forecasts():
    return {"prices":[{"start":NOW+i*900,"end":NOW+(i+1)*900,"price":.4 if i==0 else .2} for i in range(25)],
        "weather":[{"datetime":NOW+i*3600,"temperature":5} for i in range(8)],
        "solar":[[{"start":NOW+i*3600,"power_kw":.5} for i in range(8)]]}

