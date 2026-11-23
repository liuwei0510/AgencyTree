"""Reuse, minimal local maintenance or regeneration, with immutable rollback.

The ACT diff is the only change input. Runtime availability/quality are explicitly
outside offline validation and never reported as passing.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

from agentictree.assets import generate_asset
from agentictree.backends import Backend
from agentictree.ewc_build import materialize_ewc
from agentictree.knowledge import active_capabilities
from agentictree.plan_validate import structural_validate
from agentictree.schemas import BindingRef, PlanNode, WorkflowPlan
from agentictree.storage import digest, now, save_version


def _adapter_chain(caps, source_type, target_type):
    """Breadth-first search, restricted to explicit one-input conversion contracts."""
    queue = [(source_type, [])]
    seen = {source_type}
    while queue:
        current, chain = queue.pop(0)
        if current == target_type:
            return chain
        for cap in sorted(caps.values(), key=lambda c: c.id):
            if not cap.tree_branch or cap.tree_branch[:2] != ("aux", "preprocess"):
                continue
            required = [i for i in cap.inputs if i.required and i.default is None]
            if len(required) != 1 or len(cap.outputs) != 1 or required[0].type != current:
                continue
            out_type = cap.outputs[0].type
            if out_type not in seen:
                seen.add(out_type)
                queue.append((out_type, chain + [(cap, required[0].name, cap.outputs[0].name)]))
    return None


def _adapt(plan, caps, actions):
    by_id = {n.dag_id: n for n in plan.nodes}
    for node in list(plan.nodes):
        cap = caps[node.atomic_id]
        for name, binding in list(node.bindings.items()):
            upstream = by_id[binding.from_node]
            outputs = {o.name: o.type for o in caps[upstream.atomic_id].outputs}
            inputs = {i.name: i.type for i in cap.inputs}
            if name not in inputs or binding.output not in outputs:
                raise ValueError(f"{node.dag_id}: renamed port needs explicit semantic mapping")
            source_type, target_type = outputs[binding.output], inputs[name]
            if source_type == target_type or target_type == "any":
                continue
            chain = _adapter_chain(caps, source_type, target_type)
            if chain is None:
                raise ValueError(f"No adapter from {source_type} to {target_type}")
            previous = binding.model_copy(deep=True)
            for adapter, input_name, output_name in chain:
                new_id = f"adapter_{len(plan.nodes)}"
                while new_id in by_id:
                    new_id += "_"
                added = PlanNode(dag_id=new_id, atomic_id=adapter.id,
                                 depends_on=[previous.from_node], bindings={input_name: previous})
                plan.nodes.append(added)
                by_id[new_id] = added
                actions.append({"action": "insert_adapter", "node": new_id,
                                "atomic_id": adapter.id, "target": node.dag_id, "input": name})
                previous = BindingRef(from_node=new_id, output=output_name)
            node.bindings[name] = previous
            node.depends_on = sorted(set(node.depends_on + [previous.from_node]))


def _plan_for_asset(asset):
    legacy = any('atomic_id' in n for n in asset.get('awg', {}).get('nodes', []))
    return WorkflowPlan.model_validate(asset['awg'] if legacy else asset['execution_plan'])


def _model_contract(record):
    six = record.get('six_tuple', {})
    meta = record.get('metadata', {})
    return (six.get('input_types', six.get('input_type')), six.get('output_types', six.get('output_type')),
            meta.get('type'), meta.get('base'))


def _binding_rows(asset, plan, snapshot):
    from agentictree.catalog_contracts import bind_models
    rows = copy.deepcopy(asset.get('model_bindings'))
    if rows is None:
        rows = bind_models(asset.get('ewc') or {}, asset.get('workflow_formats', {}), snapshot['records'])
    return {node:[({'model_id':v} if isinstance(v,str) else v) for v in values]
            for node,values in rows.items()}


def evolve_asset(asset: dict, old_act: dict, new_act: dict, store: Path, *,
                 backend: Backend | None = None, max_iterations: int = 5) -> dict:
    from itertools import product
    from agentictree.catalog_contracts import bind_models, set_graph_field
    from agentictree.graph_validate import validate_api_graph
    from agentictree.catalog_contracts import validate_editor_graph
    from agentictree.knowledge import diff_records
    from agentictree.thesis_pipeline import media_type, numeric_controls
    from agentictree.model_compat import compatible_model
    if asset['act_version'] != old_act['version']:
        raise ValueError('Asset ACT version does not match old snapshot')
    if asset['status'] != 'validated_offline':
        raise ValueError('Evolution requires a previously validated asset')
    changes=diff_records(old_act['records'], new_act['records'])
    changed={e['id']:e for e in changes}
    plan=_plan_for_asset(asset)
    rows=_binding_rows(asset,plan,old_act)
    reasons={}
    for node in plan.nodes:
        ids={node.atomic_id}
        for binding in rows.get(node.dag_id,[]):
            ids.update(binding.get('candidate_model_ids',[]))
            if binding.get('model_id'):
                ids.add(binding['model_id'])
        events=[changed[k] for k in sorted(ids) if k in changed]
        if events:
            reasons[node.dag_id]=events
    affected=list(reasons)
    caps=active_capabilities(new_act,store/'materialized')
    classification='partial' if affected else 'reuse'
    actions=[]
    diagnostics=[]
    attempts=[]
    candidate=None
    forbidden_filenames=set()
    for bindings in rows.values():
        for binding in bindings:
            model_id=binding.get('model_id')
            if not model_id and any(k in changed for k in binding.get('candidate_model_ids',[])):
                # A filename alone cannot select among multiple catalog models.
                # Regenerate without the ambiguous loader instead of claiming reuse.
                classification='total'
                forbidden_filenames.add(binding['filename'])
                continue
            if model_id not in changed:
                continue
            old_model=old_act['records'][model_id]
            compatible=[r for r in new_act['records'].values()
                        if r.get('kind')=='comfyui_model' and r.get('status')=='available'
                        and r.get('tree_branch')==old_model.get('tree_branch')
                        and compatible_model(old_model,r,binding)]
            if not compatible:
                classification='total'
                forbidden_filenames.add(old_model.get('metadata',{}).get('filename'))
    affected_edges=[{'from_node':b.from_node,'to_node':n.dag_id,'input':name}
                    for n in plan.nodes for name,b in n.bindings.items()
                    if n.dag_id in reasons or b.from_node in reasons]
    try:
        choices=[]
        for node in plan.nodes:
            old_record=old_act['records'].get(node.atomic_id,{})
            branch=old_record.get('tree_branch')
            current=new_act['records'].get(node.atomic_id)
            if node.dag_id in reasons and current and current.get('tree_branch')!=branch:
                classification='total'
            candidates=[c for c in caps.values() if list(c.tree_branch or ())==list(branch or ())]
            candidates.sort(key=lambda c:(c.id!=node.atomic_id,
                sum(i.required and i.default is None and i.name not in node.inputs and i.name not in node.bindings
                    and i.name not in plan.global_inputs for i in c.inputs),c.id))
            if node.dag_id not in reasons:
                candidates=[c for c in candidates if c.id==node.atomic_id]
            if not candidates:
                classification='total'
            choices.append(candidates)
        if classification=='total':
            inputs={k:v for t in asset.get('att',{}).get('tasks',[]) for k,v in t.get('inputs',{}).items()}
            from agentictree.catalog_contracts import model_references
            excluded=set()
            for key,cap in caps.items():
                from agentictree.registry import load_workflow_json
                if any(r['filename'] in forbidden_filenames for r in model_references(load_workflow_json(cap.workflow_path()),cap.workflow_format)):
                    excluded.add(key)
            candidate=generate_asset(asset['intent'],new_act,store,backend=backend,
                                     max_iterations=max_iterations,parent=asset['version'],inputs=inputs,
                                     excluded_implementations=excluded)
            if candidate['status']!='validated_offline':
                raise ValueError(candidate['generation']['failure_reason'])
            actions.append({'action':'regenerate','scope':['ATT','AWG','EWC'],
                            'events':[e['change_id'] for events in reasons.values() for e in events]})
        else:
            combinations=product(*choices)
            for index, replacements in enumerate(combinations):
                if index>=max(1,max_iterations):
                    break
                trial=plan.model_copy(deep=True)
                trial_actions=[]
                try:
                    for node,replacement in zip(trial.nodes,replacements):
                        if node.dag_id not in reasons:
                            continue
                        before=node.model_dump()
                        node.atomic_id=replacement.id
                        allowed={i.name for i in replacement.inputs}
                        # Removing a supplied parameter is not a safe migration.
                        unknown=set(node.inputs)-allowed
                        if unknown:
                            raise ValueError(f'Explicit parameter migration required: {sorted(unknown)}')
                        if before!=node.model_dump():
                            trial_actions.append({'action':'rebind_and_migrate','node':node.dag_id,
                                'before':before,'after':node.model_dump(),
                                'events':[e['change_id'] for e in reasons[node.dag_id]]})
                        elif node.atomic_id in changed:
                            trial_actions.append({'action':'refresh_implementation','node':node.dag_id,
                                'implementation_id':node.atomic_id,'events':[changed[node.atomic_id]['change_id']]})
                    _adapt(trial,caps,trial_actions)
                    errors=structural_validate(trial,caps)
                    for abstract in asset.get('awg',{}).get('nodes',[]):
                        if 'capability_id' not in abstract:
                            continue
                        bound=next((n for n in trial.nodes if n.dag_id==abstract['node_id']),None)
                        if bound:
                            cap=caps[bound.atomic_id]
                            try:
                                bound.inputs.update(numeric_controls(cap, abstract.get('constraints',{})))
                            except ValueError as error:
                                errors.append(f'{bound.dag_id}: replacement violates quantitative constraint: {error}')
                            expected={media_type(v) for v in abstract.get('output_types',{}).values()}
                            if '.'.join(cap.tree_branch or ())!=abstract['capability_id'] or not expected.issubset({media_type(o.type) for o in cap.outputs}):
                                errors.append(f"{bound.dag_id}: replacement violates the retained abstract capability/output contract")
                    graphs,graph_errors=materialize_ewc(trial,caps)
                    errors+=graph_errors
                    if errors:
                        raise ValueError('; '.join(errors))
                    # Preserve saved engineering model overrides when the template itself
                    # has not changed. Reuse must not reset a previously evolved loader.
                    for node in trial.nodes:
                        original=next((n for n in plan.nodes if n.dag_id==node.dag_id),None)
                        old_record=asset.get('capabilities',{}).get(node.atomic_id,old_act['records'].get(node.atomic_id,{}))
                        new_record=new_act['records'].get(node.atomic_id,{})
                        if original and original.atomic_id==node.atomic_id and old_record.get('workflow_sha256')==new_record.get('workflow_sha256'):
                            for binding in rows.get(node.dag_id,[]):
                                if binding.get('input_key') and binding.get('filename'):
                                    set_graph_field(graphs[node.dag_id],binding,binding['filename'])
                    for node_id, bindings in rows.items():
                        for binding in bindings:
                            model_id=binding.get('model_id')
                            if model_id not in changed:
                                continue
                            old_model=old_act['records'][model_id]
                            available=[(key,value) for key,value in new_act['records'].items()
                                if value.get('kind')=='comfyui_model' and value.get('status')=='available'
                                and value.get('tree_branch')==old_model.get('tree_branch')
                                and compatible_model(old_model,value,binding)]
                            available.sort(key=lambda kv:(kv[0]!=model_id,kv[0]))
                            if not available:
                                raise ValueError(f'Model interface needs replanning or an explicit adapter: {model_id}')
                            if 'node_id' not in binding or not binding.get('input_key'):
                                raise ValueError(f'Model binding has no concrete loader reference: {model_id}')
                            new_id,model=available[0]
                            filename=model['metadata']['filename']
                            set_graph_field(graphs[node_id],binding,filename)
                            trial_actions.append({'action':'rebind_model','node':node_id,'old_model_id':model_id,
                                'new_model_id':new_id,'loader':binding,'filename':filename,
                                'events':[changed[model_id]['change_id']]})
                    touched=set(affected)|{a['target'] for a in trial_actions if a['action']=='insert_adapter'}
                    for key,graph in (asset.get('ewc') or {}).items():
                        if key not in touched:
                            if graphs.get(key)!=graph:
                                raise ValueError(f'Unplanned mutation of {key}')
                            graphs[key]=copy.deepcopy(graph)
                    for key,graph in graphs.items():
                        errors=validate_editor_graph(graph) if 'nodes' in graph else validate_api_graph(graph)
                        if errors:
                            raise ValueError('; '.join(errors))
                        from agentictree.engineering import validate_video_constraints,validate_upscale_constraints
                        abstract=next((n for n in asset.get('awg',{}).get('nodes',[]) if n.get('node_id')==key),{})
                        errors=validate_video_constraints(graph,abstract.get('constraints',{}))
                        errors+=validate_upscale_constraints(graph,abstract.get('constraints',{}))
                        if errors:
                            raise ValueError('; '.join(errors))
                    if backend and affected:
                        review=json.loads(backend.complete_json(
                            'You are the intent consistency verifier. Check original requirement, abstract assets and explicit maintenance actions. '
                            'Return {"ok":boolean,"issues":[string]}. Do not infer runtime execution.',
                            json.dumps({'intent':asset['intent'],'att':asset['att'],'awg':asset['awg'],
                                        'actions':trial_actions},ensure_ascii=False)))
                        if review.get('ok') is not True:
                            raise ValueError(f"Intent verification failed: {review.get('issues')}")
                    formats={n.dag_id:caps[n.atomic_id].workflow_format for n in trial.nodes}
                    candidate={**copy.deepcopy(asset),'parent':asset['version'],'created_at':now(),
                        'act_version':new_act['version'],'execution_plan':trial.model_dump(),'ewc':graphs,
                        'workflow_formats':formats,'model_bindings':bind_models(graphs,formats,new_act['records']),
                        'capabilities':{n.atomic_id:new_act['records'][n.atomic_id] for n in trial.nodes}}
                    # Legacy assets keep their old layout; new assets preserve abstract AWG.
                    if any('atomic_id' in n for n in asset.get('awg',{}).get('nodes',[])):
                        candidate['awg']=trial.model_dump()
                    for node in trial.nodes:
                        row=candidate.setdefault('bindings',{}).setdefault(node.dag_id,{'awg_node_id':node.dag_id})
                        row['implementation_id']=node.atomic_id
                        row['model_ids']=sorted({mid for b in candidate['model_bindings'].get(node.dag_id,[])
                                                 for mid in ([b['model_id']] if b.get('model_id') else b.get('candidate_model_ids',[]))})
                    candidate.pop('generation',None)
                    from agentictree.compose import attach_export
                    attach_export(candidate)
                    if candidate['status']!='validated_offline':
                        raise ValueError('ComfyUI assembly failed: '+ '; '.join(candidate['export_info']['issues']))
                    candidate['version']='workflow-'+digest(candidate)[:20]
                    save_version(store/'versions',candidate)
                    actions=trial_actions
                    attempts.append({'attempt':index+1,'ok':True})
                    break
                except Exception as error:
                    candidate=None
                    attempts.append({'attempt':index+1,'ok':False,'diagnostic':str(error)})
            if candidate is None:
                raise ValueError(attempts[-1]['diagnostic'] if attempts else 'No compatible implementation')
    except Exception as error:
        diagnostics.append(str(error))
        candidate=None
    abstract_plan={'class':classification,'minimal_subgraph':{'nodes':affected,'edges':affected_edges},
        'reuse':[n.dag_id for n in plan.nodes if n.dag_id not in reasons],
        'events':{k:[e['change_id'] for e in v] for k,v in reasons.items()},
        'rebuild':['ATT','AWG','EWC'] if classification=='total' else [],'rollback':asset['version']}
    report={'version':'','parent':asset['version'],'created_at':now(),'old_act':old_act['version'],
        'new_act':new_act['version'],'classification':classification,'affected_nodes':affected,
        'changes':changes,'model_changes':[e for e in changes if (e.get('after') or e.get('before') or {}).get('kind')=='comfyui_model'],'abstract_change_plan':abstract_plan,
        'executable_change_plan':actions,'actions':actions,'attempts':attempts,'diagnostics':diagnostics,
        'committed':candidate is not None,'candidate':candidate,'rollback_target':asset['version'],
        'runtime_verified':False,'intent_validation':'agent' if backend else 'task_contract_and_binding_coverage'}
    report['version']='evolution-'+digest(report)[:20]
    save_version(store/'evolutions',report)
    return report
