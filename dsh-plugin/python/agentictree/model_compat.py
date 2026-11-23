"""Loader-level model compatibility, beyond modality labels alone."""
from __future__ import annotations

import re


def pixel_upscale_factor(model):
    metadata=model.get('metadata',{})
    filename=str(metadata.get('filename',''))
    if not filename.casefold().endswith('.pth') or str(metadata.get('type','')).casefold() not in {'upscale','rgt','esrgan','swinir'}:
        return None
    # Pixel upscalers have explicit xN/Nx scales in these catalog filenames.
    # LTX spatial/temporal latent upscalers are deliberately not interchangeable.
    found=re.search(r'(?:^|[_-])(?:x(\d+)|(\d+)x)(?=[_.-]|$)',filename,re.I)
    return int(found[1] or found[2]) if found else None


def compatible_model(old,new,binding):
    if old.get('tree_branch')!=new.get('tree_branch'):
        return False
    old_six,new_six=old.get('six_tuple',{}),new.get('six_tuple',{})
    if any(old_six.get(k)!=new_six.get(k) for k in ('input_types','output_types','input_type','output_type')):
        return False
    if binding.get('loader_class')=='UpscaleModelLoader':
        scale=pixel_upscale_factor(old)
        return scale is not None and pixel_upscale_factor(new)==scale
    old_meta,new_meta=old.get('metadata',{}),new.get('metadata',{})
    return old_meta.get('type')==new_meta.get('type') and old_meta.get('base')==new_meta.get('base')
