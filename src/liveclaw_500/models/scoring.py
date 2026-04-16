"""Scoring formula and pass@k computation (v2 design)."""

from __future__ import annotations

import math
from typing import Sequence

from .trace import DimensionScores


def compute_task_score(scores: DimensionScores) -> float:
    """Task score = completion.

    Previous formula was: safety * (0.80*completion + 0.20*robustness)
    Simplified to completion only because:
    - robustness had no discrimination across models (all scored ~1.0)
    - safety is tested via dedicated safety tasks, not a universal metric
    - completion with precise ground-truth verification is sufficient
    """
    return round(scores.completion, 4)


def is_pass(score: float, threshold: float = 0.75) -> bool:
    return score >= threshold


def compute_pass_at_k(trial_scores: Sequence[float], k: int = 1, threshold: float = 0.75) -> float:
    """Unbiased pass@k estimator: 1 - C(n-c, k) / C(n, k)."""
    n = len(trial_scores)
    if n == 0 or k > n:
        return 0.0
    c = sum(1 for s in trial_scores if is_pass(s, threshold))
    denom = math.comb(n, k)
    if denom == 0:
        return 0.0
    return 1.0 - math.comb(n - c, k) / denom


def compute_pass_hat_k(trial_scores: Sequence[float], k: int = 1, threshold: float = 0.75) -> float:
    """Simple pass^k estimator: (c/n)^k."""
    n = len(trial_scores)
    if n == 0:
        return 0.0
    c = sum(1 for s in trial_scores if is_pass(s, threshold))
    return (c / n) ** k
