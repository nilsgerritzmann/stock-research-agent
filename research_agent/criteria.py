"""Checks the calculated metrics against the criteria in config/criteria.yaml."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from .metrics import Metric, MetricSet

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "criteria.yaml"

PASS, FAIL, NOT_ASSESSED = "Pass", "Fail", "Not assessed"


@dataclass
class CriterionResult:
    id: str
    name: str
    threshold_text: str
    metric: Metric
    status: str
    why: str


@dataclass
class DeepDiveFlag:
    text: str
    metrics: list[Metric]


def load_config(path: Path = CONFIG_PATH) -> dict:
    return yaml.safe_load(path.read_text())


def _passes(value: float, operator: str, threshold: float) -> bool:
    if operator == ">=":
        return value >= threshold
    if operator == "<=":
        return value <= threshold
    raise ValueError(f"Unknown operator {operator!r} in criteria config.")


def evaluate(metrics: MetricSet, config: dict) -> list[CriterionResult]:
    results = []
    for c in config["criteria"]:
        metric = metrics.summary.get(c["metric"])
        if metric is None:
            raise KeyError(f"Criterion {c['id']} refers to unknown metric {c['metric']!r}.")
        unit = c.get("unit", "")
        sep = "" if unit in ("%", "x", "") else " "
        threshold_text = f"{c['operator']} {c['threshold']}{sep}{unit}"
        if not metric.available:
            status = NOT_ASSESSED
        else:
            # Note: net cash gives a negative net debt / EBITDA, which correctly passes a "<=" cap.
            status = PASS if _passes(metric.value, c["operator"], c["threshold"]) else FAIL
        results.append(CriterionResult(c["id"], c["name"], threshold_text, metric, status, c.get("why", "")))
    return results


def deep_dive_flags(metrics: MetricSet, results: list[CriterionResult], config: dict) -> list[DeepDiveFlag]:
    """Changes that should be investigated before judging. In v1 these become open questions."""
    triggers = config.get("deep_dive_triggers", {})
    flags: list[DeepDiveFlag] = []

    margin_limit = triggers.get("operating_margin_change_pp")
    if margin_limit is not None:
        for year, change in sorted(metrics.per_year["operating_margin_change"].items()):
            if change.available and abs(change.value) >= margin_limit:
                flags.append(DeepDiveFlag(
                    f"Operating margin moved by at least {margin_limit} percentage points in FY{year}.",
                    [metrics.per_year["operating_margin"][year - 1], metrics.per_year["operating_margin"][year],
                     change]))

    swing_limit = triggers.get("revenue_growth_swing_pp")
    if swing_limit is not None:
        for year, change in sorted(metrics.per_year["revenue_growth_change"].items()):
            if change.available and abs(change.value) >= swing_limit:
                flags.append(DeepDiveFlag(
                    f"Revenue growth rate swung by at least {swing_limit} percentage points in FY{year}.",
                    [metrics.per_year["revenue_growth"][year - 1], metrics.per_year["revenue_growth"][year],
                     change]))

    if triggers.get("any_failed_criterion"):
        for r in results:
            if r.status == FAIL:
                flags.append(DeepDiveFlag(f"Criterion failed: {r.name} (threshold {r.threshold_text}).",
                                          [r.metric]))
    return flags
