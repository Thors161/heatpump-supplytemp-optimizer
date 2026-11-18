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
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import device_registry as dr, area_registry as ar, frame
from homeassistant.setup import async_setup_component
from homeassistant import loader
from custom_components.heatpump_supplytemp_optimizer.const import DOMAIN


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


def make_entry(config):
    return ConfigEntry(domain=DOMAIN,title=config['name'],data=config,options={},version=1,minor_version=1,
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
    assert len(dashboard['views'])==6
    for view in dashboard['views']:
        for section in view['sections']:
            for card in section['cards']:
                if view['path']!='forecast':assert not card['type'].startswith('custom:')
                for row in card.get('entities',[]):
                    assert registry.async_get(row['entity']) is not None


async def test_switch_and_number_persist_across_unload_reload(hass,config,states):
    await seed(hass,config,states)
    entry=make_entry(config)
    await asyncio.wait_for(hass.config_entries.async_add(entry),10)
    await hass.async_block_till_done()
    registry=er.async_get(hass)
    number=registry.async_get_entity_id('number',DOMAIN,entry.entry_id+'_room_allowance_room_a')
    await hass.services.async_call('number','set_value',{'entity_id':number,'value':.7},blocking=True)
    switch=registry.async_get_entity_id('switch',DOMAIN,entry.entry_id+'_enabled')
    await hass.services.async_call('switch','turn_on',{'entity_id':switch},blocking=True)
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.engine.state['enabled'] is True
    assert entry.runtime_data.engine.state['allowances']['room_a']==.7
    assert hass.states.get(number).state=='0.7'


async def test_state_and_parameter_updates_do_not_recalculate_between_boundaries(hass,config,states):
    await seed(hass,config,states)
    entry=make_entry(config)
    await hass.config_entries.async_add(entry)
    await hass.async_block_till_done()
    c=entry.runtime_data
    c.engine.state.update(target=33,effective_reduction=1,reduction=1,enabled=True)
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
    opts=await hass.config_entries.options.async_init(entry.entry_id)
    fid=opts['flow_id']
    opts=await hass.config_entries.options.async_configure(fid,{'next_step_id':'rooms'})
    opts=await hass.config_entries.options.async_configure(fid,{'next_step_id':'remove_room'})
    opts=await hass.config_entries.options.async_configure(fid,{'room':entry.runtime_data.config['rooms'][0]['id']})
    assert opts['type']=='form' and opts['errors']['base']=='invalid_configuration'

