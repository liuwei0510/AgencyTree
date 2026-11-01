#!/usr/bin/env python3
"""Sample the real 220-task dataset through generation and workflow evolution."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from agentictree.assets import generate_asset
from agentictree.catalog_contracts import node_fields
from agentictree.evolution import evolve_asset
from agentictree.storage import digest,now,read_json,write_json
from agentictree.engineering import mask_issues,video_contracts,validate_video_constraints,validate_upscale_constraints
from agentictree.graph_validate import validate_api_graph


def verify(asset, row):
    if asset['status'] != 'validated_offline':
        return [asset['generation']['failure_reason']]
    errors = []
    if not asset.get('export_info',{}).get('complete') or not asset.get('comfy_workflow'):
        errors.append('No complete ComfyUI workflow export')
    elif asset['export_info']['format']=='api':
        errors.extend(validate_api_graph(asset['comfy_workflow']))
        if '__PLACEHOLDER_' in json.dumps(asset['comfy_workflow']):
            errors.append('Complete workflow still contains a filename placeholder')
        wanted=sum(len(n['bindings']) for n in asset['execution_plan']['nodes'])
        if len(asset['export_info'].get('cross_component_edges',[]))!=wanted:
            errors.append('Cross-component connections were not fully assembled')
    if 'atomic_id' in json.dumps(asset['awg']):
        errors.append('Concrete implementation leaked into the abstract AWG')
    fields = {key:list(node_fields(graph,asset['workflow_formats'][key])) for key,graph in asset['ewc'].items()}
    if row.get('resource') and not any(value == row['resource'] for values in fields.values() for _,_,_,value,_,_ in values):
        errors.append('Original input media filename was not injected')
    for abstract in asset['awg']['nodes']:
        node = next(n for n in asset['execution_plan']['nodes'] if n['dag_id'] == abstract['node_id'])
        graph=asset['ewc'][abstract['node_id']]
        errors+=mask_issues(graph,node['inputs'])
        errors+=validate_video_constraints(graph,abstract['constraints'])
        errors+=validate_upscale_constraints(graph,abstract['constraints'])
        values = {key:value for _,_,key,value,_,_ in fields[abstract['node_id']]}
        if 'prompt' in abstract['input_types'] and not any(
                value == abstract['parameters'].get('prompt') for _,_,_,value,_,_ in fields[abstract['node_id']]):
            errors.append(f'{abstract["node_id"]}: task instruction was not injected into the graph')
        fixed = asset['capabilities'][node['atomic_id']].get('fixed_constraints',{})
        for name,wanted in abstract['constraints'].items():
            if not isinstance(wanted,(int,float)) or isinstance(wanted,bool):
                continue
            if fixed.get(name) == wanted or values.get(name) == wanted:
                continue
            if name == 'scale_factor' and any(values.get(key) == wanted for key in ('scale_by','upscale_factor')):
                continue
            if name == 'duration':
                timing=video_contracts(graph)
                if timing and all(abs(v['duration']-wanted)<1e-6 for v in timing):
                    continue
            errors.append(f'{abstract["node_id"]}: {name}={wanted} absent from actual graph/fixed contract')
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset',type=Path,default=ROOT/'data/benchmarks/requirements_220.json')
    parser.add_argument('--plans',type=Path,default=ROOT/'data/benchmarks/smoke_plans.json')
    parser.add_argument('--act',type=Path,default=ROOT/'data/act/latest.json')
    parser.add_argument('--output',type=Path,default=ROOT/'data/benchmarks/smoke/latest')
    parser.add_argument('--ids',nargs='+',default=['001','041','061','101','121','161','201'])
    args = parser.parse_args()
    dataset = read_json(args.dataset)
    rows = {r['id']:r for r in dataset['requirements']}
    plans = read_json(args.plans)['plans']
    snapshot = read_json(args.act)
    results,assets = [],{}
    for key in args.ids:
        row = rows[key]
        try:
            asset = generate_asset(row['instruction'],snapshot,args.output/key,att=plans.get(key))
            errors = verify(asset,row)
            assets[key] = asset
            write_json(args.output/key/'asset.json',asset)
            write_json(args.output/key/'att.json',asset['att'])
            write_json(args.output/key/'awg.json',asset['awg'])
            write_json(args.output/key/'ewc.json',asset['ewc'])
            workflow_file=None
            if asset.get('comfy_workflow'):
                workflow_file='workflow_api.json' if asset['export_info']['format']=='api' else 'workflow_editor.json'
                write_json(args.output/key/workflow_file,asset['comfy_workflow'])
            outcome = {'id':key,'name':row['name'],'category':row['category'],'modality':row['modality'],
                'instruction':row['instruction'],'status':'passed' if not errors else 'failed',
                'mode':asset['generation_mode'],'task_count':len(asset['att']['tasks']),
                'nodes':len(asset['execution_plan']['nodes']),'errors':errors,
                'implementations':[n['atomic_id'] for n in asset['execution_plan']['nodes']],
                'asset':str((args.output/key/'asset.json').resolve())}
            outcome['workflow']=str((args.output/key/workflow_file).resolve()) if workflow_file else None
            outcome['complete_export']=asset.get('export_info',{}).get('complete',False)
            outcome['video_outputs']=video_contracts(asset['comfy_workflow']) if asset.get('comfy_workflow') else []
        except Exception as error:
            outcome = {'id':key,'name':row['name'],'category':row['category'],'status':'failed','errors':[str(error)]}
        results.append(outcome)
        print(json.dumps(outcome,ensure_ascii=False),flush=True)
    evolution = {}
    if '101' in assets and assets['101']['status'] == 'validated_offline':
        asset = assets['101']
        reuse = evolve_asset(asset,snapshot,snapshot,args.output/'evolution/reuse')
        changed = copy.deepcopy(snapshot)
        binding=next(b for b in asset['model_bindings']['n2'] if b.get('model_id') and b['loader_class']=='UpscaleModelLoader')
        old_model=binding['model_id']
        changed['records'][old_model]['status']='deprecated'
        changed['version'] = 'smoke-act-model-'+digest([snapshot['version'],old_model])[:16]
        partial = evolve_asset(asset,snapshot,changed,args.output/'evolution/partial')
        preserved = bool(partial['candidate']) and partial['candidate']['att'] == asset['att'] and partial['candidate']['awg'] == asset['awg']
        untouched = bool(partial['candidate']) and partial['candidate']['ewc']['n1'] == asset['ewc']['n1']
        model_actions=[a for a in partial['actions'] if a['action']=='rebind_model']
        after_reuse=None
        actual_model_replaced=False
        if partial['candidate']:
            after_reuse=evolve_asset(partial['candidate'],changed,changed,args.output/'evolution/reuse_after_model_change')
            write_json(args.output/'evolution/after_workflow_api.json',partial['candidate']['comfy_workflow'])
            actual_model_replaced=bool(model_actions)
            for action in model_actions:
                key=f'{action["node"]}::{action["loader"]["node_id"]}'
                field=action['loader']['input_key']
                actual_model_replaced=actual_model_replaced and (
                    action['old_model_id']!=action['new_model_id']
                    and asset['comfy_workflow'][key]['inputs'][field]==action['loader']['filename']
                    and partial['candidate']['comfy_workflow'][key]['inputs'][field]==action['filename'])
        from agentictree.model_compat import compatible_model
        incompatible=copy.deepcopy(changed)
        for model in incompatible['records'].values():
            if model.get('kind')=='comfyui_model' and compatible_model(snapshot['records'][old_model],model,binding):
                model['status']='deprecated'
        incompatible['version']='smoke-act-no-compatible-'+digest(old_model)[:16]
        rollback=evolve_asset(asset,snapshot,incompatible,args.output/'evolution/no_compatible_model')
        evolution = {'requirement_id':'101','reuse':reuse['committed'] and reuse['classification']=='reuse',
            'partial':partial['committed'] and partial['classification']=='partial',
            'abstract_assets_preserved':preserved,'unaffected_graph_preserved':untouched,
            'real_catalog_model_replaced':actual_model_replaced,
            'model_changes':model_actions,'reusable_after_model_change':bool(after_reuse) and after_reuse['committed'],
            'rollback_without_compatible_model':not rollback['committed'] and rollback['rollback_target']==asset['version'],
            'scenario':'Simulate deprecation of the actual bound 2x model in a copy of the real ACT; live catalog remains intact.',
            'diagnostics':partial['diagnostics']}
        evolution['reusable_after_model_change']=bool(after_reuse) and after_reuse['committed'] and (
            after_reuse['classification']=='reuse' and after_reuse['candidate']['comfy_workflow']==partial['candidate']['comfy_workflow'])
        write_json(args.output/'evolution/before_workflow_api.json',asset['comfy_workflow'])
        write_json(args.output/'evolution/no_compatible_model.json',rollback)
        if after_reuse:
            write_json(args.output/'evolution/reuse_after_model_change.json',after_reuse)
        write_json(args.output/'evolution/reuse.json',reuse)
        write_json(args.output/'evolution/partial.json',partial)
    report = {'created_at':now(),'dataset':str(args.dataset.resolve()),'dataset_count':dataset['count'],
        'act_version':snapshot['version'],'cases':results,'passed':sum(r['status']=='passed' for r in results),
        'failed':sum(r['status']=='failed' for r in results),'evolution':evolution,
        'model_api_called':False,'comfyui_executed':False,
        'scope':'Offline contracts, quantitative parameter injection, graph structure and versioned evolution; media quality is not executed/evaluated.'}
    write_json(args.output/'report.json',report)
    lines = ['# 220 条需求的抽样 smoke', '',
             f'本次抽样 {len(results)} 条：{report["passed"]} 条通过，{report["failed"]} 条失败。', '',
             f'ACT：`{snapshot["version"]}`。未调用额外模型 API，未执行 ComfyUI。', '',
             '| 编号 | 需求 | 分类 | 任务数 | 结果 | 完整工作流 | 资产 |', '| --- | --- | --- | ---: | --- | --- | --- |']
    for case in results:
        workflow=f'[API JSON]({case["id"]}/workflow_api.json)' if case.get('complete_export') else '未完成'
        lines.append(f'| {case["id"]} | {case["name"]} | {case["category"]} | {case.get("task_count",0)} | {case["status"]} | {workflow} | [查看]({case["id"]}/asset.json) |')
    lines.extend(['', '检查范围：需求原文及输入文件名注入、抽象图与实现绑定分离、完整 API 图中的真实连线、掩码前置条件、数值参数与实际视频输出契约。媒体内容与生成质量未执行验证。', '',
                  '## 编辑与视频', '',
                  '局部编辑未提供目标掩码时，跳过依赖输入 alpha 的 Fill 和只提供边缘掩码的扩图实现，选择无需掩码的指令编辑实现。'])
    for case in results:
        for output in case.get('video_outputs',[]):
            lines.append(f'- 需求 {case["id"]}：{output["width"]}×{output["height"]}，原生生成 {output["generated_frames"]} 帧，最终 {output["output_frames"]} 帧 / {output["fps"]} fps = {output["duration"]:g} 秒。')
    lines.extend(['', '## 模型演化', '',
                  '需求 101 在真实 ACT 的临时副本中模拟已绑定模型退役；原始目录未修改。', '',
                  '[演化前完整工作流](evolution/before_workflow_api.json) · [演化后完整工作流](evolution/after_workflow_api.json) · [详细演化记录](evolution/partial.json)', ''])
    for action in evolution.get('model_changes',[]):
        lines.append(f'- `{action["node"]}` 的 `{action["loader"]["loader_class"]}.{action["loader"]["input_key"]}`：`{action["loader"]["filename"]}` → `{action["filename"]}`，核对的是导出图中的实际字段。')
    for key,label in [('reuse','原资产复用'),('partial','局部演化'),('abstract_assets_preserved','保留 ATT/AWG'),
                      ('unaffected_graph_preserved','保留未受影响分支'),('real_catalog_model_replaced','实际替换目录模型'),
                      ('reusable_after_model_change','演化后复用且保留新模型'),('rollback_without_compatible_model','无兼容模型时回滚')]:
        lines.append(f'- {label}：{"通过" if evolution.get(key) else "未通过"}。')
    lines.append('')
    for case in results:
        if case['errors']:
            lines.extend([f'## {case["id"]} 诊断', '', *[f'- {error}' for error in case['errors']], ''])
    (args.output/'report.md').write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='cases'},ensure_ascii=False,indent=2))
    evolution_failed = bool(evolution) and not all(evolution.get(k) for k in ('reuse','partial','abstract_assets_preserved','unaffected_graph_preserved',
        'real_catalog_model_replaced','reusable_after_model_change','rollback_without_compatible_model'))
    return int(report['failed']>0 or evolution_failed)


if __name__ == '__main__':
    raise SystemExit(main())
