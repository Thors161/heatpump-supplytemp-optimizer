"""Export an existing YAML/Jinja package to a PRIVATE local import profile.

Reads only local files. Output must be outside this repository. Never run this
against an untrusted Python file: no Python from the package is imported/executed.
"""
import argparse
import ast
import json
from pathlib import Path
import re
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def export(package_root,output,state_file=None):
    # Pure schema import without initializing the HA integration package.
    import importlib.util,types
    parent='profile_schema'
    module=types.ModuleType(parent)
    module.__path__=[str(ROOT/'custom_components/heatpump_optimizer')]
    sys.modules[parent]=module
    from importlib import import_module
    validate_config=import_module(parent+'.schema').validate_config
    package_root=Path(package_root).resolve()
    output=Path(output).resolve()
    if output==ROOT or ROOT in output.parents:
        raise ValueError('Private profiles must be written outside the public repository')
    templates=list((package_root/'custom_templates').glob('*.jinja'))
    if len(templates)!=1:
        raise ValueError('Expected the single source-package Jinja file')
    source=templates[0].read_text()
    generator=(package_root/'build.py').read_text()
    calibration=json.loads((package_root/'model-calibration.json').read_text())
    saved=json.loads(Path(state_file).read_text()) if state_file else {}
    variables=dict(re.findall(r"{% set (\w+)\s*=\s*states\('([^']+)'\)",source))
    mapping={'curve_outdoor':'curve_outdoor','local_outdoor':'outdoor','heat_power':'heat_kw',
        'electric_power':'hp_w','backup_power':'heater_w','supply_temperature':'hp_supply',
        'primary_return':'hp_return','secondary_return':'rad_return','primary_flow':'primary_flow',
        'secondary_flow':'rad_flow','compressor_frequency':'compressor_hz','heating_mode':'valve',
        'actual_target':'actual_target','import_power':'grid_in','export_power':'grid_out',
        'ventilation_power':'ecol_w','stove_temperature':'flue'}
    inputs={role:variables[var] for role,var in mapping.items() if var in variables}
    pv=re.search(r"resolve_pv\(states\('([^']+)'\)",source)
    weather=re.search(r"weather_temperature=state_attr\('([^']+)'",source)
    price=re.search(r"current_price=states\('([^']+)'",source)
    for role,match in [('pv_power',pv),('weather',weather),('electricity_price',price)]:
        if match:inputs[role]=match.group(1)
    zones=ast.literal_eval(re.search(r'{% set zones = (\[.*?\]) %}',source,re.S).group(1))
    names=ast.literal_eval(re.search(r'ROOM_ALLOWANCES\s*=\s*(\[.*?\])',generator,re.S).group(1))
    labels=dict(names)
    prefix_match=re.search(r"PREFIX\s*=\s*'([^']+)'",generator)
    prefix=prefix_match.group(1)
    rooms=[]
    for index,entity in enumerate(zones):
        slug=entity.split('.',1)[1]
        key='input_number.'+prefix+'room_allowance_'+slug
        rooms.append(dict(id='room_'+str(index+1),name=labels.get(slug,'Room '+str(index+1)),
                          climate=entity,allowance=float(saved.get(key,0))))
    m=calibration['daily_house_model_coefficients']
    model={'confirmed':True,'heat_loss':m['heat_loss_kw_per_k'],
        'solar_gain':m['solar_kw_per_pv_kw'],'stove_gain':m['effective_stove_kw_per_proxy'],
        'background_gain':m['background_gain_kw']}
    model.update(dict(zip(['cop_b0','cop_b1','cop_b2','cop_b3'],calibration['coefficients'])))
    ranges=calibration['training_ranges']
    for output_key,input_key in [('outdoor','outdoor_c'),('water','water_target_c')]:
        model['training_'+output_key+'_min'],model['training_'+output_key+'_max']=ranges[input_key]
    model['cop_q_min'],model['cop_q_max']=ranges['heat_kw']
    cop_bounds=re.search(r'set q = \[([\d.]+), \[([\d.]+), heat_kw\]',source)
    if cop_bounds:model['cop_q_min'],model['cop_q_max']=map(float,cop_bounds.groups())
    ventilation_calibration=re.search(r'\(data.ecol_w-([\d.]+)\)/\(([\d.]+)-',source)
    if ventilation_calibration:model['ventilation_idle_w'],model['ventilation_full_w']=map(float,ventilation_calibration.groups())
    curve=re.search(r'original\s+([\d.]+)@(-?[\d.]+)\s*/\s*([\d.]+)@(-?[\d.]+)',source)
    if not curve:
        raise ValueError('Cannot infer the old heating curve; enter it manually')
    cold_water,cold_out,warm_water,warm_out=map(float,curve.groups())
    parameters={'curve_cold_outdoor':cold_out,'curve_cold_water':cold_water,
                'curve_warm_outdoor':warm_out,'curve_warm_water':warm_water,'curve_rounding':'ceil'}
    settings={}
    for old,new in [('efficiency_gap_high','gap_high'),('efficiency_gap_low','gap_low'),
                    ('efficiency_room_margin','room_margin'),('efficiency_room_recovery','room_recovery')]:
        key='input_number.'+prefix+old
        if key in saved:settings[new]=float(saved[key])
    config=validate_config(dict(name='Heat Pump Supply Temperature Optimizer',inputs=inputs,rooms=rooms,
        model=model,parameters=parameters,settings=settings,
        pv_forecast_entities=list(dict.fromkeys(re.findall(r"config_entry_id\('([^']+)'\)",generator)))))
    output.write_text(json.dumps(config,indent=2,ensure_ascii=False)+'\n')
    output.chmod(0o600)
    return len(rooms)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package-root',required=True)
    parser.add_argument('--output',required=True)
    parser.add_argument('--state-file',help='Optional JSON entity-id -> helper-value map, kept outside the repository')
    args=parser.parse_args()
    print(f'Exported {export(args.package_root,args.output,args.state_file)} rooms to a private local profile. Do not commit or publish it.')

