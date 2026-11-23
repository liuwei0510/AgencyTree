"""Deterministic ComfyUI preconditions and video frame contracts."""
from __future__ import annotations

import math
import re

# These native nodes encode T as ((length - 1) // 4) + 1. Decoding
# reconstructs 4n+1 frames; arbitrary requested counts need a final trim.
FRAME_RULES = {'EmptyHunyuanLatentVideo': (4,1), 'WanImageToVideo': (4,1)}


def is_link(value):
    return isinstance(value,list) and len(value)==2 and isinstance(value[0],str) and isinstance(value[1],int)


def ancestors(graph, start):
    seen, pending = set(), [start]
    while pending:
        key = pending.pop()
        if key in seen or key not in graph:
            continue
        seen.add(key)
        pending.extend(v[0] for v in graph[key].get('inputs',{}).values() if is_link(v))
    return seen


def mask_issues(graph, supplied=None):
    """Alpha from an ordinary input image is not a task-specific edit mask."""
    if 'nodes' in graph:
        from agentictree.editor_to_api import convert_editor_workflow
        converted, _ = convert_editor_workflow(graph)
        if converted is None:
            classes={n.get('type') for n in graph.get('nodes',[])}
            return ['Masked editor template needs a resolvable mask contract'] if 'InpaintModelConditioning' in classes else []
        graph=converted
    issues=[]
    supplied=supplied or {}
    has_user_mask=any('mask' in k.casefold() and k.casefold()!='mask_prompt' and isinstance(v,str) and v.strip()
                      for k,v in supplied.items())
    prompt=' '.join(str(v) for k,v in supplied.items() if 'prompt' in k.casefold() or k=='instruction')
    outpaint=bool(re.search(r'\b(?:outpaint(?:ing)?|expand|extend|uncrop)\b|扩图|外补|扩展画布|扩展图像',prompt,re.I))
    for key,node in graph.items():
        if not isinstance(node,dict):
            continue
        for name,ref in node.get('inputs',{}).items():
            if 'mask' not in name.casefold() or not is_link(ref):
                continue
            source=graph.get(ref[0],{})
            if source.get('class_type')=='LoadImage' and ref[1]==1:
                issues.append(f'{key}.{name}: ordinary image alpha is not an explicit target mask; supply a mask loader/segmentation or use instruction editing')
            if source.get('class_type')=='LoadImageMask' and not has_user_mask:
                issues.append(f'{key}.{name}: explicit user mask input is missing')
            elif source.get('class_type')=='ImagePadForOutpaint':
                if not outpaint:
                    issues.append(f'{key}.{name}: a canvas-border mask cannot implement an interior object edit; use a target mask or instruction editing')
            elif source.get('class_type') not in {'LoadImage','LoadImageMask'}:
                upstream=[graph[n] for n in ancestors(graph,ref[0])]
                explicit=has_user_mask and any(n.get('class_type')=='LoadImageMask' for n in upstream)
                targeted=bool(supplied.get('mask_prompt')) and any(
                    any(word in n.get('class_type','').casefold() for word in ('segment','sam')) for n in upstream)
                if not explicit and not targeted:
                    issues.append(f'{key}.{name}: mask provenance has no explicit user region or target segmentation')
    return issues


def resolution_issues(graph, constraints):
    if 'nodes' in graph or not all(k in constraints for k in ('width','height')):
        return []
    short=min(constraints['width'],constraints['height'])
    for node in graph.values():
        if not isinstance(node,dict) or node.get('class_type')!='UNETLoader':
            continue
        filename=str(node.get('inputs',{}).get('unet_name',''))
        match=re.search(r'(?:^|[_-])(\d{3,4})p(?:[_\-.]|$)',filename,re.I)
        if match and int(match[1])<short:
            return [f'{filename}: declared model resolution is below the requested {constraints["width"]}x{constraints["height"]}; select a suitable model or an explicit upscaling stage']
    return []


def _generator(graph, images):
    if not is_link(images):
        return None
    matches=[key for key in ancestors(graph,images[0])
             if graph[key].get('class_type') in FRAME_RULES]
    if len(matches)>1:
        raise ValueError('Ambiguous video frame generators; an explicit output frame contract is required')
    return matches[0] if matches else None


def align_video_frames(graph):
    """Pad native generation to valid latent lengths, trim at the video output."""
    if 'nodes' in graph:
        return graph
    requested_lengths={key:node.get('inputs',{}).get('length') for key,node in graph.items()
                       if isinstance(node,dict) and node.get('class_type') in FRAME_RULES}
    for key,node in list(graph.items()):
        if not isinstance(node,dict) or node.get('class_type')!='CreateVideo':
            continue
        images=node['inputs'].get('images')
        generator=_generator(graph,images)
        if generator is None:
            continue
        source=graph[generator]
        length=requested_lengths[generator]
        if not isinstance(length,(int,float)) or length<1 or int(length)!=length:
            raise ValueError('Video length must be a positive integer')
        stride,offset=FRAME_RULES[source['class_type']]
        requested=int(length)
        if (requested-offset)%stride==0:
            continue
        source['inputs']['length']=math.ceil((requested-offset)/stride)*stride+offset
        trim=f'{key}__trim_frames'
        if trim in graph:
            raise ValueError('Video trim node ID collision')
        graph[trim]={'class_type':'ImageFromBatch','inputs':{'image':images,'batch_index':0,'length':requested},
                     '_meta':{'title':f'Trim generated video to {requested} output frames'}}
        node['inputs']['images']=[trim,0]
    return graph


def video_contracts(graph):
    """Describe actual output counts after padding/trim, not raw length widgets."""
    if 'nodes' in graph:
        from agentictree.editor_to_api import convert_editor_workflow
        graph,issues=convert_editor_workflow(graph)
        if graph is None:
            return []
    outputs=[]
    for key,node in graph.items():
        if not isinstance(node,dict) or node.get('class_type')!='CreateVideo':
            continue
        inputs=node['inputs'];generator=_generator(graph,inputs.get('images'))
        if generator is None:
            continue
        source=graph[generator];length=source['inputs']['length']
        stride,offset=FRAME_RULES[source['class_type']]
        if not isinstance(length,int) or length<1 or (length-offset)%stride:
            raise ValueError(f'{generator}: invalid {stride}n+{offset} native video length {length}')
        count=length;images=inputs.get('images');trim=graph.get(images[0],{}) if is_link(images) else {}
        if trim.get('class_type')=='ImageFromBatch':
            start=trim['inputs'].get('batch_index',0);count=trim['inputs'].get('length')
            if not isinstance(start,int) or not isinstance(count,int) or start<0 or count<1 or start+count>length:
                raise ValueError('Video trim exceeds the generated frame range')
        fps=inputs.get('fps')
        if not isinstance(fps,(int,float)) or fps<=0:
            raise ValueError('Video FPS must be positive')
        outputs.append({'node_id':key,'generator':generator,'generator_class':source['class_type'],
                        'generated_frames':length,'output_frames':count,'fps':fps,'duration':count/fps,
                        'width':source['inputs'].get('width'),'height':source['inputs'].get('height')})
    return outputs


def validate_video_constraints(graph, constraints):
    errors=[]
    if 'nodes' in graph:
        from agentictree.editor_to_api import convert_editor_workflow
        converted,_=convert_editor_workflow(graph)
        if converted is None:
            return ['Video timing requires a verifiable node/frame contract'] if 'duration' in constraints else []
        graph=converted
    try:
        outputs=video_contracts(graph)
    except ValueError as error:
        return [str(error)]
    if 'duration' in constraints and sum(n.get('class_type')=='CreateVideo' for n in graph.values() if isinstance(n,dict))>len(outputs):
        errors.append('CreateVideo output has no verified native frame alignment contract')
    for output in outputs:
        for key in ('duration','fps','width','height'):
            if key in constraints and (not isinstance(output.get(key),(int,float)) or abs(output[key]-constraints[key])>1e-6):
                errors.append(f'{output["node_id"]}: actual video {key}={output.get(key)} differs from {constraints[key]}')
    return errors


def validate_upscale_constraints(graph, constraints):
    if 'scale_factor' not in constraints or 'nodes' in graph:
        return []
    from agentictree.model_compat import pixel_upscale_factor
    expected=constraints['scale_factor'];errors=[]
    for key,node in graph.items():
        if not isinstance(node,dict) or node.get('class_type')!='ImageUpscaleWithModel':
            continue
        ref=node['inputs'].get('upscale_model');loader=graph.get(ref[0],{}) if is_link(ref) else {}
        factor=pixel_upscale_factor({'metadata':{'filename':loader.get('inputs',{}).get('model_name',''),'type':'upscale'}})
        if factor!=expected:
            errors.append(f'{key}: actual pixel-upscaler scale {factor} differs from {expected}')
    return errors
