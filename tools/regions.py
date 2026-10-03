#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path
roster = json.loads(Path(os.environ.get('REGION_ROSTER_FILE', 'deploy/regions.json')).read_text())['region']
wanted = sys.argv[1].split()
if wanted:
    allowed = {r['region']: r for r in roster}
    if len(set(wanted)) != len(wanted) or not set(wanted) <= set(allowed):
        raise ValueError('diagnostic regions must be unique production roster entries')
    roster = [allowed[r] for r in wanted]
with open(os.environ['GITHUB_OUTPUT'], 'a') as f:
    print('matrix=' + json.dumps({'region': roster}), file=f)
    print('count=' + str(len(roster)), file=f)
    print('production=' + str(not wanted).lower(), file=f)
