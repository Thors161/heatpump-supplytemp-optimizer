"""Emit a synthetic dashboard for JavaScript tests; never reads HA."""
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).parents[1]))
from custom_components.heatpump_supplytemp_optimizer.dashboard import build_dashboard
from custom_components.heatpump_supplytemp_optimizer.sensor import SENSORS
from custom_components.heatpump_supplytemp_optimizer.binary_sensor import DEFINITIONS

rooms=[{'id':'room_a','name':'Room A'}]
keys=list(SENSORS)+list(DEFINITIONS)+['enabled','price_enabled','efficiency_enabled','gap_high','gap_low','room_margin','room_recovery','refresh_forecast','export_dashboard','room_allowance_room_a','room_shortfall_room_a','room_limited_shortfall_room_a','room_max_shortfall_rise_room_a']
print(json.dumps(build_dashboard({k:'sensor.demo_'+k for k in keys},rooms)))

