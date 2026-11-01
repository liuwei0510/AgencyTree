from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field


class InjectSpec(BaseModel):
    node_id: str
    input_key: str
    widget_index: int | None = None


class ManifestInput(BaseModel):
    name: str
    type: str = "string"
    required: bool = True
    default: Any | None = None
    description: str | None = None


class ManifestOutput(BaseModel):
    name: str
    type: str = "string"
    description: str | None = None


class AtomicCapability(BaseModel):
    id: str
    title: str
    modality: list[str] = Field(default_factory=list)
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    workflow_file: str
    workflow_format: Literal["api", "editor"] = "api"
    inject: dict[str, InjectSpec] = Field(default_factory=dict)
    inputs: list[ManifestInput] = Field(default_factory=list)
    outputs: list[ManifestOutput] = Field(default_factory=list)
    fixed_constraints: dict[str, Any] = Field(default_factory=dict)
    workflow_sha256: str | None = None
    manifest_path: Path | None = None
    tree_branch: tuple[str, str, str] | None = None

    def workflow_path(self) -> Path:
        if not self.manifest_path:
            raise ValueError("manifest_path not set")
        return self.manifest_path.parent / self.workflow_file


class BindingRef(BaseModel):
    from_node: str
    output: str = "images"
    index: int = 0


class PlanNode(BaseModel):
    """One vertex in the abstract workflow DAG."""

    dag_id: str
    atomic_id: str
    depends_on: list[str] = Field(default_factory=list)
    inputs: dict[str, Any] = Field(default_factory=dict)
    bindings: dict[str, BindingRef] = Field(default_factory=dict)
    notes: str | None = None


class WorkflowPlan(BaseModel):
    """DAG over atomic workflows plus global literals (e.g. user prompt)."""

    summary: str = ""
    nodes: list[PlanNode] = Field(default_factory=list)
    global_inputs: dict[str, Any] = Field(default_factory=dict)


class AnalystResult(BaseModel):
    subgoals: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


class VerifierResult(BaseModel):
    ok: bool
    issues: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)


class StageState(str, Enum):
    """Workspace stage for Algorithm 1–style collaborative generation."""

    ANALYSIS = "ANALYSIS"
    ARCHITECTURE = "ARCHITECTURE"
    ENGINEERING = "ENGINEERING"
    CONVERGED = "CONVERGED"
    FAILED = "FAILED"


class ValidationReport(BaseModel):
    """Result of ValidateStage(workspace, currentStage)."""

    stage: Literal["ANALYSIS", "ARCHITECTURE", "ENGINEERING"]
    iteration: int
    is_converged: bool
    issues: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    structural_issues: list[str] = Field(default_factory=list)


class AnalysisVerifierResult(BaseModel):
    """LLM JSON for analysis-stage verification."""

    is_converged: bool
    issues: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)


class ExecutionRecord(BaseModel):
    dag_id: str
    atomic_id: str
    prompt_id: str | None = None
    status: Literal["skipped", "queued", "completed", "failed"] = "queued"
    outputs: dict[str, list[Any]] = Field(default_factory=dict)
    error: str | None = None


class PipelineStep(BaseModel):
    """Trace entry for the multi-stage collaborative loop (generate / validate / advance / backtrack)."""

    iteration: int = 0
    stage: str = ""
    action: Literal["generate", "validate", "advance", "backtrack", "abort"] = "generate"
    ok: bool | None = None
    detail: str = ""


class LegacyPlanningOutcome(BaseModel):
    """Result of collaborative workflow generation (ATT → AWG → EWC) with validation logs."""

    analyst: AnalystResult
    plan: WorkflowPlan
    verifier: VerifierResult | None = None
    verify_rounds: list[VerifierResult] = Field(default_factory=list)
    structural_issues: list[str] = Field(default_factory=list)
    trace: list[PipelineStep] = Field(default_factory=list)
    stopped_after_rounds: int = 0
    stage_state: str = StageState.CONVERGED.value
    converged: bool = True
    iteration_count: int = 0
    max_iterations: int = 5
    validation_log: list[ValidationReport] = Field(default_factory=list)
    ewc: dict[str, dict[str, Any]] | None = None
    failure_reason: str | None = None


class AbstractTask(BaseModel):
    model_config = {'extra': 'forbid'}
    task_id: str
    goal: str
    capability_id: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    input_types: dict[str, str] = Field(default_factory=dict)
    outputs: dict[str, str] = Field(default_factory=dict)
    constraints: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)


class AbstractTaskTree(BaseModel):
    model_config = {'extra': 'forbid'}
    tasks: list[AbstractTask] = Field(default_factory=list)
    edges: list[tuple[str, str]] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


class AbstractWorkflowNode(BaseModel):
    model_config = {'extra': 'forbid'}
    node_id: str
    task_id: str
    capability_id: str
    input_types: dict[str, str] = Field(default_factory=dict)
    output_types: dict[str, str] = Field(default_factory=dict)
    parameters: dict[str, Any] = Field(default_factory=dict)
    constraints: dict[str, Any] = Field(default_factory=dict)


class AbstractEdge(BaseModel):
    from_node: str
    output: str
    to_node: str
    input: str


class AbstractWorkflowGraph(BaseModel):
    model_config = {'extra': 'forbid'}
    nodes: list[AbstractWorkflowNode] = Field(default_factory=list)
    edges: list[AbstractEdge] = Field(default_factory=list)
    ordering: list[tuple[str, str]] = Field(default_factory=list)
    constraints: dict[str, Any] = Field(default_factory=dict)
    unresolved: list[str] = Field(default_factory=list)


# Public generation result uses abstract assets; WorkflowPlan is engineering-only.
class PlanningOutcome(BaseModel):
    analyst: AbstractTaskTree
    plan: AbstractWorkflowGraph
    execution_plan: WorkflowPlan = Field(default_factory=WorkflowPlan)
    ewc: dict[str, dict[str, Any]] | None = None
    converged: bool = False
    generation_mode: str = 'tools_first'
    validation_log: list[ValidationReport] = Field(default_factory=list)
    failure_reason: str | None = None
    stage_state: str = 'FAILED'
