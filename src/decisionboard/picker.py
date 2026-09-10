"""The AI-assisted knowledge pick (spec section 5.1, decided 10 September
2026): one model call that chooses, per member, which of the candidate
sections Python ranked first go to that member in full and which as a one
line summary, with a reason per pick.

Python keeps the last word (AP-1): the model sees only ids from the
candidate list, every id it returns is checked against that list, an id
that is not there is dropped, and a call that fails or returns nothing
usable leaves the Python ranking in force. The reasons are shown to Alex
before the board runs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .agent.prompts import load_prompt
from .agent.provider import TASK_BOARD, AiProvider, AiResult

MAX_FULL = 8        # sections in full per member
MAX_BRIEF = 20      # one-line pages per member
MARKER = "## Candidate sections"     # what the statistics route on


@dataclass
class PickResult:
    picks: dict[str, dict[str, Any]] = field(default_factory=dict)   # member -> {"full": [...], "brief": [...], "reasons": {...}}
    ai_result: AiResult | None = None
    error: str | None = None            # why the Python ranking stays in force, if it does
    dropped: int = 0                    # ids the model named that were not candidates

    @property
    def ok(self) -> bool:
        return self.error is None and any(p.get("full") or p.get("brief") for p in self.picks.values())


def pick_prompt(question: str, members: dict[str, str], candidates: dict[str, list[dict[str, Any]]]) -> str:
    """``members`` maps a member's name to one line about what it judges;
    ``candidates`` is ``knowledge.candidates``."""
    lines = [load_prompt("knowledge_pick"), "", "## Question for the board", "", question.strip(), "", "## Members", ""]
    for member, line in members.items():
        lines.append(f"- {member}: {line}" if line else f"- {member}")
    lines += ["", MARKER, ""]
    for member, rows in candidates.items():
        lines.append(f"### {member}")
        if not rows:
            lines.append("(no candidates: the vault has nothing ranked for this member)")
        for row in rows:
            summary = f" | {row['summary']}" if row.get("summary") else ""
            heading = f" - {row['heading']}" if row.get("heading") else ""
            lines.append(f"- id: {row['id']} | {row['path']}{heading}{summary} | {row.get('tokens', 0)} tokens")
        lines.append("")
    lines.append("Choose now, as the JSON object described above.")
    return "\n".join(lines)


def parse_picks(text: str, candidates: dict[str, list[dict[str, Any]]]) -> PickResult:
    """The model's answer with Python's checks applied: only ids from the
    member's own candidate list survive, the caps hold, and a reason that
    names an id not chosen is dropped."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return PickResult(error="the pick did not parse as JSON")
    if not isinstance(data, dict) or not isinstance(data.get("members"), list):
        return PickResult(error="the pick carried no members list")
    wanted = {m.lower(): m for m in candidates}
    result = PickResult()
    for item in data["members"]:
        if not isinstance(item, dict):
            continue
        member = wanted.get(str(item.get("member", "")).strip().lower())
        if member is None or member in result.picks:
            continue
        allowed = {row["id"] for row in candidates.get(member, [])}
        allowed_pages = {row["path"] for row in candidates.get(member, [])}

        def clean(raw, cap: int) -> list[str]:
            ids: list[str] = []
            for value in (raw if isinstance(raw, list) else []):
                sid = str(value).strip()
                if sid in allowed or sid in allowed_pages:
                    if sid not in ids:
                        ids.append(sid)
                else:
                    result.dropped += 1
            return ids[:cap]

        full = clean(item.get("full"), MAX_FULL)
        brief = [sid for sid in clean(item.get("brief"), MAX_BRIEF + MAX_FULL) if sid not in full][:MAX_BRIEF]
        reasons_raw = item.get("reasons") if isinstance(item.get("reasons"), dict) else {}
        reasons = {str(k): str(v).strip() for k, v in reasons_raw.items() if str(k) in full or str(k) in brief}
        result.picks[member] = {"full": full, "brief": brief, "reasons": reasons}
    for member in candidates:
        result.picks.setdefault(member, {"full": [], "brief": [], "reasons": {}})
    if not result.ok:
        result.error = "the pick named no candidate section"
    return result


def pick(provider: AiProvider, question: str, members: dict[str, str],
         candidates: dict[str, list[dict[str, Any]]]) -> PickResult:
    """The one call. Never raises for a bad answer: the result says why the
    Python ranking stays in force. A provider failure does raise, as every
    other call's does, so the session can show it."""
    ai_result = provider.complete(TASK_BOARD, pick_prompt(question, members, candidates))
    result = parse_picks(ai_result.text, candidates)
    result.ai_result = ai_result
    return result
