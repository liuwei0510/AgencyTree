from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from test_system import fixture, T2I
from agentictree.assets import generate_asset
from agentictree.knowledge import update_act, active_capabilities
from agentictree.evolution import evolve_asset
from agentictree.comfy_taxonomy import route_template
from agentictree.storage import digest, write_json, read_json


class ThesisAlignmentTests(unittest.TestCase):
    def test_tool_parser_preserves_hyphenated_duration_and_backtick_resource(self):
        from agentictree.thesis_pipeline import tool_analyze
        self.assertEqual(tool_analyze('Generate a 2-second video of a dog').tasks[0].constraints['duration'],2)
        self.assertEqual(tool_analyze('Upscale the image `room.png` by 2x').tasks[0].inputs['source_image'],'room.png')
        self.assertEqual(tool_analyze('Upscale the image `room.png` by 2x').tasks[0].constraints['scale_factor'],2)

    def test_numeric_constraints_need_controls_or_a_fixed_contract(self):
        from agentictree.thesis_pipeline import numeric_controls
        from agentictree.schemas import AtomicCapability, ManifestInput, InjectSpec
        cap=AtomicCapability(id='video',title='video',workflow_file='video.json',
            inputs=[ManifestInput(name='fps',default=24,required=False),ManifestInput(name='length',default=81,required=False)],
            inject={'fps':InjectSpec(node_id='1',input_key='fps'),'length':InjectSpec(node_id='2',input_key='length')})
        self.assertEqual(numeric_controls(cap,{'duration':2}),{'length':48,'fps':24})
        with self.assertRaises(ValueError):numeric_controls(cap,{'scale_factor':2})
        cap.fixed_constraints={'scale_factor':2}
        self.assertEqual(numeric_controls(cap,{'scale_factor':2}),{})
        with self.assertRaises(ValueError):numeric_controls(cap,{'scale_factor':4})

    def test_unsupported_quantitative_constraint_cannot_be_ignored(self):
        att={'tasks':[{'task_id':'draw','goal':'blue square image','capability_id':'core.generative.text_to_image',
                       'constraints':{'playback_speed':2},'outputs':{'images':'image'}}]}
        asset=generate_asset('Draw a blue square image',self.act(),self.assets,att=att)
        self.assertEqual(asset['status'],'failed')
        self.assertIn('playback_speed',asset['generation']['failure_reason'])

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.registry=self.root/'atomic_workflows'
        self.folder=fixture(self.registry)
        self.store=self.root/'act'
        self.assets=self.root/'assets'

    def act(self,source=None):
        return update_act(self.registry,self.store,source)

    def source(self,models=None,workflows=None,version='s1'):
        return {'version':version,'complete':True,'fresh':True,'provenance':{'models':{},'templates':{}},
                'models':models or [],'workflows':workflows or []}

    def model(self,name='model',filename='model.safetensors'):
        return {'name':name,'type':'checkpoint','base':'SDXL','save_path':'checkpoints',
                'filename':filename,'url':'https://example.org/'+filename}

    def test_abstract_assets_do_not_bind_implementations(self):
        asset=generate_asset('Create a blue square image',self.act(),self.assets)
        self.assertEqual(asset['status'],'validated_offline')
        self.assertEqual(asset['att']['tasks'][0]['capability_id'],'core.generative.text_to_image')
        self.assertNotIn('atomic_id',json.dumps(asset['awg']))
        self.assertEqual(asset['execution_plan']['nodes'][0]['atomic_id'],T2I)
        self.assertEqual(asset['bindings']['n1']['task_id'],'t1')

    def test_wrong_modality_cannot_be_reported_as_success(self):
        snapshot=self.act()
        for intent in ['Transcribe this audio into text','Generate a 3D mesh of a chair','Summarize a legal contract']:
            asset=generate_asset(intent,snapshot,self.assets)
            self.assertEqual(asset['status'],'failed',intent)
            self.assertIsNone(asset['ewc'])

    def test_host_att_can_be_consumed_without_model_api(self):
        att={'tasks':[{'task_id':'draw','goal':'blue square','capability_id':'core.generative.text_to_image',
                       'outputs':{'images':'image'},'constraints':{'width':512}}]}
        asset=generate_asset('Draw a blue square image',self.act(),self.assets,att=att)
        # The fixture has no writable width contract: cannot silently drop it.
        self.assertEqual(asset['status'],'failed')
        att['tasks'][0]['constraints']={}
        asset=generate_asset('Draw a blue square image',self.act(),self.assets,att=att)
        self.assertEqual(asset['status'],'validated_offline')
        self.assertEqual(asset['generation_mode'],'host_plan')

    def test_semantic_provider_cannot_turn_audio_into_image(self):
        class WrongAgent:
            def complete_json(self,system,user):
                return json.dumps({'tasks':[{'task_id':'t1','goal':'transcribe audio',
                    'capability_id':'core.generative.text_to_image','outputs':{'images':'image'}}]})
        asset=generate_asset('Transcribe audio to text',self.act(),self.assets,backend=WrongAgent())
        self.assertEqual(asset['status'],'failed')

    def test_native_editor_example_is_a_generation_candidate(self):
        (self.folder/'cap.manifest.yaml').unlink()
        editor={'nodes':[
            {'id':1,'type':'TextToImage','inputs':[{'name':'prompt','widget':{'name':'prompt'},'link':None}],
             'outputs':[{'name':'IMAGE','type':'IMAGE','links':[1]}],'widgets_values':['sample']},
            {'id':2,'type':'SaveImage','inputs':[{'name':'images','link':1}],
             'outputs':[],'widgets_values':['example']}],
            'links':[[1,1,0,2,0,'IMAGE']]}
        path=self.root/'source.json';write_json(path,editor)
        template={'name':'native_editor','metadata':{'title':'Text to Image','io':{'outputs':[{'mediaType':'image'}]}},
                  'path':str(path),'sha256':digest(editor),'url':'https://example.org/source.json','status':'available','format':'editor'}
        snapshot=self.act(self.source(workflows=[template]))
        caps=active_capabilities(snapshot,self.root/'cache')
        self.assertIn('remote:template:native_editor',caps)
        asset=generate_asset('Draw a blue square image',snapshot,self.assets)
        self.assertEqual(asset['status'],'validated_offline',asset['generation'])
        self.assertEqual(asset['workflow_formats']['n1'],'editor')
        self.assertEqual(asset['ewc']['n1']['nodes'][0]['widgets_values'][0],'Draw a blue square image')
        self.assertIn('nodes',asset['ewc']['n1'])

    def test_model_change_traces_to_loader_and_keeps_abstract_assets(self):
        graph=read_json(self.folder/'workflow.json')
        graph['3']={'class_type':'CheckpointLoaderSimple','inputs':{'ckpt_name':'old.safetensors'}}
        write_json(self.folder/'workflow.json',graph)
        old=self.act(self.source(models=[self.model('old','old.safetensors')]))
        asset=generate_asset('blue square image',old,self.assets)
        self.assertTrue(asset['model_bindings']['n1'][0]['model_id'])
        new=self.act(self.source(models=[self.model('new','new.safetensors')],version='s2'))
        report=evolve_asset(asset,old,new,self.assets)
        self.assertTrue(report['committed'],report['diagnostics'])
        self.assertEqual(report['classification'],'partial')
        self.assertEqual(report['affected_nodes'],['n1'])
        self.assertEqual(report['candidate']['ewc']['n1']['3']['inputs']['ckpt_name'],'new.safetensors')
        self.assertEqual(report['candidate']['awg'],asset['awg'])
        self.assertEqual(report['candidate']['att'],asset['att'])
        self.assertEqual(asset['ewc']['n1']['3']['inputs']['ckpt_name'],'old.safetensors')
        self.assertTrue(any(a['action']=='rebind_model' for a in report['executable_change_plan']))
        self.assertTrue(report['abstract_change_plan']['events']['n1'])

    def test_changed_model_contract_cannot_be_classified_as_reuse(self):
        graph=read_json(self.folder/'workflow.json')
        graph['3']={'class_type':'CheckpointLoaderSimple','inputs':{'ckpt_name':'model.safetensors'}}
        write_json(self.folder/'workflow.json',graph)
        old=self.act(self.source(models=[self.model()]))
        asset=generate_asset('blue square image',old,self.assets)
        new=copy.deepcopy(old);new['version']='changed'
        model_id=asset['model_bindings']['n1'][0]['model_id']
        new['records'][model_id]['six_tuple']['input_types']=['incompatible type']
        report=evolve_asset(asset,old,new,self.assets)
        self.assertNotEqual(report['classification'],'reuse')
        self.assertFalse(report['committed'])
        self.assertEqual(report['rollback_target'],asset['version'])

    def test_deprecated_model_is_not_a_current_tree_leaf(self):
        old=self.act(self.source(models=[self.model()]))
        new=self.act(self.source(version='removed'))
        model_id=next(k for k in old['records'] if k.startswith('remote:model:'))
        self.assertEqual(new['records'][model_id]['status'],'deprecated')
        current=[i for tasks in new['tree'].values() for caps in tasks.values() for ids in caps.values() for i in ids]
        self.assertNotIn(model_id,current)

    def test_title_and_declared_io_outrank_incidental_description(self):
        image={'name':'moss','metadata':{'title':'MOSS: Text to Image','description':'Reads a scene caption',
               'io':{'outputs':[{'mediaType':'image'}]}}}
        upscale={'name':'seedvr','metadata':{'title':'Image Upscale: SeedVR2',
                 'description':'A video restoration model','io':{'inputs':[{'mediaType':'image'}],'outputs':[{'mediaType':'image'}]}}}
        self.assertEqual(route_template(image),('core','generative','text_to_image'))
        self.assertEqual(route_template(upscale),('aux','postprocess','upscale'))

    def test_editing_filesystem_leaf_updates_act_without_resync(self):
        first=self.act(self.source(models=[self.model()]))
        import yaml
        path=next(self.registry.rglob('model.yaml'))
        doc=yaml.safe_load(path.read_text());doc['six_tuple']['parameters'].append({'name':'strength','default':0.5})
        path.write_text(yaml.safe_dump(doc))
        second=self.act()
        key=next(k for k in first['records'] if k.startswith('remote:model:'))
        self.assertNotEqual(first['records'][key]['six_tuple'],second['records'][key]['six_tuple'])
        self.assertTrue(any(c['type']=='interface_changed' and c['id']==key for c in second['changes']))

    def test_asr_requires_user_audio_even_if_template_has_sample_default(self):
        import yaml
        folder=self.registry/'core/perception/audio_recognition/asr'
        folder.mkdir(parents=True)
        graph={'1':{'class_type':'LoadAudio','inputs':{'audio':'sample.wav'}},
               '2':{'class_type':'Whisper','inputs':{'audio':['1',0]}},
               '3':{'class_type':'SaveText','inputs':{'text':['2',0],'filename_prefix':'transcript'}}}
        write_json(folder/'workflow.json',graph)
        doc={'id':'core.perception.audio_recognition.asr','workflow_file':'workflow.json',
             'inputs':[{'name':'audio_file','type':'audio_path','required':False,'default':'sample.wav'}],
             'inject':{'audio_file':{'node_id':'1','input_key':'audio'}},
             'outputs':[{'name':'transcript','type':'text'}]}
        (folder/'asr.manifest.yaml').write_text(yaml.safe_dump(doc))
        snapshot=self.act()
        missing=generate_asset('Transcribe audio into text',snapshot,self.assets)
        self.assertEqual(missing['status'],'failed')
        supplied=generate_asset('Transcribe audio into text',snapshot,self.assets,inputs={'audio_file':'provided.wav'})
        self.assertEqual(supplied['status'],'validated_offline',supplied['generation'])
        self.assertEqual(supplied['ewc']['n1']['1']['inputs']['audio'],'provided.wav')
        self.assertNotIn('SaveImage',json.dumps(supplied['ewc']))

    def test_typed_host_plan_builds_image_then_video_without_early_model_binding(self):
        import yaml
        folder=self.registry/'core/editing/image_to_video/video'
        folder.mkdir(parents=True)
        write_json(folder/'workflow.json',{
            '1':{'class_type':'LoadImage','inputs':{'image':'sample.png'}},
            '2':{'class_type':'ImageToVideo','inputs':{'image':['1',0],'prompt':'sample'}},
            '3':{'class_type':'SaveVideo','inputs':{'video':['2',0],'filename_prefix':'video'}}})
        (folder/'video.manifest.yaml').write_text(yaml.safe_dump({
            'id':'core.editing.image_to_video.video','workflow_file':'workflow.json',
            'inputs':[{'name':'source_image','type':'image_filenames','required':True},
                      {'name':'prompt','type':'string','required':True}],
            'inject':{'source_image':{'node_id':'1','input_key':'image'},'prompt':{'node_id':'2','input_key':'prompt'}},
            'outputs':[{'name':'video','type':'video_path'}]}))
        att={'tasks':[
            {'task_id':'draw','goal':'blue square','capability_id':'core.generative.text_to_image','outputs':{'images':'image'}},
            {'task_id':'animate','goal':'animate the blue square','capability_id':'core.editing.image_to_video',
             'outputs':{'video':'video'},'depends_on':['draw']}]}
        asset=generate_asset('Create an image then animate it',self.act(),self.assets,att=att)
        self.assertEqual(asset['status'],'validated_offline',asset['generation'])
        self.assertEqual(asset['awg']['ordering'],[['n1','n2']] if isinstance(asset['awg']['ordering'][0],list) else [('n1','n2')])
        self.assertEqual(asset['awg']['edges'][0]['input'],'source_image')
        self.assertNotIn('atomic_id',json.dumps(asset['awg']))
        self.assertEqual(asset['execution_plan']['nodes'][1]['bindings']['source_image']['from_node'],'n1')
        self.assertEqual(asset['bindings']['n2']['task_id'],'animate')

    def test_replacement_must_preserve_abstract_output_and_retry_alternatives(self):
        old=self.act(); asset=generate_asset('blue square image',old,self.assets)
        (self.folder/'cap.manifest.yaml').unlink()
        fixture(self.registry,'core.generative.text_to_image.a_wrong',output_type='text')
        fixture(self.registry,'core.generative.text_to_image.z_correct')
        report=evolve_asset(asset,old,self.act(),self.assets)
        self.assertTrue(report['committed'],report['diagnostics'])
        self.assertTrue(report['candidate']['execution_plan']['nodes'][0]['atomic_id'].endswith('z_correct'))
        self.assertTrue(any(not attempt['ok'] for attempt in report['attempts']))
        self.assertEqual(report['candidate']['awg'],asset['awg'])

    def test_invalid_current_template_is_not_replaced_by_a_retired_path(self):
        import yaml
        raw={'nodes':[],'links':[]}
        source_path=self.root/'invalid.json';write_json(source_path,raw)
        template={'name':'invalid','metadata':{'title':'Text to Image'},'path':str(source_path),
                  'sha256':digest(raw),'url':'https://example.org/invalid.json','status':'available','format':'editor'}
        first=self.act(self.source(workflows=[template]))
        current=next(p for p in self.registry.rglob('template.yaml'))
        retired=self.registry/'core/generative/retired/old'
        retired.mkdir(parents=True)
        data=yaml.safe_load(current.read_text());data['status']='deprecated'
        (retired/'template.yaml').write_text(yaml.safe_dump(data))
        second=self.act()
        record=second['records']['remote:template:invalid']
        self.assertEqual(record['status'],'unusable_format')
        self.assertFalse(record['code_usable'])
        self.assertTrue(record['validation_errors'])

    def test_nested_editor_model_bindings_track_only_used_subgraphs(self):
        from agentictree.catalog_contracts import bind_models, set_graph_field
        graph={'nodes':[{'id':1,'type':'used'}],'links':[], 'definitions':{'subgraphs':[
            {'id':'used','nodes':[{'id':2,'type':'CheckpointLoaderSimple','widgets_values':['old.safetensors']}],'links':[]},
            {'id':'unused','nodes':[{'id':3,'type':'CheckpointLoaderSimple','widgets_values':['unused.safetensors']}],'links':[]}]}}
        records={'old':{'kind':'comfyui_model','status':'available','metadata':{'type':'checkpoint','filename':'old.safetensors'}}}
        rows=bind_models({'n1':graph},{'n1':'editor'},records)['n1']
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['model_id'],'old')
        self.assertEqual(rows[0]['graph_path'],['definitions','subgraphs',0])
        set_graph_field(graph,rows[0],'new.safetensors')
        self.assertEqual(graph['definitions']['subgraphs'][0]['nodes'][0]['widgets_values'],['new.safetensors'])
        self.assertEqual(graph['definitions']['subgraphs'][1]['nodes'][0]['widgets_values'],['unused.safetensors'])

    def test_ambiguous_changed_model_binding_cannot_be_reused(self):
        old=self.act(self.source(models=[self.model('first'),self.model('second')]))
        asset=generate_asset('blue square image',old,self.assets)
        ids=[key for key,r in old['records'].items() if r.get('kind')=='comfyui_model']
        self.assertEqual(len(ids),2)
        asset['model_bindings']={'n1':[{'model_id':None,'candidate_model_ids':ids,'filename':'model.safetensors'}]}
        changed=copy.deepcopy(old);changed['version']='changed'
        changed['records'][ids[0]]['status']='deprecated'
        report=evolve_asset(asset,old,changed,self.assets)
        self.assertNotEqual(report['classification'],'reuse')
        self.assertEqual(report['affected_nodes'],['n1'])
