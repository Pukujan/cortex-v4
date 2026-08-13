"""V4-owned runtime control primitives with no retired SSC runtime dependency."""

from .control_plane import ControlPlaneError, ExecutionPolicy, classify_terminal_closeout, validate_routes
from .extended_task import (
    ExtendedTaskController,
    ExtendedTaskProvider,
    ExtendedTaskResult,
    StallThenTimeoutInjector,
    run_extended_task,
    seed_workspace,
    validate_public_workspace,
)
from .fallback_matrix import FallbackAttempt, FallbackResult, VendorFallbackController
from .long_running import AttemptResult, LongRunningController, ScriptedProvider
from .temporal import create_run, start, status, supervise
from .workorder_recovery import (
    AttemptReceipt,
    Deadlines,
    TerminalReceipt,
    WorkOrder,
    WorkOrderContractError,
    WorkOrderRecoveryHarness,
)
from .run_brain import (
    BrainAuthorizationError,
    BrainError,
    BrainGenerationError,
    BrainHandle,
    BrainLeaseError,
    RunBrain,
    create_run as create_brain_run,
)
from .staged_runner import CampaignResult, StageContext, StageOutcome, StageRunnerError, StagedRunner
from .task_contract import StageSpec, TaskContract, TaskContractError
from .litellm_worker import LiteLLMStageWorker, ToolExecution, ToolExecutor
from .native_methodology import DispatchDecision, MethodologyPlan, MethodologyPreflightError, NativeV4Methodology

__all__ = [
    "AttemptReceipt", "AttemptResult", "ControlPlaneError", "Deadlines", "ExecutionPolicy",
    "ExtendedTaskController", "ExtendedTaskProvider", "ExtendedTaskResult", "FallbackAttempt",
    "FallbackResult", "LongRunningController", "ScriptedProvider", "StallThenTimeoutInjector",
    "TerminalReceipt", "VendorFallbackController", "WorkOrder", "WorkOrderContractError",
    "WorkOrderRecoveryHarness", "classify_terminal_closeout", "create_run", "run_extended_task",
    "seed_workspace", "start", "status", "supervise", "validate_public_workspace", "validate_routes",
    "BrainAuthorizationError", "BrainError", "BrainGenerationError", "BrainHandle", "BrainLeaseError",
    "RunBrain", "create_brain_run",
    "CampaignResult", "StageContext", "StageOutcome", "StageRunnerError", "StagedRunner",
    "StageSpec", "TaskContract", "TaskContractError",
    "LiteLLMStageWorker", "ToolExecution", "ToolExecutor",
    "DispatchDecision", "MethodologyPlan", "MethodologyPreflightError", "NativeV4Methodology",
]
