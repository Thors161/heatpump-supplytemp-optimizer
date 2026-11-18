"""Build a manual-install ZIP containing only the integration runtime."""
import json
from pathlib import Path
import zipfile
from check_repository import ROOT,check


if __name__=='__main__':
    check()
    destination=ROOT/'dist'
    destination.mkdir(exist_ok=True)
    runtime=ROOT/'custom_components/heatpump_supplytemp_optimizer'
    path=destination/'heatpump_supplytemp_optimizer.zip'
    with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as archive:
        for file in sorted(runtime.rglob('*')):
            if file.is_file() and file.suffix in ['.py','.json','.png','.svg']:
                archive.write(file,file.relative_to(ROOT))
        archive.write(ROOT/'LICENSE','custom_components/heatpump_supplytemp_optimizer/LICENSE')
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None
        assert all(n.startswith('custom_components/heatpump_supplytemp_optimizer/') for n in archive.namelist())
    print(json.dumps({'archive':str(path),'bytes':path.stat().st_size}))

