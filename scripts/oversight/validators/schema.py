"""
schema.py — shared output contract for all oversight validators.

Every validator returns a dict matching make_result(). The risk assessor
agent reads these without knowing which validator produced them.
"""

from __future__ import annotations
from typing import Any


def make_finding(file: str, line: int, message: str, severity: str = "medium") -> dict:
    return {"file": file, "line": line, "message": message, "severity": severity}


def make_result(
    dimension: str,
    score: float,
    raw_value: Any,
    weight: float = 1.0,
    evidence: list | None = None,
    checklist_items: list[str] | None = None,
    findings: list | None = None,
    error: str | None = None,
) -> dict:
    """
    Standard output envelope for every validator.

    score     : 0.0 (no risk) → 1.0 (maximum risk), used by the aggregate
    raw_value : the dimension-specific measurement (e.g. {"max_rn": 9.0})
    weight    : the validator's contribution weight to the composite score
    evidence  : list of make_finding() dicts — specific locations
    checklist_items : inspection questions for reviewers (CID-style)
    findings  : structured findings with full context
    error     : non-None if the validator failed to run
    """
    return {
        "dimension": dimension,
        "score": round(max(0.0, min(1.0, score)), 4),
        "raw_value": raw_value,
        "weight": weight,
        "evidence": evidence or [],
        "checklist_items": checklist_items or [],
        "findings": findings or [],
        "error": error,
    }


def normalize(value: float, low: float, high: float) -> float:
    """Linear normalize value to [0, 1] between low (→0) and high (→1)."""
    if high <= low:
        return 0.0
    return max(0.0, min(1.0, (value - low) / (high - low)))


# Weights for composite score — tunable as research data accumulates
WEIGHTS = {
    "risk_number":          0.18,
    "cyclomatic":           0.08,
    "cognitive":            0.08,
    "function_metrics":     0.07,
    "n1_queries":           0.08,
    "migration_risk":       0.12,
    "static_analysis":      0.15,
    "historical_density":   0.12,
    "hallucination_surface": 0.06,
    "ip_check":             0.08,
    "prompt_ambiguity":     0.07,
}

# Score thresholds that map to risk tier.
# Format: (lo_inclusive, hi_exclusive) — score < hi maps to that tier.
# score_to_tier() uses exclusive upper bounds; do NOT use lo/hi directly
# for range checks as the tuples look inclusive but hi is exclusive.
# Boundary value 0.30 → MEDIUM (not LOW); 0.55 → HIGH; 0.78 → CRITICAL.
TIER_THRESHOLDS = {
    "LOW":      (0.00, 0.30),   # score < 0.30
    "MEDIUM":   (0.30, 0.55),   # 0.30 ≤ score < 0.55
    "HIGH":     (0.55, 0.78),   # 0.55 ≤ score < 0.78
    "CRITICAL": (0.78, 1.00),   # score ≥ 0.78
}


def composite_score(results: list[dict]) -> float:
    """Weighted average of validator scores, ignoring errored validators."""
    total_weight = 0.0
    weighted_sum = 0.0
    for r in results:
        if r.get("error"):
            continue
        w = r.get("weight", 1.0)
        weighted_sum += r["score"] * w
        total_weight += w
    return round(weighted_sum / total_weight, 4) if total_weight > 0 else 0.0


def score_to_tier(score: float) -> str:
    # Exclusive upper bounds — consistent with run_validators.sh inline Python.
    # Boundary value 0.30 → MEDIUM (not LOW), matching the threshold semantics.
    if score < 0.30: return "LOW"
    if score < 0.55: return "MEDIUM"
    if score < 0.78: return "HIGH"
    return "CRITICAL"
