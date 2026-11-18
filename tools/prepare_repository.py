"""Set real GitHub metadata after the account/organization is known."""
import argparse
import json
from pathlib import Path
import re


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('owner',help='GitHub user or organization; never a token')
    parser.add_argument('--maintainer',help='GitHub user for codeowners, if owner is an organization')
    args=parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})',args.owner):
        parser.error('Invalid GitHub owner')
    root=Path(__file__).resolve().parents[1]
    path=root/'custom_components/heatpump_supplytemp_optimizer/manifest.json'
    manifest=json.loads(path.read_text())
    url=f'https://github.com/{args.owner}/heatpump-supplytemp-optimizer'
    manifest.update(documentation=url,issue_tracker=url+'/issues',codeowners=['@'+(args.maintainer or args.owner)])
    path.write_text(json.dumps(manifest,indent=2)+'\n')
    print('Repository metadata set. Run tests and release checks before publishing.')

