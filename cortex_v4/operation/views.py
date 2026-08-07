from __future__ import annotations

from html import escape
from typing import Any, Mapping


def render_closeout(receipt: Mapping[str, Any], *, context_ref: str) -> str:
    """Owner-legible closeout view; it reports evidence, not a correctness verdict."""
    return (
        "# V4 walking-skeleton closeout\n\n"
        f"- status: `{escape(str(receipt.get('status', 'unknown')))}`\n"
        f"- run: `{escape(str(receipt.get('run_id', '')))}`\n"
        f"- task: {escape(str(receipt.get('task', '')))}`\n"
        f"- methodology pack: `{escape(str(receipt.get('methodology_pack_hash', '')))}`\n"
        f"- corpus context: `{escape(str(receipt.get('context_hash', '')))}` ({escape(context_ref)})\n"
        f"- summon: `{escape(str(receipt.get('seat', '')))} / "
        f"{escape(str(receipt.get('model_override', '')) or 'default')}`\n"
        f"- observation: `{escape(str(receipt.get('observation_overall', '')))}`\n\n"
        "The SSC corpus remains the source of knowledge and closeout authority."
    )

