#!/usr/bin/env python3
"""Smoke the maintained ACT, generation, reuse and local evolution without ComfyUI."""
from pathlib import Path
import copy
import json
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from agentictree.storage import read_json,write_json,digest
from agentictree.knowledge import active_capabilities
from agentictree.assets import generate_asset
from agentictree.evolution import evolve_asset
from agentictree.catalog_contracts import node_fields,set_graph_field


def main():
    snapshot=read_json(ROOT/'data/act/latest.json')
    with tempfile.TemporaryDirectory(prefix='agentictree-smoke-') as temp:
        temp=Path(temp)
        caps=active_capabilities(snapshot,temp/'caps',{'core.generative.text_to_image'})
        remote=sum(key.startswith('remote:') for key in caps)
        assert remote>0,'Maintained official examples did not enter the candidate set'
        official=generate_asset('Create a blue square image',snapshot,temp/'official',
                                excluded_implementations={key for key in caps if not key.startswith('remote:')})
        assert official['status']=='validated_offline',official['generation']['failure_reason']
        assert official['execution_plan']['nodes'][0]['atomic_id'].startswith('remote:')
        asset=generate_asset('Create a blue square image',snapshot,temp/'generated')
        assert asset['status']=='validated_offline',asset['generation']['failure_reason']
        assert asset['att']['tasks'] and 'atomic_id' not in json.dumps(asset['awg'])
        reuse=evolve_asset(asset,snapshot,snapshot,temp/'reuse')
        assert reuse['committed'] and reuse['classification']=='reuse'
        changed=copy.deepcopy(snapshot)
        node=asset['execution_plan']['nodes'][0]
        record=changed['records'][node['atomic_id']]
        graph=copy.deepcopy(record.get('graph') or read_json(Path(record['graph_object'])))
        fmt=record.get('workflow_format','api')
        writable=next((v for v in node_fields(graph,fmt) if v[2]=='filename_prefix'),None)
        assert writable,'Smoke needs a real image output component'
        nid,cls,key,value,widget,title=writable
        set_graph_field(graph,{'node_id':nid,'input_key':key,'widget_index':widget},str(value)+'_evolved')
        if 'graph' in record:
            record['graph']=graph
        else:
            path=temp/'changed_graph.json';write_json(path,graph);record['graph_object']=str(path)
        record['workflow_sha256']=digest(graph)
        changed['version']='smoke-act-'+digest(graph)[:12]
        partial=evolve_asset(asset,snapshot,changed,temp/'partial')
        assert partial['committed'] and partial['classification']=='partial',partial['diagnostics']
        assert partial['candidate']['att']==asset['att'] and partial['candidate']['awg']==asset['awg']
        failures={}
        for intent in ['Transcribe this audio into text','Summarize a legal contract']:
            result=generate_asset(intent,snapshot,temp/'negative')
            assert result['status']=='failed',intent
            failures[intent]='correctly rejected without required input/semantic plan'
        print(json.dumps({'act_version':snapshot['version'],'four_level_tree':snapshot['validation']['four_level_tree'],
            'remote_text_to_image_candidates':remote,'generation':asset['status'],
            'official_example_generation':official['status'],
            'official_implementation':official['execution_plan']['nodes'][0]['atomic_id'],
            'selected_implementation':node['atomic_id'],'format':asset['workflow_formats'][node['dag_id']],
            'reuse':reuse['committed'],'partial_evolution':partial['committed'],
            'abstract_assets_preserved':True,'negative_cases':failures,'comfyui_executed':False},ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
