"""Read model and example leaves from the on-disk four-level ACT."""
from __future__ import annotations

from pathlib import Path
import yaml

from agentictree.catalog_contracts import discover_contract, validate_editor_graph
from agentictree.graph_validate import validate_api_graph
from agentictree.storage import digest, read_json, write_json


def scan_catalog_records(root: Path, store: Path) -> dict:
    records = {}
    current_parents = set()
    paths = sorted([*root.rglob('model.yaml'), *root.rglob('template.yaml')])
    for path in paths:
        data = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
        if data.get('kind') not in {'comfyui_model', 'comfyui_template'}:
            continue
        parts = path.relative_to(root).parts
        if len(parts) != 5:
            raise ValueError(f'Catalog leaf must follow L1/L2/L3/L4/file: {path}')
        key = str(data['id'])
        for prefix in ('remote.model.', 'remote.template.', 'remote.api_example.'):
            if key.startswith(prefix):
                key = prefix.replace('.', ':').rstrip(':') + ':' + key[len(prefix):]
                break
        record = {**data, 'id': key, 'tree_branch': list(parts[:3]),
                  'atomic_workflow_path': str(path.parent.relative_to(root)),
                  'evidence': {**data.get('evidence', data.get('source', {})), 'manifest': str(path.resolve())}}
        if data['kind'] == 'comfyui_model':
            from agentictree.comfy_models import comfyui_model_issues
            record['validation_errors'] = comfyui_model_issues(data.get('metadata', {}))
        current = data.get('status') != 'deprecated'
        if data['kind'] == 'comfyui_template' and current:
            workflow_path = path.parent / data['workflow_file']
            graph = read_json(workflow_path)
            fmt = data.get('format', 'editor' if 'nodes' in graph else 'api')
            contract = discover_contract(graph, fmt, data.get('metadata'))
            format_errors=validate_editor_graph(graph) if fmt=='editor' else validate_api_graph(graph)
            record['code_usable']=not format_errors
            record['validation_errors']=format_errors
            record['status']='unusable_format' if format_errors else 'available'
            sha = digest(graph)
            object_path = (store / 'objects' / f'{sha}.json').resolve()
            if not object_path.exists():
                write_json(object_path, graph)
            record.update(contract)
            record.update(workflow_format=fmt, workflow_sha256=sha, graph_object=str(object_path),
                          planning_eligible=not contract['contract_errors'],
                          modality=[o['type'] for o in contract['outputs']], tags=['official-comfyui'])
            record['six_tuple'] = {**record.get('six_tuple', {}),
                'input_types': {v['name']: v['type'] for v in contract['inputs']},
                'output_types': {v['name']: v['type'] for v in contract['outputs']},
                'parameters': contract['inputs'],
                'invocation': {'platform': 'ComfyUI', 'format': fmt, 'workflow_file': data['workflow_file']}}
            derived=('status','code_usable','validation_errors','planning_eligible','inputs','inject','outputs','six_tuple')
            payload={**data,**{k:record[k] for k in derived}}
            if payload != data:
                path.write_text(yaml.safe_dump(payload,allow_unicode=True,sort_keys=False),encoding='utf-8')
        previous = records.get(key)
        # A moved instance retains its retired path, but has one current parent.
        if current and key in current_parents:
            raise ValueError(f'Two available parents for ACT instance {key}')
        if current or key not in current_parents:
            records[key] = record
        if current:
            current_parents.add(key)
    return records
