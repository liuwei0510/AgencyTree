# AgenticTree for Workflow

This project implements the thesis chain **R → ATT → abstract AWG → implementation bindings → EWC**, with ACT maintenance and versioned workflow evolution. Agents use tools for catalog retrieval, parameter injection, graph construction, contract checks and local maintenance. Semantic agents handle decomposition or ambiguous abstract planning through a host callback, an external command, or an API.

## Setup

Python 3.10 or newer:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
export PYTHONPATH=.
```

Configuration is in `config.yaml`; `--config path.yaml` selects another configuration. Agent providers are `api` and `command`; a Python application can also pass an object with `complete_json(system,user)` or use `HostBackend`. See `configs/agents.*.example.yaml`. External commands receive one JSON request on stdin and return one JSON object. No model API key is needed when a host agent supplies a structured ATT/AWG directly.

## Maintain the ACT

`atomic_workflows/` is the on-disk four-level ACT:

```text
L1 core|aux / L2 task type / L3 abstract capability / L4 implementation instance
```

Core task types are `generative`, `editing`, `perception`; auxiliary types are `preprocess`, `postprocess`, `conditional`, `evaluate`, `latent_process`. L4 leaves contain local `*.manifest.yaml` workflows, `model.yaml` model assets, or `template.yaml` plus original official workflow JSON. Each detail record carries a six-tuple and source evidence. Retired leaves retain their history and are excluded from current selection.

```bash
python run_agent.py maintain --sync
python run_agent.py maintain
python run_agent.py catalog
python run_agent.py search "qwen image edit" --kind templates --limit 8
```

Sync fetches current ComfyUI-Manager models and official ComfyUI workflow examples at pinned repository commits. A complete new source snapshot updates the physical tree; partial fetches never imply deletion. Between source updates, local tree files are authoritative. ACT snapshots and content-addressed graph objects are stored under `data/act/`; `data/act/tree.md` renders the current tree.

ComfyUI-format usability is independent of local installation. Model files remain `available` when their catalog format is valid. Model assets are linked to concrete loader fields through `implemented_by` and generated `model_bindings`; a model download entry is not itself a whole workflow. Official examples with extractable interfaces enter the same implementation candidate set as local workflows. Others remain available for tool retrieval without fabricated parameter contracts.

Editor workflows retain their native format and may be loaded directly in the ComfyUI frontend. Conversion is optional for direct `/prompt` execution:

```bash
python scripts/convert_workflow_format.py workflow.json --output workflow-api.json
python scripts/convert_workflow_format.py workflow.json --object-info object_info.json
python run_agent.py sync --convert-editor
```

## Generate and validate assets

```bash
python run_agent.py plan "Create a watercolor landscape image" --output data/draft.json
python run_agent.py plan "Create an image then animate it" --agent-assisted
```

ATT contains task IDs, goals, L3 capability IDs, inputs, output types, constraints and dependencies. AWG contains abstract nodes, typed edges, ordering and constraints; it never contains concrete `atomic_id` bindings. Engineering tools choose detailed implementations and store them in `execution_plan`, `bindings`, `model_bindings`, `workflow_formats` and `ewc`.

The deterministic planner supports recognizable atomic tasks. Missing capabilities, missing media inputs, unsupported controls and wrong output modalities produce explicit failures. It never silently substitutes an image for audio, text or a mesh. Optional agent fallback receives compact abstract catalogs and diagnostics instead of complete graph bodies.

Codex, Cursor or another host can supply semantic plans as files and invoke the tools without a separate model API:

```bash
python run_agent.py plan "Draw a blue square image" --att task-tree.json --inputs inputs.json
python run_agent.py plan "Composite multimedia request" --att task-tree.json --awg abstract-graph.json
```

Example `task-tree.json`:

```json
{"tasks":[{"task_id":"t1","goal":"Draw a blue square image","capability_id":"core.generative.text_to_image","inputs":{},"outputs":{"images":"image"},"constraints":{},"depends_on":[]}],"edges":[],"diagnostics":[]}
```

Generated assets include `comfy_workflow` and `export_info`. Engineering tools assemble component graphs into one ComfyUI workflow, replacing intermediate filename placeholders with node connections and explicit image selectors. An unresolved media port or adapter fails the export. Export a saved asset with:

```bash
python run_agent.py export data/draft.json --output workflow-api.json
```

`validated_offline` means abstract task coverage, binding contracts and ComfyUI graph structure passed, including complete workflow assembly. It does not claim generated-media quality or actual execution. Direct `run` submits the assembled API workflow once; `--dry-run` checks the same path without contacting ComfyUI. A single native editor result retains its format for frontend import; `export --api` explicitly converts it when the node contracts permit conversion.

Selection checks task preconditions before accepting a candidate. Ordinary image alpha and canvas-border masks cannot stand in for an interior edit mask; tasks without target masks can select an instruction editing implementation. Known native video generators use their required frame alignment, with final batch trimming to preserve the requested duration. HD requirements bind actual output dimensions. Missing or unknown timing contracts fail explicitly.

## Evolve saved assets

```bash
python run_agent.py evolve data/workflows/versions/<workflow>.json --old-act data/act/versions/<act>.json
```

Evolution follows explicit implementation/model bindings, computes the affected nodes and incident edges, and emits model-change records, an abstract change plan, and concrete maintenance actions with event references and a rollback point. Reuse preserves valid assets. Partial maintenance preserves the abstract task goals and changes only affected bindings/parameters or inserts typed adapters. Unrecoverable capability/model interfaces trigger generation from the original requirement. Failed candidates are not committed. Candidate attempts are bounded by `max_iterations`.

Model replacement checks loader compatibility as well as abstract interfaces. Pixel upscalers retain the requested scale and cannot be replaced by latent upscalers with a matching coarse modality. Successful replacements update the actual loader fields, rebuild the complete export, and remain bound on subsequent reuse. A failed export cannot commit a candidate version.

## Offline checks

```bash
python -m unittest discover -s tests -v
python scripts/smoke_offline.py
```

The smoke uses the maintained tree and official candidates, generates an asset, checks reuse, changes a real output configuration in a temporary ACT copy, verifies partial evolution preserves ATT/AWG, and rejects invalid requests. It requires neither ComfyUI nor model API credentials.

## Thesis requirements

The paper's complete 220 requirements are merged in [data/benchmarks/requirements_220.json](data/benchmarks/requirements_220.json), with a [readable list](data/benchmarks/requirements_220.md) and both original source files. Counts are Vanilla 100, Complex 60, Creative 40 and Compositional 20. Original wording, media filenames and source IDs are preserved.

```bash
python scripts/smoke_requirements.py
```

This samples seven real requirements across all four categories and checks generation, complete API graph assembly, input/mask contracts, duration/resolution, reuse and model evolution. The evolution scenario deprecates the bound RealESRGAN 2x model in a temporary copy of the real ACT and checks replacement with a compatible catalog model, subsequent reuse, and rollback when none remains. Compound requirements use the host's saved ATT plans in `data/benchmarks/smoke_plans.json`, without a separate model API. The [report](data/benchmarks/smoke/latest/report.md), each complete `workflow_api.json`, ATT/AWG/EWC, and before/after evolution exports are retained for inspection. Offline success covers graph contracts and bindings; it does not claim rendered-media quality.

`scripts/refresh_workflow_contracts.py` exposes supported fields already present in local templates before ACT maintenance. Numeric constraints must have writable controls or an explicit fixed implementation contract; video duration can bind to unambiguous frame-count/fps fields. Unsupported quantitative controls fail explicitly.
