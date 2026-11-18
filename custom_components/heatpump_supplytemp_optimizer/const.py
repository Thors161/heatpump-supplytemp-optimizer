"""Public defaults and input roles. No household-specific configuration."""
DOMAIN = "heatpump_supplytemp_optimizer"
VERSION = "2.0.0-beta.1"
STORAGE_VERSION = 1
PLATFORMS = ["sensor", "binary_sensor", "switch", "number", "button"]

# Input-role names are stable; entity IDs are configured locally.
REQUIRED_ROLES = {
    "curve_outdoor": "temperature", "local_outdoor": "temperature",
    "heat_power": "power", "electric_power": "power",
    "supply_temperature": "temperature", "primary_return": "temperature",
    "secondary_return": "temperature", "primary_flow": "flow",
    "secondary_flow": "flow", "compressor_frequency": "frequency",
    "heating_mode": "mode",
}
OPTIONAL_ROLES = {
    "backup_power": "power", "actual_target": "temperature",
    "import_power": "power", "export_power": "power", "pv_power": "power",
    "electricity_price": "price", "ventilation_power": "power",
    "stove_temperature": "temperature", "weather": "weather",
}
ROLES = REQUIRED_ROLES | OPTIONAL_ROLES
SETTINGS = {
    "gap_high": (6.0, 0, 15, .1, "°C"),
    "gap_low": (4.0, 0, 15, .1, "°C"),
    "room_margin": (.2, 0, 1, .1, "°C"),
    "room_recovery": (.4, 0, 2, .1, "°C"),
}
DEFAULT_PARAMETERS = {
    "curve_cold_outdoor": -10., "curve_cold_water": 40.,
    "curve_warm_outdoor": 15., "curve_warm_water": 28.,
    "target_floor": 27., "maximum_reduction": 4, "curve_rounding": "ceil",
    "capacity_kw": 8., "comparison_min_heat_kw": .5, "comparison_max_heat_kw": 9., "export_discount": .11,
    "space_mode": "Room", "dhw_mode": "Tank", "outlet_dhw_fallback": False,
    "price_format": "zonneplan", "price_attribute": "forecast",
    "price_start_key": "start", "price_end_key": "end", "price_value_key": "price",
    "pv_forecast_format": "forecast_solar", "pv_forecast_attribute": "forecast",
    "pv_forecast_start_key": "start", "pv_forecast_value_key": "power_kw",
}
# These are explicit example engineering estimates, not fitted to any user's home.
# Price optimization is opt-in and requires confirming a model in configuration.
DEFAULT_MODEL = {
    "confirmed": False, "heat_loss": .2, "solar_gain": 0.,
    "stove_gain": 0., "background_gain": 0.,
    "cop_b0": .4, "cop_b1": 0., "cop_b2": 0., "cop_b3": 0.,
    "cop_q_min": .5, "cop_q_max": 9.,
    "training_outdoor_min": -10., "training_outdoor_max": 15.,
    "training_water_min": 27., "training_water_max": 45.,
    "ventilation_heat_kw": 1.4, "ventilation_idle_w": 16.,
    "ventilation_full_w": 330., "ventilation_space_below": 4.,
    "stove_idle_c": 25., "stove_full_c": 100.,
}
SNAPSHOT_VERSION = 1
FORECAST_SECONDS = 1200
PV_HOLD_SECONDS = 600
MIN_STEP_SECONDS = 1500
WINDOW_SECONDS = 1800
MAX_SAMPLE_GAP = 90

