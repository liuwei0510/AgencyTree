from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml

from agentictree.assets import generate_asset
from agentictree.compose import compose_workflow
from agentictree.engineering import align_video_frames,mask_issues,video_contracts,validate_video_constraints
from agentictree.evolution import evolve_asset
from agentictree.graph_validate import validate_api_graph
from agentictree.knowledge import update_act
from agentictree.model_compat import compatible_model
from agentictree.storage import write_json
from test_system import fixture


def video_graph(length=48):
    return {'0':{'class_type':'VAELoader','inputs':{'vae_name':'video.safetensors'}},
            '1':{'class_type':'EmptyHunyuanLatentVideo','inputs':{'width':1280,'height':720,'length':length,'batch_size':1}},
            '2':{'class_type':'TestSampler','inputs':{'latent':['1',0],'prompt':'sample'}},
            '3':{'class_type':'VAEDecode','inputs':{'samples':['2',0],'vae':['0',0]}},
            '4':{'class_type':'CreateVideo','inputs':{'images':['3',0],'fps':24}},
            '5':{'class_type':'SaveVideo','inputs':{'video':['4',0],'filename_prefix':'video'}}}


class EngineeringTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);self.registry=self.root/'atomic_workflows'
        self.act_dir=self.root/'act';self.assets=self.root/'assets'

    def add(self,identifier,graph,inputs,inject,outputs=None):
        folder=self.registry.joinpath(*identifier.split('.'));folder.mkdir(parents=True)
        write_json(folder/'workflow.json',graph)
        data={'id':identifier,'workflow_file':'workflow.json','inputs':inputs,'inject':inject,
              'outputs':outputs or [{'name':'images','type':'image_filenames'}]}
        (folder/'cap.manifest.yaml').write_text(yaml.safe_dump(data))
        return folder

    def test_padding_and_trimming_preserve_exact_duration(self):
        graph=video_graph();align_video_frames(graph)
        self.assertEqual(graph['1']['inputs']['length'],49)
        self.assertEqual(graph['4__trim_frames']['inputs']['length'],48)
        timing=video_contracts(graph)[0]
        self.assertEqual((timing['generated_frames'],timing['output_frames'],timing['duration']),(49,48,2))
        self.assertEqual(validate_api_graph(graph),[])
        self.assertEqual(validate_video_constraints(graph,{'duration':2,'width':1280,'height':720}),[])
        self.assertTrue(validate_video_constraints(graph,{'duration':3}))

    def test_invalid_native_length_is_not_mistaken_for_two_seconds(self):
        self.assertTrue(validate_video_constraints(video_graph(48),{'duration':2}))

    def test_two_outputs_share_the_same_padded_generation(self):
        graph=video_graph();graph['6']=copy.deepcopy(graph['4'])
        align_video_frames(graph)
        self.assertEqual([o['output_frames'] for o in video_contracts(graph)],[48,48])

    def test_unknown_video_frame_contract_is_rejected(self):
        graph=video_graph();graph['1']['class_type']='UnknownTemporalCodec'
        self.assertIn('no verified native frame',str(validate_video_constraints(graph,{'duration':2})))

    def test_generate_hd_video_binds_output_dimensions_and_valid_frames(self):
        identifier='core.generative.text_to_video.test_video'
        self.add(identifier,video_graph(81),
            [{'name':'prompt','type':'string','required':True}]+[
                {'name':n,'type':'number','required':False,'default':v} for n,v in [('length',81),('fps',24),('width',848),('height',480)]],
            {n:{'node_id':node,'input_key':n} for n,node in [('prompt','2'),('length','1'),('fps','4'),('width','1'),('height','1')]},
            [{'name':'video','type':'video_path'}])
        snapshot=update_act(self.registry,self.act_dir)
        asset=generate_asset('Generate a 2-second high-definition video of a dog',snapshot,self.assets)
        self.assertEqual(asset['status'],'validated_offline',asset['generation']['failure_reason'])
        output=video_contracts(asset['comfy_workflow'])[0]
        self.assertEqual((output['width'],output['height'],output['duration']),(1280,720,2))
        self.assertEqual(output['generated_frames'],49)

    def test_mask_free_instruction_editor_is_selected_instead_of_unmasked_fill(self):
        base={'1':{'class_type':'LoadImage','inputs':{'image':'sample.png'}},
              '2':{'class_type':'TestInstructionEdit','inputs':{'image':['1',0],'prompt':'sample'}},
              '3':{'class_type':'SaveImage','inputs':{'images':['2',0],'filename_prefix':'edit'}}}
        ports=[{'name':'source_image','type':'image_path','required':True},{'name':'prompt','type':'string','required':True}]
        inject={'source_image':{'node_id':'1','input_key':'image'},'prompt':{'node_id':'2','input_key':'prompt'}}
        masked=copy.deepcopy(base);masked['2']['inputs']['mask']=['1',1]
        self.add('core.editing.image_editing.a_fill',masked,ports,inject)
        self.add('core.editing.image_editing.b_instruction',base,ports,inject)
        snapshot=update_act(self.registry,self.act_dir)
        att={'tasks':[{'task_id':'edit','goal':'Remove the fork from this image','capability_id':'core.editing.image_editing',
                        'inputs':{'source_image':'table.png'},'outputs':{'images':'image'}}]}
        asset=generate_asset('Remove the fork from this image',snapshot,self.assets,att=att)
        self.assertEqual(asset['status'],'validated_offline',asset['generation']['failure_reason'])
        self.assertTrue(asset['execution_plan']['nodes'][0]['atomic_id'].endswith('b_instruction'))
        self.assertTrue(mask_issues(masked,{'source_image':'table.png'}))

    def test_separate_mask_loader_needs_a_user_mask(self):
        graph={'1':{'class_type':'LoadImageMask','inputs':{'image':'sample.png','channel':'red'}},
               '2':{'class_type':'MaskConsumer','inputs':{'mask':['1',0]}}}
        self.assertTrue(mask_issues(graph,{}))
        self.assertTrue(mask_issues(graph,{'mask_prompt':'the fork'}))
        self.assertEqual(mask_issues(graph,{'mask_file':'fork_mask.png'}),[])

    def test_outpaint_border_mask_cannot_satisfy_an_interior_object_edit(self):
        graph={'1':{'class_type':'ImagePadForOutpaint','inputs':{'left':128,'right':128}},
               '2':{'class_type':'InpaintModelConditioning','inputs':{'mask':['1',1]}}}
        self.assertTrue(mask_issues(graph,{'prompt':'Remove the fork from the table'}))
        self.assertEqual(mask_issues(graph,{'prompt':'Extend the image by 128 pixels on each side'}),[])

    def chain_asset(self):
        source={'1':{'class_type':'EmptyImage','inputs':{'width':512,'height':512,'batch_size':1,'color':0}},
                '2':{'class_type':'SaveImage','inputs':{'images':['1',0],'filename_prefix':'first'}}}
        target={'1':{'class_type':'LoadImage','inputs':{'image':'__PLACEHOLDER_n1_images_0.png'}},
                '2':{'class_type':'ImageScaleBy','inputs':{'image':['1',0],'upscale_method':'bicubic','scale_by':2}},
                '3':{'class_type':'SaveImage','inputs':{'images':['2',0],'filename_prefix':'second'}}}
        return {'execution_plan':{'nodes':[{'dag_id':'n1','atomic_id':'draw'},
                    {'dag_id':'n2','atomic_id':'scale','depends_on':['n1'],'bindings':{'source_image':{'from_node':'n1','output':'images'}}}]},
                'ewc':{'n1':source,'n2':target},'workflow_formats':{'n1':'api','n2':'api'},
                'capabilities':{'draw':{'outputs':[{'name':'images','type':'image_filenames'}]},
                    'scale':{'outputs':[{'name':'images','type':'image_filenames'}],
                             'inject':{'source_image':{'node_id':'1','input_key':'image'}}}}}

    def test_complete_api_export_replaces_file_bridge_with_typed_links(self):
        graph,info=compose_workflow(self.chain_asset())
        self.assertTrue(info['complete'],info['issues'])
        self.assertEqual(validate_api_graph(graph),[])
        self.assertNotIn('__PLACEHOLDER_',json.dumps(graph))
        self.assertNotIn('n2::1',graph)
        selector=graph['n2::__input_source_image']
        self.assertEqual(selector['inputs']['image'],['n1::1',0])
        self.assertEqual(graph['n2::2']['inputs']['image'],['n2::__input_source_image',0])
        self.assertEqual(len(info['cross_component_edges']),1)

    def test_export_rejects_unknown_secondary_loader_outputs(self):
        asset=self.chain_asset();asset['ewc']['n2']['2']['inputs']['mask']=['1',1]
        graph,info=compose_workflow(asset)
        self.assertIsNone(graph);self.assertFalse(info['complete'])

    def test_pixel_model_replacement_preserves_scale_and_loader_contract(self):
        def model(name,kind='upscale',base='upscale'):
            return {'tree_branch':['aux','postprocess','upscale'],'metadata':{'filename':name,'type':kind,'base':base},
                    'six_tuple':{'input_types':['image'],'output_types':['image']}}
        binding={'loader_class':'UpscaleModelLoader'}
        old=model('RealESRGAN_x2.pth')
        self.assertTrue(compatible_model(old,model('RGT_x2.pth','RGT','RGT'),binding))
        self.assertFalse(compatible_model(old,model('RealESRGAN_x4.pth'),binding))
        self.assertFalse(compatible_model(old,model('ltx-spatial-upscaler-x2.safetensors'),binding))

    def test_export_failure_cannot_commit_a_partially_constructed_candidate(self):
        fixture(self.registry);snapshot=update_act(self.registry,self.act_dir)
        asset=generate_asset('A blue square image',snapshot,self.assets)
        before=set((self.assets/'versions').glob('*.json'))
        with patch('agentictree.compose.attach_export',side_effect=ValueError('export rejected')):
            report=evolve_asset(asset,snapshot,snapshot,self.assets)
        self.assertFalse(report['committed']);self.assertIsNone(report['candidate'])
        self.assertEqual(before,set((self.assets/'versions').glob('*.json')))
        self.assertIn('export rejected',str(report['diagnostics']))


if __name__=='__main__':
    unittest.main()
