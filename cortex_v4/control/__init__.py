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

__all__ = [
    "AttemptReceipt", "AttemptResult", "ControlPlaneError", "Deadlines", "ExecutionPolicy",
    "ExtendedTaskController", "ExtendedTaskProvider", "ExtendedTaskResult", "FallbackAttempt",
    "FallbackResult", "LongRunningController", "ScriptedProvider", "StallThenTimeoutInjector",
    "TerminalReceipt", "VendorFallbackController", "WorkOrder", "WorkOrderContractError",
    "WorkOrderRecoveryHarness", "classify_terminal_closeout", "create_run", "run_extended_task",
    "seed_workspace", "start", "status", "supervise", "validate_public_workspace", "validate_routes",
]
