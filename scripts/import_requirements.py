#!/usr/bin/env python3
"""Merge the paper's 200 ComfyBench and 20 compositional requirements."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agentictree.storage import write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--comfybench', required=True, type=Path)
    parser.add_argument('--compositional', required=True, type=Path)
    parser.add_argument('--output', type=Path, default=ROOT/'data/benchmarks/requirements_220.json')
    args = parser.parse_args()
    public_raw = args.comfybench.read_bytes()
    composed_raw = args.compositional.read_bytes()
    public = json.loads(public_raw)
    if len(public) != 200 or set(public) != {f'{n:03d}' for n in range(1, 201)}:
        raise ValueError('Expected the 200 numbered ComfyBench requirements')
    if Counter(r['category'] for r in public.values()) != {'vanilla': 100, 'complex': 60, 'creative': 40}:
        raise ValueError('Unexpected ComfyBench category counts')
    matches = list(re.finditer(r'(?m)^\s*(\d{1,2})[ \t]*\r?$', composed_raw.decode('utf-8')))
    if [int(m[1]) for m in matches] != list(range(1, 21)):
        raise ValueError('Expected exactly 20 numbered compositional requirements')
    text = composed_raw.decode('utf-8')
    rows = [{**public[key], 'id': key, 'source': {'id': 'comfybench', 'original_id': key}}
            for key in sorted(public)]
    for index, match in enumerate(matches):
        instruction = text[match.end():matches[index+1].start() if index+1 < len(matches) else len(text)].strip()
        if not instruction:
            raise ValueError('Empty compositional requirement')
        rows.append({'id': f'{201+index:03d}', 'name': f'compositional_{index+1:02d}',
                     'instruction': instruction, 'resource': None, 'modality': 'multimodal',
                     'category': 'compositional',
                     'source': {'id': 'compositional', 'original_id': str(index+1)}})
    sources = [
        {'id': 'comfybench', 'original_path': str(args.comfybench.resolve()),
         'project_path': 'sources/comfybench_complete.json',
         'sha256': hashlib.sha256(public_raw).hexdigest(), 'count': 200,
         'resource_root': str((args.comfybench.parent.parent/'resource').resolve())},
        {'id': 'compositional', 'original_path': str(args.compositional.resolve()),
         'project_path': 'sources/compositional_requirements.md',
         'sha256': hashlib.sha256(composed_raw).hexdigest(), 'count': 20},
    ]
    dataset = {'schema_version': 1, 'name': 'Thesis requirements (ComfyBench + Compositional)',
               'count': len(rows), 'category_counts': dict(Counter(r['category'] for r in rows)),
               'sources': sources, 'requirements': rows}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    raw_dir = args.output.parent/'sources'
    raw_dir.mkdir(exist_ok=True)
    (raw_dir/'comfybench_complete.json').write_bytes(public_raw)
    (raw_dir/'compositional_requirements.md').write_bytes(composed_raw)
    write_json(args.output, dataset)
    lines = ['# 论文实验需求（220 条）', '', 'Vanilla 100 / Complex 60 / Creative 40 / Compositional 20。',
             '', '保留来源中的需求原文；输入素材文件名见每条需求。', '']
    for row in rows:
        lines.extend([f'## {row["id"]} · {row["name"]}', '',
                      f'分类：{row["category"]}；模态：{row["modality"]}；输入素材：{row["resource"] or "无"}。',
                      '', row['instruction'], ''])
    args.output.with_suffix('.md').write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps({'path': str(args.output.resolve()), 'count': len(rows),
                      'category_counts': dataset['category_counts']}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
