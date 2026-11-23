"""Actual Home Assistant setup/config flow/entity/service lifecycle tests."""
import asyncio
from copy import deepcopy
from datetime import datetime,timezone
import json
from pathlib import Path
import shutil
from types import MappingProxyType
import pytest
from homeassistant.core import HomeAssistant, SupportsResponse
from homeassistant.config_entries import ConfigEntry,ConfigEntries,ConfigEntryState
from homeassistant.helpers.template import Template
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import device_registry as dr, area_registry as ar, frame
from homeassistant.setup import async_setup_component
from homeassistant import loader
from custom_components.heatpump_supplytemp_optimizer.const import CONFIG_MINOR_VERSION, DOMAIN


@pytest.fixture
async def hass(tmp_path):
    (tmp_path/'custom_components').mkdir()
    source=Path(__file__).parents[1]/'custom_components'/DOMAIN
    (tmp_path/'custom_components'/DOMAIN).symlink_to(source,target_is_directory=True)
    # Periodic loop wakeups also work in restricted runners where cross-thread
    # wakeup sockets cannot signal the selector reliably. Test-only, no HA patches.
    loop=asyncio.get_running_loop()
    def pulse():
        loop.call_later(.01,pulse)
    pulse()
    instance=HomeAssistant(str(tmp_path))
    loader.async_setup(instance)
    frame.async_setup(instance)
    instance.config_entries=ConfigEntries(instance,{})
    await asyncio.wait_for(instance.config_entries.async_initialize(),5)
    dr.async_setup(instance)
    await dr.async_load(instance)
    await ar.async_load(instance)
    await er.async_load(instance)
    yield instance
    for entry in instance.config_entries.async_entries(DOMAIN):
        if entry.state==ConfigEntryState.LOADED:
            await instance.config_entries.async_unload(entry.entry_id)
    await instance.async_stop(force=True)


def make_entry(config, *, options=None, minor_version=CONFIG_MINOR_VERSION):
    return ConfigEntry(domain=DOMAIN,title=config['name'],data=config,options=options or {},version=1,minor_version=minor_version,
        source='user',unique_id=None,discovery_keys=MappingProxyType({}),subentries_data=None)


async def seed(hass,config,states):
    for entity,state in states.items():
        hass.states.async_set(entity,state['state'],state['attributes'])
    now=datetime.now(timezone.utc).timestamp()
    q0=int(now//900)*900
    prices=[{'start':q0+i*900,'end':q0+(i+1)*900,'price':.4 if i==0 else .2} for i in range(25)]
    state=states[config['inputs']['electricity_price']]
    hass.states.async_set(config['inputs']['electricity_price'],'.4',dict(state['attributes'],forecast=prices))
    entity='sensor.demo_pv_forecast'
    config['pv_forecast_entities']=[entity]
    hass.states.async_set(entity,'ok',{'forecast':[{'start':q0+i*3600,'power_kw':.5} for i in range(8)]})
    async def weather(call):
        return {config['inputs']['weather']:{'forecast':[{'datetime':q0+i*3600,'temperature':5} for i in range(8)]}}
    hass.services.async_register('weather','get_forecasts',weather,supports_response=SupportsResponse.ONLY)
    await hass.async_block_till_done()


async def test_real_integration_setup_entities_and_read_only_forecast(hass,config,states):
    await seed(hass,config,states)
    entry=make_entry(config)
    await asyncio.wait_for(hass.config_entries.async_add(entry),10)
    await hass.async_block_till_done()
    assert entry.state==ConfigEntryState.LOADED,entry.reason
    registry=er.async_get(hass)
    entity=registry.async_get_entity_id('sensor',DOMAIN,entry.entry_id+'_effective_reduction')
    assert entity is not None
    assert hass.states.get(entity).state=='0'
    assert entry.runtime_data.engine.state['forecast']['complete']
    assert all(not name.startswith('input_') for name in {s.entity_id.split('.')[0] for s in hass.states.async_all()})


async def test_existing_entry_migrates_removed_flow_mapping(hass, config, states):
    await seed(hass, config, states)
    config["inputs"]["secondary_flow"] = "sensor.old_flow"
    configured = deepcopy(config)
    configured["rooms"][0]["max_shortfall_rise"] = 1
    entry = make_entry(config, options={"configuration": configured}, minor_version=1)
    await hass.config_entries.async_add(entry)
    await hass.async_block_till_done()

    assert entry.state == ConfigEntryState.LOADED
    assert entry.minor_version == CONFIG_MINOR_VERSION
    assert "secondary_flow" not in entry.data["inputs"]
    assert "secondary_flow" not in entry.options["configuration"]["inputs"]
    assert entry.runtime_data.engine.data["space_ok"]
    assert entry.runtime_data.engine.state["shortfall_rise_limits"]["room_a"] == 1


async def test_real_config_flow_import_and_options_room_editor(hass,config,states):
    await seed(hass,config,states)
    result=await hass.config_entries.flow.async_init(DOMAIN,context={'source':'user'})
    assert result['type']=='menu'
    flow_id=result['flow_id']
    result=await hass.config_entries.flow.async_configure(flow_id,{'next_step_id':'import_profile'})
    assert result['step_id']=='import_profile'
    result=await hass.config_entries.flow.async_configure(flow_id,{'profile_json':json.dumps(config)})
    assert result['type']=='create_entry'
    entry=result['result']
    await hass.async_block_till_done()
    options=await hass.config_entries.options.async_init(entry.entry_id)
    assert options['type']=='menu'
    options=await hass.config_entries.options.async_configure(options['flow_id'],{'next_step_id':'rooms'})
    assert options['step_id']=='rooms'
    options=await hass.config_entries.options.async_configure(options['flow_id'],{'next_step_id':'add_room'})
    options=await hass.config_entries.options.async_configure(options['flow_id'],{'name':'Room B','enabled':True,'allowance':.3,'actual':'sensor.demo_b','setpoint':'number.demo_b_target'})
    assert options['step_id']=='rooms' and options['type']=='menu'
    options=await hass.config_entries.options.async_configure(options['flow_id'],{'next_step_id':'finish_rooms'})
    options=await hass.config_entries.options.async_configure(options['flow_id'],{'next_step_id':'finish'})
    assert options['type']=='create_entry'
    await hass.async_block_till_done()
    assert len(entry.runtime_data.config['rooms'])==2


async def test_dashboard_export_resolves_real_ids_and_native_graphs(hass,config,states):
    await seed(hass,config,states)
    entry=make_entry(config)
    await asyncio.wait_for(hass.config_entries.async_add(entry),10)
    await hass.async_block_till_done()
    registry=er.async_get(hass)
    button=registry.async_get_entity_id('button',DOMAIN,entry.entry_id+'_export_dashboard')
    await hass.services.async_call('button','press',{'entity_id':button},blocking=True)
    path=Path(hass.config.path(f'{DOMAIN}_dashboard_{entry.entry_id}.yaml'))
    dashboard=json.loads(path.read_text())
    assert [view['path'] for view in dashboard['views']]==[
        'advice','price','efficiency','comfort','forecast','history','settings']
    for view in dashboard['views']:
        for section in view['sections']:
            for card in section['cards']:
                if view['path']!='forecast':assert not card['type'].startswith('custom:')
                if 'entity' in card:
                    assert registry.async_get(card['entity']) is not None
                for row in card.get('entities',[]):
                    assert registry.async_get(row['entity']) is not None


async def test_dashboard_explains_active_protections_and_limiting_room(hass, config, states):
    config["parameters"]["capacity_kw"] = 2
    config["inputs"]["defrost_state"] = "binary_sensor.hp_defrost"
    config["settings"]["minimum_defrost_interval"] = 50
    states["climate.demo_a"]["attributes"]["current_temperature"] = 19
    config["rooms"].append(dict(config["rooms"][0], id="unused", name="Excluded", enabled=False))
    await seed(hass, config, states)
    entry = make_entry(config)
    await hass.config_entries.async_add(entry)
    await hass.async_block_till_done()
    c = entry.runtime_data
    now = datetime.now(timezone.utc).timestamp()
    c.engine.state["defrost_starts"] = [now-1800, now-30]
    c.engine.decide(now)
    c.publish()
    registry = er.async_get(hass)
    button = registry.async_get_entity_id("button", DOMAIN, entry.entry_id+"_export_dashboard")
    await hass.services.async_call("button", "press", {"entity_id":button}, blocking=True)
    dashboard = json.loads(Path(hass.config.path(f"{DOMAIN}_dashboard_{entry.entry_id}.yaml")).read_text())
    cards = [card for view in dashboard["views"] for section in view["sections"] for card in section["cards"]]

    def render(card):
        return Template(card["content"], hass).async_render(parse_result=False)

    active = [card for card in cards if card["type"] == "conditional"]
    assert len(active) == 2
    assert all(hass.states.is_state(card["conditions"][0]["entity"], "on") for card in active)
    assert "2.0 kW" in render(active[0]["card"])
    assert "29.5 min" in render(active[1]["card"])
    room = render(next(card for card in cards if "limiting_rooms" in card.get("content", "")))
    assert "Room A" in room and "1.0 °C" in room and "Excluded" not in room
    timing = render(next(card for card in cards if "decision_at" in card.get("content", "")))
    assert "Evaluated" in timing and "Earliest" not in timing


async def test_switch_and_number_persist_across_unload_reload(hass,config,states):
    await seed(hass,config,states)
    entry=make_entry(config)
    await asyncio.wait_for(hass.config_entries.async_add(entry),10)
    await hass.async_block_till_done()
    registry=er.async_get(hass)
    number=registry.async_get_entity_id('number',DOMAIN,entry.entry_id+'_room_allowance_room_a')
    await hass.services.async_call('number','set_value',{'entity_id':number,'value':.7},blocking=True)
    rise_limit=registry.async_get_entity_id('number',DOMAIN,entry.entry_id+'_room_max_shortfall_rise_room_a')
    await hass.services.async_call('number','set_value',{'entity_id':rise_limit,'value':1.0},blocking=True)
    defrost_interval=registry.async_get_entity_id('number',DOMAIN,entry.entry_id+'_minimum_defrost_interval')
    await hass.services.async_call('number','set_value',{'entity_id':defrost_interval,'value':45},blocking=True)
    switch=registry.async_get_entity_id('switch',DOMAIN,entry.entry_id+'_enabled')
    await hass.services.async_call('switch','turn_on',{'entity_id':switch},blocking=True)
    decision = entry.runtime_data.engine.state["decision_at"]
    protections = [registry.async_get_entity_id("switch", DOMAIN, entry.entry_id+"_"+key)
                   for key in ("comfort_enabled", "defrost_enabled")]
    for entity in protections:
        assert hass.states.is_state(entity, "on")
        await hass.services.async_call("switch", "turn_off", {"entity_id": entity}, blocking=True)
    threshold = registry.async_get_entity_id("number", DOMAIN, entry.entry_id+"_comfort_threshold")
    await hass.services.async_call("number", "set_value", {"entity_id": threshold, "value": .8}, blocking=True)
    assert entry.runtime_data.engine.state["decision_at"] == decision
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.engine.state['enabled'] is True
    assert entry.runtime_data.engine.state['allowances']['room_a']==.7
    assert hass.states.get(number).state=='0.7'
    assert entry.runtime_data.engine.state['shortfall_rise_limits']['room_a']==1.0
    assert float(hass.states.get(rise_limit).state)==1.0
    assert float(hass.states.get(defrost_interval).state)==45
    assert all(hass.states.is_state(entity, 'off') for entity in protections)
    assert float(hass.states.get(threshold).state) == .8


async def test_state_and_parameter_updates_do_not_recalculate_between_boundaries(hass,config,states):
    await seed(hass,config,states)
    entry=make_entry(config)
    await hass.config_entries.async_add(entry)
    await hass.async_block_till_done()
    c=entry.runtime_data
    c.engine.state.update(target=33,effective_reduction=1,enabled=True)
    hass.states.async_set(config['inputs']['curve_outdoor'],'-5',{'unit_of_measurement':'°C'})
    await hass.async_block_till_done()
    assert c.engine.state['target']==33
    assert c.engine.state['effective_reduction']==1
    await c.async_set_number('gap_high',7)
    assert c.engine.state['target']==33
    assert c.engine.state['effective_reduction']==1
    await c.async_set_switch('enabled',False)
    assert c.engine.state['effective_reduction']==0


async def test_forecast_failure_recovers_without_changing_held_advice(hass,config,states):
    await seed(hass,config,states)
    entry=make_entry(config)
    await hass.config_entries.async_add(entry)
    await hass.async_block_till_done()
    c=entry.runtime_data
    healthy=dict(c.engine.state['forecast'])
    async def broken(call):
        raise ValueError('temporary provider failure')
    hass.services.async_register('weather','get_forecasts',broken,supports_response=SupportsResponse.ONLY)
    c.engine.forecast_attempt=0
    await c.async_refresh_forecast()
    assert not c.engine.state['forecast']['complete']
    assert 'forecast_provider_unavailable:weather' in c.engine.state['forecast']['problems']
    old_target=c.engine.state['target']
    await seed(hass,config,states)
    c.engine.forecast_attempt=0
    await c.async_refresh_forecast()
    assert c.engine.state['forecast']['complete']
    assert c.engine.state['target']==old_target


async def test_unconfirmed_model_blocks_price_enable(hass,config,states):
    from homeassistant.exceptions import HomeAssistantError
    config['model']['confirmed']=False
    await seed(hass,config,states)
    entry=make_entry(config)
    await hass.config_entries.async_add(entry)
    await hass.async_block_till_done()
    registry=er.async_get(hass)
    switch=registry.async_get_entity_id('switch',DOMAIN,entry.entry_id+'_price_enabled')
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call('switch','turn_on',{'entity_id':switch},blocking=True)
    assert entry.runtime_data.engine.state['price_enabled'] is False


async def test_manual_setup_flow_and_last_room_removal_validation(hass,config,states):
    from custom_components.heatpump_supplytemp_optimizer.config_flow import GROUPS
    config["inputs"]["defrost_state"] = "sensor.demo_defrost"
    states["sensor.demo_defrost"] = {"state": "Inactive", "attributes": {}}
    await seed(hass,config,states)
    result=await hass.config_entries.flow.async_init(DOMAIN,context={'source':'user'})
    flow_id=result['flow_id']
    async def advance(values):
        return await hass.config_entries.flow.async_configure(flow_id,values)
    result=await advance({'next_step_id':'setup'})
    result=await advance({'name':'Synthetic Optimizer'})
    for step in ['heat_pump','hydraulics','environment','electricity']:
        assert result['step_id']==step
        result=await advance({key:config['inputs'][key] for key in GROUPS[step] if key in config['inputs']})
    assert result['step_id']=='providers'
    result=await advance({'price_format':'generic','pv_forecast_format':'generic','pv_forecast_entities':config['pv_forecast_entities']})
    assert result['step_id']=='curve'
    fields=['curve_cold_outdoor','curve_cold_water','curve_warm_outdoor','curve_warm_water','target_floor','maximum_reduction','capacity_kw','export_discount','space_mode','dhw_mode','outlet_dhw_fallback']
    result=await advance({key:config['parameters'][key] for key in fields})
    assert result['step_id']=='model'
    result=await advance({'model_json':json.dumps(config['model'])})
    result=await advance({'next_step_id':'add_room'})
    result=await advance({'name':'Room A','enabled':True,'allowance':.1,'climate':'climate.demo_a'})
    assert result['step_id']=='rooms'
    result=await advance({'next_step_id':'finish_rooms'})
    assert result['type']=='create_entry'
    entry=result['result']
    await hass.async_block_till_done()
    assert entry.state==ConfigEntryState.LOADED
    assert entry.runtime_data.config["inputs"]["defrost_state"] == "sensor.demo_defrost"
    opts=await hass.config_entries.options.async_init(entry.entry_id)
    fid=opts['flow_id']
    opts=await hass.config_entries.options.async_configure(fid,{'next_step_id':'rooms'})
    opts=await hass.config_entries.options.async_configure(fid,{'next_step_id':'remove_room'})
    opts=await hass.config_entries.options.async_configure(fid,{'room':entry.runtime_data.config['rooms'][0]['id']})
    assert opts['type']=='form' and opts['errors']['base']=='invalid_configuration'



async def test_boundary_refreshes_previous_quarter_despite_recent_attempt(hass, config, states):
    await seed(hass, config, states)
    entry = make_entry(config)
    await hass.config_entries.async_add(entry)
    await hass.async_block_till_done()
    c = entry.runtime_data
    now = datetime.now(timezone.utc)
    old = c.engine.state["forecast"]
    old["quarters"][0]["start"] -= 900
    old.update(premium=.04, valid_until=now.timestamp()+1200)
    c.engine.forecast_attempt = now.timestamp()-15
    c.engine.state.update(enabled=True, price_enabled=True)
    assert c.engine.price_request(now.timestamp()) == 0
    async with c._forecast_lock:
        boundary = asyncio.create_task(c._boundary(now))
        await asyncio.sleep(0)
        assert not boundary.done()
    await asyncio.wait_for(boundary, 5)
    assert c.engine.state["forecast"] is not old
    assert c.engine.state["decision_price_reduction"] > 0
    current = c.engine.state["forecast"]
    await c._boundary(now)
    assert c.engine.state["forecast"] is current
