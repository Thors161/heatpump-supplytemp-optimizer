"""Structural/release/privacy checks without network or Home Assistant imports."""
import argparse
import ast
import json
import os
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[1]


def check(release=False,private_profile=None):
    components=ROOT/'custom_components'
    integrations=[p for p in components.iterdir() if p.is_dir() and (p/'manifest.json').is_file()]
    assert len(integrations)==1,'Exactly one custom integration is required'
    path=integrations[0]
    manifest=json.loads((path/'manifest.json').read_text())
    assert path.name==manifest['domain']=='heatpump_supplytemp_optimizer'
    assert manifest['config_flow'] is True
    assert manifest['version']=='2.0.0-beta.7'
    assert json.loads((ROOT/'hacs.json').read_text())['homeassistant']=='2026.11.0'
    for name in ['__init__.py','config_flow.py','sensor.py','binary_sensor.py','switch.py','number.py','button.py','strings.json','translations/en.json']:
        assert (path/name).is_file(),name
    parsed=json.loads((path/'strings.json').read_text())
    assert parsed==json.loads((path/'translations/en.json').read_text())
    for file in path.rglob('*.py'):
        ast.parse(file.read_text(),filename=str(file))
    pending=not manifest['codeowners'] or 'REPOSITORY_OWNER' in manifest['documentation']
    if release:
        assert not pending,'Set GitHub owner with tools/prepare_repository.py before publishing'
        tag=os.environ.get('GITHUB_REF_NAME','')
        if tag.startswith('v'):assert tag[1:]==manifest['version'],'Tag and manifest version differ'
    scanned=0
    if private_profile:
        profile=json.loads(Path(private_profile).read_text())
        tokens=set(profile['inputs'].values())|set(profile.get('pv_forecast_entities',[]))
        for room in profile['rooms']:
            tokens.update(room[k] for k in ['climate','actual','setpoint'] if room.get(k))
            if len(room['name'])>=4:tokens.add(room['name'])
        tokens.update(str(v) for v in profile['model'].values() if isinstance(v,float) and len(str(v))>12)
        for file in ROOT.rglob('*'):
            if not file.is_file() or any(part in ['.git','__pycache__','.pytest_cache','dist'] for part in file.parts):continue
            if file.suffix in ['.pyc','.zip']:continue
            text=file.read_text()
            assert not any(token in text for token in tokens),'Private configuration found in '+str(file.relative_to(ROOT))
            scanned+=1
    return {'structure_valid':True,'repository_metadata_pending':pending,'private_scan_files':scanned}


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--release',action='store_true')
    parser.add_argument('--private-profile',help='Optional private profile kept outside this repository')
    args=parser.parse_args()
    print(json.dumps(check(args.release,args.private_profile),indent=2))

