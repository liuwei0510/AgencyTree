"""R -> implementation-independent ATT/AWG -> concrete bindings and ComfyUI code.

Tools own retrieval, binding and validation. A host agent can supply ATT/AWG as
JSON; an optional Backend is used only for semantic decomposition when needed.
"""
from __future__ import annotations

import json
import re
from typing import Any

from agentictree.schemas import (AbstractTask, AbstractTaskTree, AbstractWorkflowNode,
    AbstractWorkflowGraph, AbstractEdge, WorkflowPlan, PlanNode, BindingRef)
from agentictree.ewc_build import materialize_ewc
from agentictree.plan_validate import structural_validate


def media_type(value: str) -> str:
    v = value.casefold()
    for kind, words in [('image', ('image', 'png', 'jpeg')), ('video', ('video', 'mp4')),
                        ('audio', ('audio', 'wav', 'mp3')), ('mesh', ('mesh', '3d', 'glb')),
                        ('text', ('text', 'string', 'transcript'))]:
        if any(w in v for w in words):
            return kind
    return v


def abstract_interface(capability: str):
    leaf = capability.split('.')[-1]
    specs = {
        'text_to_image': ({'prompt':'text'}, {'images':'image'}),
        'image_editing': ({'source_image':'image', 'prompt':'text'}, {'images':'image'}),
        'text_to_video': ({'prompt':'text'}, {'video':'video'}),
        'image_to_video': ({'source_image':'image', 'prompt':'text'}, {'video':'video'}),
        'video_editing': ({'video_file':'video', 'prompt':'text'}, {'video':'video'}),
        'text_to_audio': ({'prompt':'text'}, {'audio':'audio'}),
        'audio_editing': ({'audio_file':'audio'}, {'audio':'audio'}),
        'audio_recognition': ({'audio_file':'audio'}, {'transcript':'text'}),
        'three_d': ({'prompt':'text'}, {'mesh':'mesh'}),
        'upscale': ({'source_image':'image'}, {'images':'image'}),
        'video_upscaling': ({'video_file':'video'}, {'video':'video'}),
    }
    return specs.get(leaf, ({}, {}))


def tool_analyze(intent: str, supplied_inputs: dict | None = None) -> AbstractTaskTree:
    text = intent.strip()
    low = text.casefold()
    if not text:
        raise ValueError('User requirement is empty')
    patterns = [
        (('transcrib', 'speech to text', 'speech-to-text', '语音识别', '音频转文字', '语音转文字'), 'core.perception.audio_recognition'),
        (('3d', 'mesh', '三维', '三d'), 'core.generative.three_d'),
        (('upscale', 'super resolution', '超分', '放大'), 'aux.postprocess.upscale'),
        (('image to video', 'image-to-video', '图生视频', 'animate this image'), 'core.editing.image_to_video'),
        (('video edit', 'edit video', '视频编辑'), 'core.editing.video_editing'),
        (('video', 'animation', '视频', '动画'), 'core.generative.text_to_video'),
        (('text to audio', 'text-to-speech', 'speech synthesis', 'music', '生成音频', '语音合成', '音乐'), 'core.generative.text_to_audio'),
        (('edit', 'inpaint', 'outpaint', '编辑', '改图', '修图'), 'core.editing.image_editing'),
        (('image', 'picture', 'draw', 'photo', 'illustrat', '图片', '图像', '画一', 'square', 'landscape', 'portrait'), 'core.generative.text_to_image'),
    ]
    capability = next((c for words,c in patterns if any(w in low for w in words)), None)
    if capability is None:
        raise ValueError('Tool planner cannot determine this requirement; provide a structured ATT or a semantic agent')
    if capability == 'aux.postprocess.upscale' and any(w in low for w in ('video', '视频')):
        capability = 'aux.postprocess.video_upscaling'
    inputs = dict(supplied_inputs or {})
    for value in re.findall(r'[^\s"\'`]+\.(?:png|jpg|jpeg|webp|wav|mp3|flac|mp4|mov)', text, re.I):
        key = 'audio_file' if re.search(r'\.(wav|mp3|flac)$', value, re.I) else 'video_file' if re.search(r'\.(mp4|mov)$', value, re.I) else 'source_image'
        inputs.setdefault(key, value)
    constraints = {'requirement': text}
    dims = re.search(r'(\d{2,5})\s*[x×*]\s*(\d{2,5})', low)
    if dims:
        constraints.update(width=int(dims[1]), height=int(dims[2]))
    for name, pattern in [('duration', r'(\d+(?:\.\d+)?)[\s-]*(?:seconds?|秒)'), ('fps', r'(\d+)\s*(?:fps|帧/秒)'),
                          ('scale_factor',r'(?:upscale[^\n]*?\bby\s*|放大\s*)(\d+(?:\.\d+)?)\s*(?:x\b|倍)')]:
        match = re.search(pattern, low)
        if match:
            constraints[name] = float(match[1])
    # A compound request needs decomposition, not a single guessed workflow.
    if re.search(r'\bthen\b|\bfinally\b|\bwhile\s+(?:also|creating|generating|depicting)\b|然后|随后|同时|最后', low):
        raise ValueError('Compound temporal requirement needs a structured ATT or a semantic agent')
    _, outputs = abstract_interface(capability)
    if 'video' in outputs.values() and re.search(r'\bhigh[ -]definition\b|\bhd\b|高清',low):
        constraints.setdefault('width',1280)
        constraints.setdefault('height',720)
    return AbstractTaskTree(tasks=[AbstractTask(task_id='t1', goal=text, capability_id=capability,
        inputs=inputs, outputs=outputs, constraints=constraints)])


def abstract_catalog(snapshot: dict) -> list[str]:
    return sorted({'.'.join(r['tree_branch']) for r in snapshot['records'].values()
                   if r.get('tree_branch') and r.get('status') in {'active', 'available'}})


def validate_att(att: AbstractTaskTree, catalog: list[str], supplied_inputs: dict | None = None) -> list[str]:
    errors = list(att.diagnostics)
    ids = {t.task_id for t in att.tasks}
    if not ids or len(ids) != len(att.tasks):
        errors.append('ATT must contain tasks with unique task IDs')
    for task in att.tasks:
        if task.capability_id not in catalog:
            errors.append(f'{task.task_id}: unknown abstract capability {task.capability_id}')
        if not task.goal.strip():
            errors.append(f'{task.task_id}: missing task goal')
        if re.search(r'\bhigh[ -]definition\b|\bhd\b|高清',task.goal,re.I) and 'video' in task.outputs.values():
            dims=[task.constraints.get('width',0),task.constraints.get('height',0)]
            if min(dims)<720 or max(dims)<1280:
                errors.append(f'{task.task_id}: HD video requires at least a 1280x720 output contract')
        if any(x not in ids or x == task.task_id for x in task.depends_on):
            errors.append(f'{task.task_id}: invalid task dependency')
        if not task.depends_on:
            expected_inputs=task.input_types or abstract_interface(task.capability_id)[0]
            available={**(supplied_inputs or {}),**task.inputs}
            for name,typ in expected_inputs.items():
                if media_type(typ) in {'image','audio','video','mesh'} and not available.get(name):
                    errors.append(f'{task.task_id}: missing user input {name}; sample files are not user assets')
    expected = {(d,t.task_id) for t in att.tasks for d in t.depends_on}
    if att.edges and set(att.edges) != expected:
        errors.append('ATT edge list disagrees with task dependencies')
    pending = {t.task_id:set(t.depends_on) for t in att.tasks}
    while pending:
        ready = {k for k,v in pending.items() if not v}
        if not ready:
            errors.append('Cycle in ATT')
            break
        pending = {k:v-ready for k,v in pending.items() if k not in ready}
    return errors


def build_awg(att: AbstractTaskTree) -> AbstractWorkflowGraph:
    nodes = []
    task_nodes = {}
    for index, task in enumerate(att.tasks, 1):
        ins, outs = abstract_interface(task.capability_id)
        node = AbstractWorkflowNode(node_id=f'n{index}', task_id=task.task_id,
            capability_id=task.capability_id, input_types=task.input_types or ins, output_types=task.outputs or outs,
            parameters={'prompt':task.goal, **task.inputs}, constraints=task.constraints)
        nodes.append(node)
        task_nodes[task.task_id] = node
    edges, ordering, unresolved = [], [], []
    for task in att.tasks:
        target = task_nodes[task.task_id]
        for dep in task.depends_on:
            source = task_nodes[dep]
            ordering.append((source.node_id, target.node_id))
            matches = [(out,inp) for out,typ in source.output_types.items()
                       for inp,target_type in target.input_types.items()
                       if media_type(typ)==media_type(target_type) and target_type!='text']
            if len(matches) == 1:
                out, inp = matches[0]
                edges.append(AbstractEdge(from_node=source.node_id,output=out,to_node=target.node_id,input=inp))
            elif len(matches) > 1:
                unresolved.append(f'{source.node_id}->{target.node_id}: ambiguous data ports; supply AWG edges')
    return AbstractWorkflowGraph(nodes=nodes, edges=edges, ordering=ordering, unresolved=unresolved)


def validate_awg(att: AbstractTaskTree, awg: AbstractWorkflowGraph) -> list[str]:
    errors = list(awg.unresolved)
    tasks = {t.task_id:t for t in att.tasks}
    nodes = {n.node_id:n for n in awg.nodes}
    if len(nodes) != len(awg.nodes):
        errors.append('Duplicate abstract node ID')
    if {n.task_id for n in awg.nodes} != set(tasks):
        errors.append('AWG does not cover exactly the ATT tasks')
    for node in awg.nodes:
        task = tasks.get(node.task_id)
        if task is None or node.capability_id != task.capability_id:
            errors.append(f'{node.node_id}: abstract capability differs from ATT')
        if task and any(node.constraints.get(k)!=v for k,v in task.constraints.items()):
            errors.append(f'{node.node_id}: task constraints were lost')
    for task_id,task in tasks.items():
        produced={key:typ for n in awg.nodes if n.task_id==task_id for key,typ in n.output_types.items()}
        if any(key not in produced or media_type(produced[key])!=media_type(typ) for key,typ in task.outputs.items()):
            errors.append(f'{task_id}: abstract task output contract was lost')
    for edge in awg.edges:
        source, target = nodes.get(edge.from_node),nodes.get(edge.to_node)
        if not source or not target or edge.output not in source.output_types or edge.input not in target.input_types:
            errors.append('Unknown abstract edge endpoint/port')
        elif media_type(source.output_types[edge.output]) != media_type(target.input_types[edge.input]):
            errors.append('Abstract edge types are incompatible')
        if (edge.from_node,edge.to_node) not in awg.ordering:
            errors.append('Data edge missing from AWG ordering')
    pending={key:set() for key in nodes}
    for a,b in awg.ordering:
        if a not in nodes or b not in nodes:
            errors.append('Unknown AWG ordering endpoint')
        else:
            pending[b].add(a)
    while pending:
        ready={k for k,v in pending.items() if not v}
        if not ready:
            errors.append('Cycle in abstract workflow')
            break
        pending={k:v-ready for k,v in pending.items() if k not in ready}
    expected = {(a.task_id,b.task_id) for a in awg.nodes for b in awg.nodes if (a.node_id,b.node_id) in awg.ordering}
    if not {(d,t.task_id) for t in att.tasks for d in t.depends_on}.issubset(expected):
        errors.append('AWG ordering omitted ATT dependencies')
    return errors


def numeric_controls(cap, constraints):
    """Bind quantitative requirements or reject them; never silently drop them."""
    numeric = {k:v for k,v in constraints.items() if isinstance(v,(int,float)) and not isinstance(v,bool)}
    values = {}
    defaults = {i.name:i.default for i in cap.inputs}
    for name, value in numeric.items():
        if name in cap.inject:
            values[name] = value
        elif cap.fixed_constraints.get(name) == value:
            continue
        elif name == 'duration':
            fps = numeric.get('fps', defaults.get('fps'))
            frames = [key for key in ('length','frames','num_frames') if key in cap.inject]
            if not isinstance(fps,(int,float)) or fps <= 0 or value <= 0 or len(frames) != 1:
                raise ValueError('no unambiguous duration or frame-count/fps controls')
            count = value * fps
            if abs(count-round(count)) > 1e-6:
                raise ValueError('duration cannot be represented as an integer frame count')
            if frames[0] in numeric and numeric[frames[0]] != round(count):
                raise ValueError('duration conflicts with the explicit frame count')
            values[frames[0]] = round(count)
            if 'fps' in cap.inject:
                values['fps'] = fps
        else:
            raise ValueError(f'no control or fixed contract for {name}={value}')
    return values


def instantiate(att, awg, caps, supplied_inputs=None):
    tasks = {t.task_id:t for t in att.tasks}
    selected, concrete = {}, []
    pending = list(awg.nodes)
    while pending:
        progress = False
        for abstract in list(pending):
            dependencies = [a for a,b in awg.ordering if b==abstract.node_id]
            if any(d not in selected for d in dependencies):
                continue
            task = tasks[abstract.task_id]
            candidates = [c for c in caps.values() if '.'.join(c.tree_branch or ())==abstract.capability_id]
            candidates.sort(key=lambda c:(any(w in (c.title+' '+c.description).casefold() for w in ('slot','stub','replace workflow')), c.workflow_format=='editor', len([i for i in c.inputs if i.required]), c.id))
            failures=[]
            for cap in candidates:
                values = {**(supplied_inputs or {}), **abstract.parameters, **task.inputs}
                from agentictree.registry import load_capability_graph
                from agentictree.engineering import mask_issues,resolution_issues
                try:
                    template = load_capability_graph(cap)
                    issues = mask_issues(template,values)
                    issues += resolution_issues(template,abstract.constraints)
                    if issues:
                        failures.extend(f'{cap.id}: {issue}' for issue in issues)
                        continue
                except (ValueError,OSError) as error:
                    failures.append(f'{cap.id}: {error}')
                    continue
                try:
                    controls = numeric_controls(cap, abstract.constraints)
                except ValueError as error:
                    failures.append(f'{cap.id}: {error}')
                    continue
                values.update(controls)
                mapped, bindings = {}, {}
                for inp in cap.inputs:
                    if inp.name in values:
                        mapped[inp.name] = values[inp.name]
                    elif inp.name.startswith('prompt') and 'prompt' in values:
                        mapped[inp.name] = values['prompt']
                    elif inp.name in {'source_image','prior_image','candidate_image'} and 'source_image' in values:
                        mapped[inp.name] = values['source_image']
                    else:
                        source_names=[name for name,typ in abstract.input_types.items()
                                      if media_type(typ)==media_type(inp.type) and name in values]
                        same_type=[i for i in cap.inputs if media_type(i.type)==media_type(inp.type)]
                        if len(source_names)==1 and len(same_type)==1:
                            mapped[inp.name]=values[source_names[0]]
                edge_error = False
                for edge in [e for e in awg.edges if e.to_node==abstract.node_id]:
                    upstream = selected[edge.from_node]
                    typ = abstract.input_types[edge.input]
                    targets = [i for i in cap.inputs if media_type(i.type)==media_type(typ)]
                    sources = [o for o in upstream.outputs if media_type(o.type)==media_type(typ)]
                    exact_targets=[i for i in targets if i.name==edge.input]
                    exact_sources=[o for o in sources if o.name==edge.output]
                    targets=exact_targets or targets
                    sources=exact_sources or sources
                    if len(targets)!=1 or len(sources)!=1:
                        edge_error = True
                        break
                    bindings[targets[0].name] = BindingRef(from_node=edge.from_node,output=sources[0].name)
                output_media = {media_type(o.type) for o in cap.outputs}
                required_media = {media_type(x) for x in abstract.output_types.values()}
                need_prompt = 'prompt' in abstract.input_types
                has_prompt = any(i.name.startswith('prompt') for i in cap.inputs)
                missing_abstract_inputs=[]
                for name,typ in abstract.input_types.items():
                    if media_type(typ) not in {'image','audio','video','mesh'}:
                        continue
                    targets=[i for i in cap.inputs if media_type(i.type)==media_type(typ)]
                    if not any(i.name in mapped or i.name in bindings for i in targets):
                        missing_abstract_inputs.append(name)
                if edge_error or missing_abstract_inputs or not required_media.issubset(output_media) or (need_prompt and not has_prompt):
                    failures.append(f'{cap.id}: capability interface does not implement abstract task')
                    continue
                node=PlanNode(dag_id=abstract.node_id,atomic_id=cap.id,depends_on=dependencies,inputs=mapped,bindings=bindings)
                trial=WorkflowPlan(nodes=concrete+[node])
                errors=structural_validate(trial,caps)
                if errors:
                    failures.extend(errors)
                    continue
                from agentictree.engineering import validate_video_constraints,validate_upscale_constraints
                preview,errors=materialize_ewc(trial,caps)
                if not errors:
                    errors=validate_video_constraints(preview[abstract.node_id],abstract.constraints)
                    errors+=validate_upscale_constraints(preview[abstract.node_id],abstract.constraints)
                if errors:
                    failures.extend(f'{cap.id}: {issue}' for issue in errors)
                    continue
                selected[abstract.node_id]=cap
                concrete.append(node)
                break
            else:
                raise ValueError(f'{abstract.node_id}: no matching callable implementation for {abstract.capability_id}; '+ '; '.join(failures[:4]))
            pending.remove(abstract)
            progress=True
        if not progress:
            raise ValueError('Cycle or missing ordering endpoint in AWG')
    plan=WorkflowPlan(summary='ACT implementation bindings',nodes=concrete)
    graphs,errors=materialize_ewc(plan,caps)
    if errors:
        raise ValueError('; '.join(errors))
    from agentictree.catalog_contracts import discover_contract
    for node_id, graph in graphs.items():
        observed=discover_contract(graph, selected[node_id].workflow_format)['outputs']
        if observed:
            produced={media_type(o['type']) for o in observed}
            expected={media_type(v) for n in awg.nodes if n.node_id==node_id for v in n.output_types.values()}
            if not expected.issubset(produced):
                raise ValueError(f'{node_id}: graph output does not implement abstract output {sorted(expected)}')
        from agentictree.engineering import validate_video_constraints
        constraints=next(n.constraints for n in awg.nodes if n.node_id==node_id)
        issues=validate_video_constraints(graph,constraints)
        if issues:
            raise ValueError('; '.join(issues))
    return plan,graphs


def run_generation(intent, snapshot, caps, *, backend=None, max_iterations=5, att=None, awg=None, inputs=None):
    logs=[]
    failure=None
    catalog=abstract_catalog(snapshot)
    task_tree=AbstractTaskTree.model_validate(att) if att is not None else None
    abstract_graph=AbstractWorkflowGraph.model_validate(awg) if awg is not None else None
    plan=WorkflowPlan()
    graphs=None
    mode='host_plan' if att is not None else 'tools_first'
    for attempt in range(1,max(1,max_iterations)+1):
        try:
            if task_tree is None:
                try:
                    task_tree=tool_analyze(intent,inputs)
                except ValueError:
                    if backend is None:
                        raise
                    mode='agent_fallback'
                    task_tree=AbstractTaskTree.model_validate_json(backend.complete_json(
                        'You are the System Analyst. Return an implementation-independent ATT JSON: '
                        '{"tasks":[{"task_id":"t1","goal":"...","capability_id":"L1.L2.L3",'
                        '"inputs":{},"outputs":{},"constraints":{},"depends_on":[]}],"edges":[],"diagnostics":[]}. '
                        'Use only listed abstract capabilities. Never substitute a different user goal. '
                        'Record missing capabilities in diagnostics. Do not return atomic_id, model names or graphs.',
                        json.dumps({'requirement':intent,'abstract_capabilities':catalog,'inputs':inputs or {},'feedback':failure},ensure_ascii=False)))
            for task in task_tree.tasks:
                if 'video' in task.outputs.values() and re.search(r'\bhigh[ -]definition\b|\bhd\b|高清',task.goal,re.I):
                    task.constraints.setdefault('width',1280)
                    task.constraints.setdefault('height',720)
            if not task_tree.edges:
                task_tree.edges=[(d,t.task_id) for t in task_tree.tasks for d in t.depends_on]
            errors=validate_att(task_tree,catalog,inputs)
            logs.append({'stage':'ANALYSIS','iteration':attempt,'is_converged':not errors,'issues':errors})
            if errors:
                raise ValueError('; '.join(errors))
            abstract_graph=abstract_graph or build_awg(task_tree)
            errors=validate_awg(task_tree,abstract_graph)
            if errors and backend and awg is None:
                abstract_graph=AbstractWorkflowGraph.model_validate_json(backend.complete_json(
                    'You are the System Architect. Return an implementation-independent AWG: '
                    '{"nodes":[{"node_id":"n1","task_id":"t1","capability_id":"L1.L2.L3",'
                    '"input_types":{},"output_types":{},"parameters":{},"constraints":{}}],'
                    '"edges":[{"from_node":"n1","output":"images","to_node":"n2","input":"source_image"}],'
                    '"ordering":[],"constraints":{},"unresolved":[]}. '
                    'Preserve ATT coverage, constraints and dependency ordering. Never select models or atomic_id.',
                    json.dumps({'att':task_tree.model_dump(),'feedback':errors},ensure_ascii=False)))
                errors=validate_awg(task_tree,abstract_graph)
            try:
                requested=tool_analyze(intent,inputs)
            except ValueError:
                requested=None
            if requested:
                expected={media_type(v) for t in requested.tasks for v in t.outputs.values()}
                nonterminal={a for a,b in abstract_graph.ordering}
                actual={media_type(v) for n in abstract_graph.nodes if n.node_id not in nonterminal for v in n.output_types.values()}
                if expected != actual:
                    errors.append(f'Requested output modalities {sorted(expected)} differ from AWG terminal outputs {sorted(actual)}')
            logs.append({'stage':'ARCHITECTURE','iteration':attempt,'is_converged':not errors,'issues':errors})
            if errors:
                raise ValueError('; '.join(errors))
            plan,graphs=instantiate(task_tree,abstract_graph,caps,inputs)
            if mode=='agent_fallback' and backend:
                review=json.loads(backend.complete_json(
                    'You are the intent consistency verifier. Compare the original requirement with ATT and abstract AWG. '
                    'Return {"ok":boolean,"issues":[string]}. Check omissions, invented tasks, inputs and constraints.',
                    json.dumps({'requirement':intent,'att':task_tree.model_dump(),'awg':abstract_graph.model_dump()},ensure_ascii=False)))
                if review.get('ok') is not True:
                    raise ValueError(f"Intent verification failed: {review.get('issues')}")
            logs.append({'stage':'ENGINEERING','iteration':attempt,'is_converged':True,'issues':[]})
            return {'att':task_tree.model_dump(),'awg':abstract_graph.model_dump(),'execution_plan':plan.model_dump(),
                    'ewc':graphs,'converged':True,'failure_reason':None,'validation_log':logs,'generation_mode':mode}
        except Exception as error:
            message=str(error)
            logs.append({'stage':'ENGINEERING' if task_tree and abstract_graph else 'ANALYSIS',
                         'iteration':attempt,'is_converged':False,'issues':[message]})
            repeated = failure == message
            failure=message
            if backend is None or att is not None or repeated:
                break
            # Semantic replanning may change ATT; graph construction stays a tool.
            if mode != 'agent_fallback':
                try:
                    revised=backend.complete_json('You are the System Analyst. Revise the ATT against the requirement and diagnostics. '
                        'Return {"tasks":[{"task_id":"t1","goal":"...","capability_id":"L1.L2.L3",'
                        '"inputs":{},"outputs":{},"constraints":{},"depends_on":[]}],"edges":[],"diagnostics":[]}. '
                        'Only use abstract capabilities. Do not choose implementations or replace the user goal.',
                        json.dumps({'requirement':intent,'abstract_capabilities':catalog,'feedback':failure,'inputs':inputs or {}},ensure_ascii=False))
                    task_tree=AbstractTaskTree.model_validate_json(revised)
                    abstract_graph=None
                    mode='agent_fallback'
                except Exception as semantic_error:
                    failure=str(semantic_error)
                    break
            else:
                task_tree=None
                abstract_graph=None
    return {'att':task_tree.model_dump() if task_tree else AbstractTaskTree(diagnostics=[failure or 'failed']).model_dump(),
            'awg':abstract_graph.model_dump() if abstract_graph else AbstractWorkflowGraph().model_dump(),
            'execution_plan':plan.model_dump(),'ewc':None,'converged':False,'failure_reason':failure,
            'validation_log':logs,'generation_mode':mode}
