"""Minimal verify step for the GoldenClip Agent loop MVP."""

from __future__ import annotations

from typing import Awaitable, Callable, Optional

from .contracts import ContractReport, format_contract_issues
from .hooks import emit_agent_hook
from .trace import append_trace


class ContractVerificationError(ValueError):
    """契约拒绝；reject_and_retry 可由统一执行器安全重试。"""

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        on_fail: str = "reject",
        report: Optional[ContractReport] = None,
    ):
        super().__init__(message)
        self.retryable = retryable
        self.on_fail = on_fail
        self.report = report


async def verify_contract_report(
    task_id: str,
    phase: str,
    report: ContractReport,
    *,
    capability: Optional[str] = None,
    log_callback: Optional[Callable[[str, str, str], Awaitable[None]]] = None,
) -> ContractReport:
    """Trace a contract report, log warnings, and reject unsafe outputs."""
    validator_capability = capability or _capability_for_contract(report.contract_id)
    emit_agent_hook(
        task_id,
        "after_contract_verify",
        validator_capability,
        payload={"phase": phase, "contract_id": report.contract_id, "passed": report.passed},
    )
    append_trace(
        task_id,
        "contract_verified",
        source=phase,
        payload=report.model_dump(),
    )

    if report.rejected:
        message = format_contract_issues(report.issues)
        rejected_actions = {
            issue.on_fail
            for issue in report.issues
            if issue.on_fail in {"reject", "reject_and_retry"}
        }
        # 只要包含明确 reject 就是永久错误；全部为 reject_and_retry 才可重试。
        on_fail = "reject" if "reject" in rejected_actions else "reject_and_retry"
        retryable = on_fail == "reject_and_retry"
        if log_callback:
            await log_callback("error", "validator", f"{phase} 验证失败: {message}")
        raise ContractVerificationError(
            message,
            retryable=retryable,
            on_fail=on_fail,
            report=report,
        )

    if report.flagged_for_human:
        emit_agent_hook(
            task_id,
            "on_human_review_required",
            validator_capability,
            payload={"phase": phase, "issues": [issue.model_dump() for issue in report.issues]},
        )
        if log_callback:
            await log_callback("warn", "validator", f"{phase} 需要人工复核: {format_contract_issues(report.issues)}")

    if log_callback:
        await log_callback("success", "validator", f"{phase} 代码契约验证通过")

    return report


def _capability_for_contract(contract_id: str) -> str:
    if "highlight_reels" in contract_id:
        return "validate.highlight_reels"
    return "validate.output"

