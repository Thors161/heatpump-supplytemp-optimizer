"""Diagnostics deliberately exclude entity IDs, room names and raw history."""
from .const import VERSION


async def async_get_config_entry_diagnostics(hass, entry):
    c=entry.runtime_data
    e=c.engine
    return {"version":VERSION,"room_count":len(c.config["rooms"]),
            "input_roles":sorted(c.config["inputs"]),
            "enabled":e.state["enabled"],"forecast_status":e.state["forecast"].get("status","waiting"),
            "forecast_problem_count":len(e.state["forecast"].get("problems",[])),
            "hydraulics_ready":e.hydraulics_ready,"observations_ready":e.observations_ready,
            "reduction":e.state["effective_reduction"],"efficiency":e.efficiency_addition,
            "model_confirmed":c.config["model"]["confirmed"]}

