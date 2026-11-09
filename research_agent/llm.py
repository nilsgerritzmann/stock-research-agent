"""The only module that talks to Claude.

Uses the Claude Agent SDK, which signs in with your Claude login (the same one
Claude Code uses), so calls count against your subscription instead of a paid
API key. To switch to an API key later, only this file changes.

In v1 the model gets no tools at all: it receives the calculated figures as
placeholders and returns prose as JSON. It cannot read files, browse or run
code. Its output is checked for typed numbers and unknown placeholders; if the
check fails it gets one retry with the problems listed.
"""

from __future__ import annotations

import asyncio
import json
import os
import re

from .criteria import CriterionResult, DeepDiveFlag
from .facts import CompanyData
from .memo import Narrative, find_typed_numbers, fmt, unknown_placeholders
from .metrics import Metric

MODEL = "opus"          # alias resolved by Claude Code to the current Opus model
MAX_ATTEMPTS = 2


class LlmError(Exception):
    """The narrative could not be produced. The memo is still written without it."""


SYSTEM_PROMPT = """\
You are an equity research assistant writing the prose sections of a short research memo \
for a private investor. All figures have already been calculated by a program.

Rules:
1. Never write a number yourself. To mention a figure, write its placeholder exactly as \
given, e.g. {{operating_margin_FY2025}}. Years like 2025 or FY2025 and form names like 10-K are allowed.
2. Only use placeholders from the catalog provided.
3. You only see the figures, not the annual report text. Do not state causes, segment \
details or management explanations; you cannot verify them. Turn them into open questions instead.
4. No buy, sell or hold recommendation, no price targets, no predictions.
5. If data is missing or a criterion was not assessed, say so plainly.
6. Plain, precise English. No hype.

Return only a JSON object, no other text:
{"summary": "3-5 sentences", "findings": ["3-5 bullets"], "risks": ["2-4 bullets"], \
"open_questions": ["3-6 questions an analyst should answer from the 10-K or earnings calls"]}
"""


_LIST = {"type": "array", "items": {"type": "string"}}
NARRATIVE_SCHEMA = {
    "type": "object",
    "properties": {"summary": {"type": "string"}, "findings": _LIST, "risks": _LIST, "open_questions": _LIST},
    "required": ["summary", "findings", "risks", "open_questions"],
    "additionalProperties": False,
}


def _context(data: CompanyData, results: list[CriterionResult], flags: list[DeepDiveFlag],
             catalog: dict[str, Metric]) -> str:
    def line(m: Metric) -> str:
        note = f"  [note: {m.note}]" if m.note else ""
        return f"{{{{{m.id}}}}} = {fmt(m)}  ({m.label}){note}"

    shown = [m for m in catalog.values() if m.available or m.note]
    criteria_lines = [f"- {r.name} (threshold {r.threshold_text}): {r.status}, value {{{{{r.metric.id}}}}}"
                      for r in results]
    flag_lines = [f"- {f.text} Figures: " + ", ".join(f"{{{{{m.id}}}}}" for m in f.metrics) for f in flags]
    return "\n".join([
        f"Company: {data.name} ({data.ticker}), industry: {data.sic_description}",
        f"Fiscal years covered: FY{data.fiscal_years[0]}-FY{data.fiscal_years[-1]}",
        "",
        "Criteria results:", *criteria_lines,
        "",
        "Flags that call for a deeper look (not yet investigated):", *(flag_lines or ["- none"]),
        "",
        "Data gaps:", *(f"- {g}" for g in data.gaps), *([] if data.gaps else ["- none"]),
        "",
        "Figure catalog (placeholder = value). Use placeholders, never the values:",
        *sorted(line(m) for m in shown),
    ])


def _parse(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("no JSON object in the response")
    obj = json.loads(match.group(0))
    for key in ("summary", "findings", "risks", "open_questions"):
        if key not in obj:
            raise ValueError(f"missing key '{key}'")
    return obj


def _problems(obj: dict, catalog: dict[str, Metric]) -> list[str]:
    texts = [obj["summary"], *obj["findings"], *obj["risks"], *obj["open_questions"]]
    problems = []
    for t in texts:
        typed = find_typed_numbers(t)
        if typed:
            problems.append(f"Typed numbers {typed} in: \"{t[:80]}...\" - use placeholders instead.")
        unknown = unknown_placeholders(t, catalog)
        if unknown:
            problems.append(f"Unknown placeholders {unknown} - only use ids from the catalog.")
    return problems


def _check_subscription_only() -> None:
    """Refuse to run if an API key is set: Claude Code would use (and bill) it instead of the subscription."""
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        if os.environ.get(var):
            raise LlmError(f"{var} is set in your environment. It would be billed instead of using your "
                           f"Claude subscription. Unset it (e.g. `unset {var}`) and run again.")


async def _ask(prompt: str) -> str:
    _check_subscription_only()
    try:
        from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ResultMessage, TextBlock, query
    except ImportError as exc:
        raise LlmError("claude-agent-sdk is not installed") from exc

    options = ClaudeAgentOptions(
        system_prompt=SYSTEM_PROMPT,
        model=MODEL,
        tools=[],                # no built-in tools in v1: the model only writes text
        max_turns=3,             # structured output may take an extra internal turn
        setting_sources=[],      # isolation: ignore local CLAUDE.md / settings files
        output_format={"type": "json_schema", "schema": NARRATIVE_SCHEMA},
    )
    chunks: list[str] = []
    structured = None
    error = None
    # Consume the whole stream (no early return), so the SDK can shut down cleanly.
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, AssistantMessage):
            chunks.extend(block.text for block in message.content if isinstance(block, TextBlock))
        elif isinstance(message, ResultMessage):
            if message.is_error:
                error = message.result or message.subtype
            elif message.structured_output:
                structured = message.structured_output
    if error:
        raise LlmError(f"model call failed: {error}")
    return json.dumps(structured) if structured else "".join(chunks)


def write_narrative(data: CompanyData, results: list[CriterionResult], flags: list[DeepDiveFlag],
                    catalog: dict[str, Metric]) -> Narrative:
    prompt = _context(data, results, flags, catalog)
    feedback = ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            raw = asyncio.run(_ask(prompt + feedback))
        except LlmError:
            raise
        except Exception as exc:  # login missing, usage limit reached, network, ...
            raise LlmError(f"could not reach Claude ({type(exc).__name__}: {exc})") from exc
        try:
            obj = _parse(raw)
        except (ValueError, json.JSONDecodeError) as exc:
            feedback = f"\n\nYour previous answer was not valid JSON ({exc}). Return only the JSON object."
            continue
        problems = _problems(obj, catalog)
        if not problems:
            return Narrative(obj["summary"], obj["findings"], obj["risks"], obj["open_questions"])
        feedback = ("\n\nYour previous answer broke the rules:\n" + "\n".join(f"- {p}" for p in problems)
                    + "\nRewrite it following all rules.")
    raise LlmError(f"model output failed validation after {MAX_ATTEMPTS} attempts")
