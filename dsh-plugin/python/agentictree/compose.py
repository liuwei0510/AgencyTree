"""Assemble component EWCs into one connected ComfyUI prompt graph."""
from __future__ import annotations

import copy

from agentictree.engineering import is_link
from agentictree.graph_validate import validate_api_graph
from agentictree.schemas import WorkflowPlan


def _media(typ):
    for kind in ('mask','video','audio','mesh','image','text'):
        if kind in typ.casefold():
            return kind
    return typ


def _output_port(graph, capability, name):
    output=next((o for o in capability.get('outputs',[]) if o['name']==name),None)
    if output is None:
        raise ValueError(f'No declared component output {name}')
    kind=_media(output['type'])
    sinks={'image':{'SaveImage':'images','PreviewImage':'images'},
           'video':{'SaveVideo':'video'},'audio':{'SaveAudio':'audio','SaveAudioMP3':'audio'},
           'mesh':{'SaveGLB':'mesh','Save3D':'mesh'},'text':{'SaveText':'text','PreviewText':'text'}}.get(kind,{})
    matches=[]
    for node in graph.values():
        field=sinks.get(node.get('class_type'))
        ref=node.get('inputs',{}).get(field) if field else None
        if is_link(ref) and ref not in matches:
            matches.append(ref)
    if len(matches)!=1:
        raise ValueError(f'Output {name} needs one explicit graph port; found {len(matches)}')
    return matches[0],kind


def compose_workflow(asset):
    """Return the actual export plus transport/boundary metadata; no model call."""
    nodes=WorkflowPlan.model_validate(asset['execution_plan']).nodes
    if not nodes or not asset.get('ewc'):
        return None,{'complete':False,'format':None,'issues':['No executable components']}
    if len(nodes)==1 and asset['workflow_formats'][nodes[0].dag_id]=='editor':
        from agentictree.catalog_contracts import validate_editor_graph
        graph=copy.deepcopy(asset['ewc'][nodes[0].dag_id]);issues=validate_editor_graph(graph)
        from agentictree.engineering import mask_issues,validate_video_constraints
        constraints=next((n.get('constraints',{}) for n in asset.get('awg',{}).get('nodes',[]) if n.get('node_id')==nodes[0].dag_id),{})
        issues+=mask_issues(graph,nodes[0].inputs)+validate_video_constraints(graph,constraints)
        return (None if issues else graph),{'complete':not issues,'format':'editor','issues':issues,
                                           'components':{nodes[0].dag_id:[str(n['id']) for n in graph.get('nodes',[])]}}
    raw={};flat={};boundaries={};cross_edges=[]
    try:
        for node in nodes:
            graph=copy.deepcopy(asset['ewc'][node.dag_id])
            if asset['workflow_formats'][node.dag_id]=='editor':
                from agentictree.editor_to_api import convert_editor_workflow
                graph,issues=convert_editor_workflow(graph)
                if graph is None:
                    raise ValueError(f'{node.dag_id}: editor component requires explicit API conversion: {issues[:3]}')
            issues=validate_api_graph(graph)
            from agentictree.engineering import mask_issues,validate_video_constraints,validate_upscale_constraints
            constraints=next((n.get('constraints',{}) for n in asset.get('awg',{}).get('nodes',[]) if n.get('node_id')==node.dag_id),{})
            issues+=mask_issues(graph,node.inputs)+validate_video_constraints(graph,constraints)+validate_upscale_constraints(graph,constraints)
            if issues:
                raise ValueError('; '.join(issues))
            raw[node.dag_id]=graph
            boundaries[node.dag_id]=[]
            for key,value in graph.items():
                if str(key).startswith('__'):
                    continue
                target=f'{node.dag_id}::{key}'
                value=copy.deepcopy(value)
                value['inputs']={k:([f'{node.dag_id}::{v[0]}',v[1]] if is_link(v) else v)
                                 for k,v in value['inputs'].items()}
                flat[target]=value;boundaries[node.dag_id].append(target)
        flow={node.dag_id:{ref.from_node for ref in node.bindings.values()} for node in nodes}
        def flow_ancestors(key):
            seen=set();pending=list(flow.get(key,set()))
            while pending:
                parent=pending.pop()
                if parent not in seen:
                    seen.add(parent);pending.extend(flow.get(parent,set()))
            return seen
        for node in nodes:
            cap=asset['capabilities'][node.atomic_id]
            if set(node.depends_on)-flow_ancestors(node.dag_id):
                raise ValueError(f'{node.dag_id}: ordering-only dependencies need an explicit ComfyUI control adapter')
            for name,ref in node.bindings.items():
                source=next(n for n in nodes if n.dag_id==ref.from_node)
                port,kind=_output_port(raw[source.dag_id],asset['capabilities'][source.atomic_id],ref.output)
                replacement=[f'{source.dag_id}::{port[0]}',port[1]]
                injection=cap.get('inject',{}).get(name)
                if not injection:
                    raise ValueError(f'{node.dag_id}.{name}: no graph input binding')
                loader=f'{node.dag_id}::{injection["node_id"]}'
                cls=flat.get(loader,{}).get('class_type')
                expected={'LoadImage':'image','LoadImageMask':'mask','LoadVideo':'video','LoadAudio':'audio'}.get(cls)
                if expected!=kind:
                    raise ValueError(f'{node.dag_id}.{name}: {cls} needs a typed adapter for {kind}')
                if kind=='image':
                    select=f'{node.dag_id}::__input_{name}'
                    if select in flat:
                        raise ValueError('Component input selector ID collision')
                    flat[select]={'class_type':'ImageFromBatch','inputs':{'image':replacement,'batch_index':ref.index,'length':1},
                                  '_meta':{'title':f'{source.dag_id}.{ref.output}[{ref.index}] -> {node.dag_id}.{name}'}}
                    boundaries[node.dag_id].append(select)
                    replacement=[select,0]
                elif ref.index!=0:
                    raise ValueError(f'Index {ref.index} for {kind} needs an explicit selector adapter')
                uses=[]
                for key,part in flat.items():
                    for field,value in part['inputs'].items():
                        if is_link(value) and value[0]==loader:
                            if value[1]!=0:
                                raise ValueError(f'{loader}: additional output slot {value[1]} needs an explicit adapter')
                            uses.append((key,field))
                if not uses:
                    raise ValueError(f'{node.dag_id}.{name}: bound input is unused')
                for key,field in uses:
                    flat[key]['inputs'][field]=copy.deepcopy(replacement)
                flat.pop(loader)
                cross_edges.append({'from_component':source.dag_id,'output':ref.output,'to_component':node.dag_id,
                                    'input':name,'consumers':[{'node_id':k,'input':f} for k,f in uses]})
        for node in flat.values():
            if any(isinstance(v,str) and v.startswith('__PLACEHOLDER_') for v in node['inputs'].values()):
                raise ValueError('Unresolved cross-component filename placeholder')
        issues=validate_api_graph(flat)
        if issues:
            raise ValueError('; '.join(issues))
        return flat,{'complete':True,'format':'api','issues':[],'cross_component_edges':cross_edges,
                     'components':{k:[n for n in value if n in flat] for k,value in boundaries.items()}}
    except (ValueError,KeyError,TypeError) as error:
        return None,{'complete':False,'format':'api','issues':[str(error)]}


def attach_export(asset, *, strict=True):
    graph,info=compose_workflow(asset)
    asset['comfy_workflow']=graph
    asset['export_info']=info
    if strict and asset.get('status')=='validated_offline' and not info['complete']:
        asset['status']='failed'
        if 'generation' in asset:
            asset['generation']['converged']=False
            asset['generation']['failure_reason']='ComfyUI assembly failed: '+'; '.join(info['issues'])
    return asset
