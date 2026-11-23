# Abstract Capability Tree

This directory is the actual ACT. The path is `L1 strategic domain / L2 task type / L3 abstract capability / L4 implementation instance`.

L1 is `core` or `aux`. Core task types are `generative`, `editing`, `perception`. Auxiliary types are `preprocess`, `postprocess`, `conditional`, `evaluate`, `latent_process`.

L4 leaves contain local workflow manifests, `model.yaml` model assets, or official `template.yaml` examples with their native JSON graphs. Format-compliant examples remain available; callable interfaces are extracted from actual graph fields. Generation tools select implementations only after abstract ATT/AWG planning. Model assets bind through compatible workflow loader fields.

`python run_agent.py maintain --sync` refreshes official source snapshots and this tree. `maintain` reads local leaves between source updates. Deprecated paths are retained as history, excluded from current candidates. The current projection is `data/act/tree.md`; `run_agent.py search` retrieves concise references without dumping workflow bodies.
