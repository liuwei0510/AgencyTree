"""Persist the thesis asset chain and explicit engineering bindings."""
from __future__ import annotations

from pathlib import Path

from agentictree.backends import Backend
from agentictree.catalog_contracts import bind_models
from agentictree.knowledge import active_capabilities
from agentictree.storage import digest, now, save_version
from agentictree.thesis_pipeline import run_generation


def generate_asset(intent: str, snapshot: dict, store: Path, *, backend: Backend | None = None,
                   max_iterations: int = 5, parent: str | None = None,
                   att: dict | None = None, awg: dict | None = None, inputs: dict | None = None,
                   excluded_implementations: set[str] | None = None) -> dict:
    from agentictree.thesis_pipeline import tool_analyze
    branches = None
    if backend is None:
        try:
            tasks = att['tasks'] if att is not None else tool_analyze(intent,inputs).model_dump()['tasks']
            branches = {t['capability_id'] for t in tasks}
        except (ValueError,KeyError):
            branches = set()
    caps = active_capabilities(snapshot, store / 'materialized', branches)
    caps = {k:v for k,v in caps.items() if k not in (excluded_implementations or set())}
    result = run_generation(intent, snapshot, caps, backend=backend, max_iterations=max_iterations,
                            att=att, awg=awg, inputs=inputs)
    formats = {n['dag_id']:caps[n['atomic_id']].workflow_format for n in result['execution_plan']['nodes']}
    model_bindings = bind_models(result['ewc'] or {}, formats, snapshot['records'])
    task_ids = {n['node_id']:n['task_id'] for n in result['awg']['nodes']}
    cap_ids = {n['node_id']:n['capability_id'] for n in result['awg']['nodes']}
    bindings = {n['dag_id']:{'task_id':task_ids[n['dag_id']], 'awg_node_id':n['dag_id'],
                'capability_id':cap_ids[n['dag_id']], 'implementation_id':n['atomic_id'],
                'model_ids':sorted({key for b in model_bindings.get(n['dag_id'],[]) for key in
                                   ([b['model_id']] if b['model_id'] else b['candidate_model_ids'])})}
                for n in result['execution_plan']['nodes']}
    asset = {'version':'', 'schema_version':2, 'parent':parent, 'created_at':now(), 'intent':intent,
             'act_version':snapshot['version'], 'status':'validated_offline' if result['converged'] else 'failed',
             'runtime_verified':False, 'generation_mode':result['generation_mode'],
             'intent_validation':'abstract_task_coverage_and_explicit_contracts',
             'att':result['att'], 'awg':result['awg'], 'execution_plan':result['execution_plan'],
             'ewc':result['ewc'], 'workflow_formats':formats, 'bindings':bindings,
             'model_bindings':model_bindings, 'generation':result,
             'capabilities':{n['atomic_id']:snapshot['records'][n['atomic_id']]
                             for n in result['execution_plan']['nodes']}}
    if result['converged']:
        from agentictree.compose import attach_export
        attach_export(asset)
    asset['version']='workflow-'+digest(asset)[:20]
    save_version(store/'versions',asset)
    return asset
