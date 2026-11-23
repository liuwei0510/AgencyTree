"""Discover callable interfaces from stored ComfyUI artifacts, without a server."""
from __future__ import annotations

import copy
from pathlib import Path

from agentictree.editor_to_api import _object_info_widget_names, _SEED_CONTROL_VALUES
from agentictree.graph_validate import validate_api_graph

MODEL_FIELDS = {'ckpt_name', 'unet_name', 'vae_name', 'lora_name', 'clip_name', 'clip_name1', 'clip_name2',
                'model_name', 'control_net_name', 'upscale_model', 'clip_vision'}


def validate_editor_graph(graph: dict) -> list[str]:
    if not isinstance(graph, dict) or not isinstance(graph.get('nodes'), list) or not graph['nodes']:
        return ['Expected a nonempty ComfyUI editor graph']
    ids = [str(n.get('id')) for n in graph['nodes'] if isinstance(n, dict)]
    errors = []
    if len(ids) != len(graph['nodes']) or len(set(ids)) != len(ids) or 'None' in ids:
        errors.append('Editor nodes must have distinct IDs')
    if any(not isinstance(n.get('type'), str) for n in graph['nodes'] if isinstance(n, dict)):
        errors.append('Editor node type is missing')
    for link in graph.get('links') or []:
        if isinstance(link, list) and len(link) >= 5:
            source, target = str(link[1]), str(link[3])
        elif isinstance(link, dict):
            source, target = str(link.get('origin_id')), str(link.get('target_id'))
        else:
            errors.append('Malformed editor link')
            continue
        if source not in ids or target not in ids:
            errors.append(f'Dangling editor link {source} -> {target}')
    return errors


def node_fields(graph: dict, fmt: str):
    """Yield only field names supported by API keys or unambiguous editor widgets."""
    if fmt == 'api':
        for node_id, node in graph.items():
            if isinstance(node, dict) and 'class_type' in node:
                for key, value in node.get('inputs', {}).items():
                    if not isinstance(value, list):
                        yield str(node_id), node['class_type'], key, value, None, node.get('_meta', {}).get('title', '')
        return
    for node in graph.get('nodes', []):
        if node.get('mode') in (2, 4):
            continue
        cls = (node.get('properties') or {}).get('Node name for S&R') or node.get('type', '')
        values = node.get('widgets_values') or []
        if not isinstance(values, list):
            continue
        explicit = [x['name'] for x in node.get('inputs', []) if x.get('widget') and x.get('link') is None]
        names = explicit if len(explicit) == len(values) else _object_info_widget_names(cls, node, None)
        indices = list(range(len(values)))
        if cls in {'KSampler', 'KSamplerAdvanced'} and len(values) > 1 and isinstance(values[1], str) and values[1] in _SEED_CONTROL_VALUES:
            indices.pop(1)
        if cls in {'LoadImage', 'LoadImageMask'} and names and len(indices) == len(names) + 1:
            indices = indices[:len(names)]
        if names is None or len(names) != len(indices):
            continue
        for name, index in zip(names, indices):
            yield str(node['id']), cls, name, values[index], index, node.get('title', '')


def model_references(graph: dict, fmt: str) -> list[dict]:
    refs = []
    def walk(current, path):
        for nid, cls, key, value, index, title in node_fields(current, fmt):
            if key in MODEL_FIELDS and isinstance(value, str) and value:
                refs.append({'node_id': nid, 'input_key': key, 'widget_index': index, 'filename': value,
                             'loader_class': cls, 'graph_path':path})
        if fmt != 'editor':
            return
        used={node.get('type') for node in current.get('nodes',[]) if node.get('mode') not in (2,4)}
        for node in current.get('nodes',[]):
            if node.get('mode') in (2,4):
                continue
            for model in (node.get('properties') or {}).get('models',[]) or []:
                filename=model.get('name') if isinstance(model,dict) else None
                if filename and not any(r['node_id']==str(node['id']) and r['filename']==filename and r['graph_path']==path for r in refs):
                    refs.append({'node_id':str(node['id']), 'input_key':None,'widget_index':None,
                                 'filename':filename,'loader_class':node.get('type'),'graph_path':path,
                                 'contract_incomplete':True})
        subgraphs=(current.get('definitions') or {}).get('subgraphs') or []
        if isinstance(subgraphs,list):
            for index,child in enumerate(subgraphs):
                if child.get('id') in used:
                    walk(child,[*path,'definitions','subgraphs',index])
    walk(graph,[])
    return refs


def discover_contract(graph: dict, fmt: str, metadata: dict | None = None) -> dict:
    """Extract explicit mutable parameters; catalog availability stays independent."""
    metadata = metadata or {}
    errors = validate_editor_graph(graph) if fmt == 'editor' else validate_api_graph(graph)
    inputs, inject, outputs = [], {}, []
    counts = {}
    successors = {}
    if fmt == 'api':
        for target, node in graph.items():
            if isinstance(node, dict):
                for name, value in node.get('inputs', {}).items():
                    if isinstance(value, list) and len(value)==2 and isinstance(value[0],str):
                        successors.setdefault(value[0], []).append((str(target),name))
    else:
        nodes={str(n['id']):n for n in graph.get('nodes',[]) if isinstance(n,dict) and 'id' in n}
        for link in graph.get('links') or []:
            if isinstance(link,list) and len(link)>=5:
                target=nodes.get(str(link[3]),{})
                slots=target.get('inputs') or []
                name=slots[link[4]].get('name','') if isinstance(link[4],int) and 0<=link[4]<len(slots) else ''
                successors.setdefault(str(link[1]),[]).append((str(link[3]),name))
    def negative_path(start):
        queue=[(start,0)]
        seen=set()
        while queue:
            node,depth=queue.pop(0)
            if node in seen or depth>8:
                continue
            seen.add(node)
            for target,name in successors.get(node,[]):
                if name=='negative':
                    return True
                if name!='positive':
                    queue.append((target,depth+1))
        return False
    for nid, cls, key, value, index, title in node_fields(graph, fmt):
        logical, typ, required = None, 'string', True
        if key in {'prompt', 'positive_prompt'} or (key == 'text' and ('TextEncode' in cls or 'Prompt' in cls)):
            logical = 'negative_prompt' if 'negative' in title.casefold() or negative_path(nid) else 'prompt'
            required = logical == 'prompt'
        elif key in {'negative_prompt', 'negative'}:
            logical, required = 'negative_prompt', False
        elif cls in {'LoadImage', 'LoadImageMask', 'LoadAudio', 'LoadVideo', 'VHS_LoadVideo'} and key in {'image', 'audio', 'video', 'file'}:
            logical, typ = {'LoadImage': ('source_image', 'image_filenames'), 'LoadImageMask': ('mask_file','mask_path'),
                            'LoadAudio': ('audio_file', 'audio_path')}.get(cls, ('video_file', 'video_path'))
        elif key in {'width', 'height', 'seed', 'noise_seed', 'fps', 'frame_rate', 'length', 'duration', 'frames',
                     'num_frames', 'scale_by', 'scale_factor', 'upscale_factor'} and isinstance(value, (int, float)):
            logical = 'scale_factor' if key in {'scale_by', 'upscale_factor'} else 'fps' if key == 'frame_rate' else key
            typ, required = 'number', False
        elif key in MODEL_FIELDS and isinstance(value, str) and ('REPLACE_' in value or 'TODO' in value):
            logical = 'model_file'
        if logical is None:
            continue
        counts[logical] = counts.get(logical, 0) + 1
        name = logical if counts[logical] == 1 else f'{logical}_{counts[logical]}'
        inject[name] = {'node_id': nid, 'input_key': key, 'widget_index': index}
        inputs.append({'name': name, 'type': typ, 'required': required,
                       'default': value if not required else None})
    media = {x.get('mediaType') for x in (metadata.get('io') or {}).get('outputs', [])}
    if media.intersection({'model','3d','model3d','glb','gltf'}):
        media.add('mesh')
    classes = {n.get('class_type', n.get('type', '')) for n in
               (graph.get('nodes', []) if fmt == 'editor' else graph.values()) if isinstance(n, dict)}
    for match, name, typ in [('image', 'images', 'image_filenames'), ('video', 'video', 'video_path'),
                             ('audio', 'audio', 'audio_path'), ('mesh', 'mesh', 'mesh_path'), ('text', 'transcript', 'text')]:
        saves = {'image': ('SaveImage', 'PreviewImage'), 'video': ('SaveVideo', 'SaveAnimatedWEBP', 'VHS_VideoCombine'),
                 'audio': ('SaveAudio', 'SaveAudioMP3'), 'mesh': ('SaveGLB', 'Save3D'), 'text': ('SaveText', 'PreviewText')}
        if match in media or classes.intersection(saves[match]):
            outputs.append({'name': name, 'type': typ})
    if not outputs:
        errors.append('No explicit output interface could be extracted')
    return {'inputs': inputs, 'inject': inject, 'outputs': outputs,
            'model_references': model_references(graph, fmt), 'contract_errors': errors}


def resolve_model_reference(ref: dict, records: dict) -> list[str]:
    filename=ref['filename'].replace('\\','/')
    matches=[]
    kind_by_field={'ckpt_name':{'checkpoint'}, 'unet_name':{'diffusion_model'},
                   'vae_name':{'vae','taesd'}, 'lora_name':{'lora','motion lora'},
                   'control_net_name':{'controlnet'}, 'clip_name':{'clip'},
                   'clip_name1':{'clip'}, 'clip_name2':{'clip'}}
    allowed=kind_by_field.get(ref['input_key'])
    for key,record in records.items():
        if record.get('kind')!='comfyui_model' or record.get('status')!='available':
            continue
        metadata=record.get('metadata',{})
        if allowed and str(metadata.get('type','')).casefold() not in allowed:
            continue
        target=metadata.get('filename','')
        full=str(metadata.get('save_path','')).rstrip('/')+'/'+target
        if target==filename or ('/' in filename and full.endswith('/'+filename)):
            matches.append(key)
    return matches


def bind_models(graphs: dict, plans: dict, records: dict) -> dict:
    models = {k:v for k,v in records.items() if v.get('kind') == 'comfyui_model' and v.get('status') == 'available'}
    bindings = {}
    for node_id, graph in graphs.items():
        fmt = plans.get(node_id, 'api')
        bindings[node_id] = []
        for ref in model_references(graph, fmt):
            matches = resolve_model_reference(ref,models)
            bindings[node_id].append({**ref, 'model_id': matches[0] if len(matches) == 1 else None,
                                      'candidate_model_ids': matches})
    return bindings


def set_graph_field(graph: dict, ref: dict, value):
    for part in ref.get('graph_path',[]):
        graph=graph[part]
    if 'nodes' in graph:
        node = next(n for n in graph['nodes'] if str(n['id']) == str(ref['node_id']))
        if ref.get('widget_index') is None:
            raise ValueError('Editor input needs an explicit widget index')
        node['widgets_values'][ref['widget_index']] = value
    else:
        graph[str(ref['node_id'])]['inputs'][ref['input_key']] = value
