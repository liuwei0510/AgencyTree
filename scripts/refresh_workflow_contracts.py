#!/usr/bin/env python3
"""Expose existing, unambiguous ComfyUI fields in local workflow manifests."""
from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agentictree.catalog_contracts import discover_contract
from agentictree.storage import read_json


def refresh(root):
    changed = []
    for path in sorted(root.rglob('*.manifest.yaml')):
        data = yaml.safe_load(path.read_text())
        before = copy.deepcopy(data)
        graph = read_json(path.parent/data['workflow_file'])
        contract = discover_contract(graph, data.get('workflow_format','api'))
        inputs = {i['name']:i for i in data.get('inputs',[])}
        inject = data.setdefault('inject',{})
        for inp in contract['inputs']:
            name = inp['name']
            target = contract['inject'][name]
            if name not in inputs and any(str(v['node_id']) == target['node_id'] and v['input_key'] == target['input_key']
                                          for v in inject.values()):
                continue  # Preserve meaningful aliases such as edit_prompt.
            if name not in inputs:
                inputs[name] = inp
            if name not in inject:
                inject[name] = target
        data['inputs'] = list(inputs.values())
        observed = {o['name']:o['type'] for o in contract['outputs']}
        for output in data.get('outputs',[]):
            if output['name'] == 'video' and output['type'] == 'image_filenames' and observed.get('video') == 'video_path':
                output['type'] = 'video_path'
        # These two checked-in workflows select explicit fixed-scale RealESRGAN models.
        if 'nodes' not in graph:
            models = [n['inputs'].get('model_name') for n in graph.values()
                      if isinstance(n,dict) and n.get('class_type') == 'UpscaleModelLoader']
            fixed = {'RealESRGAN_x2.pth': 2, 'RealESRGAN_x4.pth': 4}
            if len(models) == 1 and models[0] in fixed:
                data.setdefault('fixed_constraints',{})['scale_factor'] = fixed[models[0]]
        if data != before:
            with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=path.parent,delete=False) as file:
                temporary = Path(file.name)
                file.write(yaml.safe_dump(data,allow_unicode=True,sort_keys=False))
            os.replace(temporary,path)
            changed.append(str(path.relative_to(root)))
    return changed


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=ROOT/'atomic_workflows')
    args = parser.parse_args()
    changed = refresh(args.root)
    print(json.dumps({'changed_count':len(changed),'manifests':changed},ensure_ascii=False,indent=2))
