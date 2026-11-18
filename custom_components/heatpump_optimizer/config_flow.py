"""UI setup, reconfiguration and a dynamic room editor."""
from copy import deepcopy
import json
from uuid import uuid4
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import selector
from .const import DOMAIN, DEFAULT_MODEL, DEFAULT_PARAMETERS, REQUIRED_ROLES
from .schema import validate_config


GROUPS = {
    "heat_pump": ["heat_power", "electric_power", "backup_power", "supply_temperature", "primary_return", "primary_flow", "compressor_frequency", "heating_mode", "actual_target"],
    "hydraulics": ["secondary_return", "secondary_flow"],
    "environment": ["curve_outdoor", "local_outdoor", "weather", "ventilation_power", "stove_temperature"],
    "electricity": ["import_power", "export_power", "pv_power", "electricity_price"],
}


class ConfigurationWizard:
    is_options = False

    def _begin(self, config=None):
        self.working = deepcopy(dict(config) if config else {"name": "Heat Pump Supply Temperature Optimizer", "inputs": {},
            "rooms": [], "parameters": dict(DEFAULT_PARAMETERS), "model": dict(DEFAULT_MODEL)})

    def _entity_schema(self, roles):
        schema = {}
        for role in roles:
            entity = self.working.get("inputs", {}).get(role)
            marker = vol.Required if role in REQUIRED_ROLES else vol.Optional
            key = marker(role, default=entity) if entity else marker(role)
            domain = "weather" if role == "weather" else ["sensor", "binary_sensor", "input_select", "select"] if role == "heating_mode" else ["sensor", "input_number", "number"]
            schema[key] = selector.EntitySelector(selector.EntitySelectorConfig(domain=domain))
        return vol.Schema(schema)

    async def _entities(self, step, user_input, next_step):
        if user_input is not None:
            # Omitted optional selectors remove the previous mapping.
            for role in GROUPS[step]:
                self.working["inputs"].pop(role, None)
            self.working["inputs"].update({k: v for k, v in user_input.items() if v})
            return await getattr(self, "async_step_"+next_step)()
        return self.async_show_form(step_id=step, data_schema=self._entity_schema(GROUPS[step]))

    async def async_step_heat_pump(self, user_input=None):
        return await self._entities("heat_pump", user_input, "hydraulics")

    async def async_step_hydraulics(self, user_input=None):
        return await self._entities("hydraulics", user_input, "environment")

    async def async_step_environment(self, user_input=None):
        return await self._entities("environment", user_input, "electricity")

    async def async_step_electricity(self, user_input=None):
        return await self._entities("electricity", user_input, "providers")

    async def async_step_providers(self, user_input=None):
        if user_input is not None:
            self.working["pv_forecast_entities"] = user_input.get("pv_forecast_entities", [])
            self.working["parameters"].update(price_format=user_input["price_format"], pv_forecast_format=user_input["pv_forecast_format"])
            return await (self.async_step_init() if self.is_options else self.async_step_curve())
        return self.async_show_form(step_id="providers", data_schema=vol.Schema({
            vol.Required("price_format", default=self.working["parameters"].get("price_format", "zonneplan")): selector.SelectSelector(selector.SelectSelectorConfig(options=["zonneplan", "generic"])),
            vol.Required("pv_forecast_format", default=self.working["parameters"].get("pv_forecast_format", "forecast_solar")): selector.SelectSelector(selector.SelectSelectorConfig(options=["forecast_solar", "generic"])),
            vol.Optional("pv_forecast_entities", default=self.working.get("pv_forecast_entities", [])): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor", multiple=True))}))

    async def async_step_curve(self, user_input=None):
        errors = {}
        if user_input is not None:
            candidate = deepcopy(self.working)
            candidate["parameters"].update(user_input)
            try:
                self._validate_partial(candidate)
                self.working = candidate
                return await (self.async_step_init() if self.is_options else self.async_step_model())
            except ValueError:
                errors["base"] = "invalid_configuration"
        fields = {}
        for key in ["curve_cold_outdoor", "curve_cold_water", "curve_warm_outdoor", "curve_warm_water", "target_floor", "maximum_reduction", "capacity_kw", "export_discount"]:
            fields[vol.Required(key, default=self.working["parameters"][key])] = selector.NumberSelector(selector.NumberSelectorConfig(mode=selector.NumberSelectorMode.BOX, step="any"))
        fields[vol.Required("space_mode", default=self.working["parameters"]["space_mode"])] = selector.TextSelector()
        fields[vol.Required("dhw_mode", default=self.working["parameters"]["dhw_mode"])] = selector.TextSelector()
        fields[vol.Required("outlet_dhw_fallback", default=self.working["parameters"]["outlet_dhw_fallback"])] = selector.BooleanSelector()
        return self.async_show_form(step_id="curve", data_schema=vol.Schema(fields),errors=errors)

    def _validate_partial(self, candidate):
        check=deepcopy(candidate)
        if not check.get("rooms"):
            check["rooms"]=[{"id":"validation","name":"Validation","climate":"climate.validation"}]
        validate_config(check)

    async def async_step_model(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                model = json.loads(user_input["model_json"])
                if not isinstance(model, dict) or set(model)-set(DEFAULT_MODEL):
                    raise ValueError
                candidate=deepcopy(self.working)
                candidate["model"] = dict(DEFAULT_MODEL) | model
                self._validate_partial(candidate)
                self.working=candidate
                return await (self.async_step_init() if self.is_options else self.async_step_rooms())
            except (ValueError, TypeError):
                errors["base"] = "invalid_json"
        return self.async_show_form(step_id="model", errors=errors, data_schema=vol.Schema({
            vol.Required("model_json", default=json.dumps(self.working["model"], indent=2)): selector.TextSelector(selector.TextSelectorConfig(multiline=True))}))

    async def async_step_advanced(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                parameters = json.loads(user_input["parameters_json"])
                if not isinstance(parameters, dict) or set(parameters)-set(DEFAULT_PARAMETERS):
                    raise ValueError
                candidate=deepcopy(self.working)
                candidate["parameters"] = dict(DEFAULT_PARAMETERS) | parameters
                self._validate_partial(candidate)
                self.working=candidate
                return await self.async_step_init()
            except (ValueError, TypeError):
                errors["base"] = "invalid_json"
        return self.async_show_form(step_id="advanced", errors=errors, data_schema=vol.Schema({
            vol.Required("parameters_json", default=json.dumps(self.working["parameters"], indent=2)): selector.TextSelector(selector.TextSelectorConfig(multiline=True))}))

    async def async_step_rooms(self, user_input=None):
        choices = ["add_room"]
        if self.working.get("rooms"):
            choices += ["edit_room", "remove_room"]
        choices += ["finish_rooms"]
        return self.async_show_menu(step_id="rooms", menu_options=choices)

    async def async_step_finish_rooms(self, user_input=None):
        return await (self.async_step_init() if self.is_options else self.async_step_finish())

    async def async_step_add_room(self, user_input=None):
        return await self._room_form("add_room", user_input)

    async def _room_form(self, step, user_input, room=None):
        errors = {}
        room = room or {"id": uuid4().hex, "name": "Room", "enabled": True, "allowance": 0.}
        if user_input is not None:
            updated = dict(user_input, id=room["id"])
            candidate = deepcopy(self.working)
            candidate["rooms"] = [r for r in candidate.get("rooms", []) if r["id"] != updated["id"]]+[updated]
            try:
                validate_config(candidate)
                self.working = candidate
                return await self.async_step_rooms()
            except ValueError as err:
                errors["base"] = "invalid_configuration"
                self._validation_detail = str(err)
        fields = {
            vol.Required("name", default=room["name"]): selector.TextSelector(),
            vol.Required("enabled", default=room["enabled"]): selector.BooleanSelector(),
            vol.Required("allowance", default=room["allowance"]): selector.NumberSelector(selector.NumberSelectorConfig(min=0, max=2, step=.1, mode=selector.NumberSelectorMode.BOX)),
        }
        for key, domain in [("climate", "climate"), ("actual", ["sensor", "number", "input_number"]), ("setpoint", ["sensor", "number", "input_number"])]:
            marker = vol.Optional(key, default=room[key]) if room.get(key) else vol.Optional(key)
            fields[marker] = selector.EntitySelector(selector.EntitySelectorConfig(domain=domain))
        return self.async_show_form(step_id=step, data_schema=vol.Schema(fields), errors=errors,
                                    description_placeholders={"detail": getattr(self, "_validation_detail", "")})

    def _room_selection(self):
        return vol.Schema({vol.Required("room"): selector.SelectSelector(selector.SelectSelectorConfig(
            options=[selector.SelectOptionDict(value=r["id"], label=r["name"]) for r in self.working["rooms"]]))})

    async def async_step_edit_room(self, user_input=None):
        if user_input is not None:
            self.editing = next(r for r in self.working["rooms"] if r["id"] == user_input["room"])
            return await self.async_step_room_details()
        return self.async_show_form(step_id="edit_room", data_schema=self._room_selection())

    async def async_step_room_details(self, user_input=None):
        return await self._room_form("room_details", user_input, self.editing)

    async def async_step_remove_room(self, user_input=None):
        errors={}
        if user_input is not None:
            candidate=deepcopy(self.working)
            candidate["rooms"] = [r for r in candidate["rooms"] if r["id"] != user_input["room"]]
            try:
                validate_config(candidate)
                self.working=candidate
                return await self.async_step_rooms()
            except ValueError:
                errors["base"]="invalid_configuration"
        return self.async_show_form(step_id="remove_room", data_schema=self._room_selection(),errors=errors)

    async def async_step_finish(self, user_input=None):
        try:
            config = validate_config(self.working)
        except ValueError as err:
            return self.async_show_form(step_id="finish", errors={"base": "invalid_configuration"},
                description_placeholders={"detail": str(err)}, data_schema=vol.Schema({}))
        return self.async_create_entry(title="" if self.is_options else config["name"],
                                      data={"configuration": config} if self.is_options else config)


class OptimizerConfigFlow(ConfigurationWizard, config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        self._begin()
        return self.async_show_menu(step_id="user", menu_options=["setup", "import_profile"])

    async def async_step_setup(self, user_input=None):
        if user_input is not None:
            self.working["name"] = user_input["name"]
            return await self.async_step_heat_pump()
        return self.async_show_form(step_id="setup", data_schema=vol.Schema({
            vol.Required("name", default=self.working["name"]): selector.TextSelector()}))

    async def async_step_import_profile(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                config = validate_config(json.loads(user_input["profile_json"]))
                return self.async_create_entry(title=config["name"], data=config)
            except (ValueError, TypeError):
                errors["base"] = "invalid_configuration"
        return self.async_show_form(step_id="import_profile", errors=errors, data_schema=vol.Schema({
            vol.Required("profile_json"): selector.TextSelector(selector.TextSelectorConfig(multiline=True))}))

    @staticmethod
    def async_get_options_flow(config_entry):
        return OptimizerOptionsFlow()


class OptimizerOptionsFlow(ConfigurationWizard, config_entries.OptionsFlowWithReload):
    is_options = True

    async def async_step_init(self, user_input=None):
        if not hasattr(self, "working"):
            self._begin(self.config_entry.options.get("configuration", self.config_entry.data))
            runtime=getattr(self.config_entry,"runtime_data",None)
            if runtime is not None:
                self.working["settings"]=dict(runtime.engine.state["settings"])
                for room in self.working["rooms"]:
                    room["allowance"]=runtime.engine.state["allowances"].get(room["id"],room["allowance"])
        return self.async_show_menu(step_id="init", menu_options=["heat_pump", "rooms", "curve", "model", "advanced", "finish"])

