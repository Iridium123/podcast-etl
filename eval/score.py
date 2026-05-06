"""Segment matching and scoring for ad detection evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from podcast_etl.detectors import AdSegment


# ---------------------------------------------------------------------------
# Segment matching
# ---------------------------------------------------------------------------

MatcherFunc = Callable[[AdSegment, AdSegment, float], bool]


def overlap_fraction_matcher(predicted: AdSegment, gold: AdSegment, threshold: float = 0.5) -> bool:
    """Return True if the overlap between predicted and gold exceeds threshold
    fraction of the gold segment's duration."""
    gold_duration = gold.end - gold.start
    if gold_duration <= 0:
        return False
    overlap_start = max(predicted.start, gold.start)
    overlap_end = min(predicted.end, gold.end)
    overlap = max(0.0, overlap_end - overlap_start)
    return (overlap / gold_duration) >= threshold


def _compute_overlap(a: AdSegment, b: AdSegment) -> float:
    """Compute overlap duration between two segments."""
    start = max(a.start, b.start)
    end = min(a.end, b.end)
    return max(0.0, end - start)


@dataclass
class MatchedPair:
    predicted: AdSegment
    gold: AdSegment


@dataclass
class MatchResult:
    matched: list[MatchedPair]
    false_positives: list[AdSegment]   # predicted with no gold match
    false_negatives: list[AdSegment]   # gold with no prediction match


def match_segments(
    predicted: list[AdSegment],
    gold: list[AdSegment],
    matcher: MatcherFunc = overlap_fraction_matcher,
    threshold: float = 0.5,
) -> MatchResult:
    """Match predicted segments to gold segments.

    Each gold segment matches at most one prediction (best overlap),
    and each prediction matches at most one gold segment.
    """
    if not predicted and not gold:
        return MatchResult(matched=[], false_positives=[], false_negatives=[])

    # Build a list of all valid (pred_idx, gold_idx, overlap) triples
    candidates: list[tuple[int, int, float]] = []
    for pi, p in enumerate(predicted):
        for gi, g in enumerate(gold):
            if matcher(p, g, threshold):
                candidates.append((pi, gi, _compute_overlap(p, g)))

    # Greedy assignment: best overlap first
    candidates.sort(key=lambda c: c[2], reverse=True)
    matched_pred: set[int] = set()
    matched_gold: set[int] = set()
    matched: list[MatchedPair] = []

    for pi, gi, _overlap in candidates:
        if pi not in matched_pred and gi not in matched_gold:
            matched.append(MatchedPair(predicted=predicted[pi], gold=gold[gi]))
            matched_pred.add(pi)
            matched_gold.add(gi)

    false_positives = [p for i, p in enumerate(predicted) if i not in matched_pred]
    false_negatives = [g for i, g in enumerate(gold) if i not in matched_gold]

    return MatchResult(matched=matched, false_positives=false_positives, false_negatives=false_negatives)


# ---------------------------------------------------------------------------
# Per-episode scoring
# ---------------------------------------------------------------------------

@dataclass
class EpisodeScore:
    true_positives: int
    false_positives: int
    false_negatives: int
    start_errors: list[float]      # predicted.start - gold.start for each TP
    end_errors: list[float]        # predicted.end - gold.end for each TP
    content_lost_seconds: float    # total duration of false positives
    ads_missed_seconds: float      # total duration of false negatives

    @property
    def precision(self) -> float:
        total = self.true_positives + self.false_positives
        return self.true_positives / total if total > 0 else 1.0

    @property
    def recall(self) -> float:
        total = self.true_positives + self.false_negatives
        return self.true_positives / total if total > 0 else 1.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) > 0 else 0.0


def score_episode(
    predicted: list[AdSegment],
    gold: list[AdSegment],
    matcher: MatcherFunc = overlap_fraction_matcher,
    threshold: float = 0.5,
) -> EpisodeScore:
    """Score predicted segments against gold standard for a single episode."""
    result = match_segments(predicted, gold, matcher, threshold)

    start_errors = [m.predicted.start - m.gold.start for m in result.matched]
    end_errors = [m.predicted.end - m.gold.end for m in result.matched]
    content_lost = sum(p.end - p.start for p in result.false_positives)
    ads_missed = sum(g.end - g.start for g in result.false_negatives)

    return EpisodeScore(
        true_positives=len(result.matched),
        false_positives=len(result.false_positives),
        false_negatives=len(result.false_negatives),
        start_errors=start_errors,
        end_errors=end_errors,
        content_lost_seconds=content_lost,
        ads_missed_seconds=ads_missed,
    )


# ---------------------------------------------------------------------------
# Aggregate scoring
# ---------------------------------------------------------------------------

@dataclass
class AggregateScore:
    total_tp: int
    total_fp: int
    total_fn: int
    precision: float
    recall: float
    f1: float
    start_error_mean: float
    start_error_median: float
    start_error_p95: float
    end_error_mean: float
    end_error_median: float
    end_error_p95: float
    total_content_lost: float
    total_ads_missed: float


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    if n % 2 == 1:
        return s[n // 2]
    return (s[n // 2 - 1] + s[n // 2]) / 2


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * p / 100
    f = int(k)
    c = f + 1 if f + 1 < len(s) else f
    return s[f] + (k - f) * (s[c] - s[f])


def aggregate_scores(scores: list[EpisodeScore]) -> AggregateScore:
    """Aggregate per-episode scores into summary metrics."""
    total_tp = sum(s.true_positives for s in scores)
    total_fp = sum(s.false_positives for s in scores)
    total_fn = sum(s.false_negatives for s in scores)

    precision = total_tp / (total_tp + total_fp) if (total_tp + total_fp) > 0 else 1.0
    recall = total_tp / (total_tp + total_fn) if (total_tp + total_fn) > 0 else 1.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    all_start = [e for s in scores for e in s.start_errors]
    all_end = [e for s in scores for e in s.end_errors]

    # Use absolute values for mean/median/p95 so we measure magnitude
    abs_start = [abs(e) for e in all_start]
    abs_end = [abs(e) for e in all_end]

    return AggregateScore(
        total_tp=total_tp,
        total_fp=total_fp,
        total_fn=total_fn,
        precision=precision,
        recall=recall,
        f1=f1,
        start_error_mean=sum(abs_start) / len(abs_start) if abs_start else 0.0,
        start_error_median=_median(abs_start),
        start_error_p95=_percentile(abs_start, 95),
        end_error_mean=sum(abs_end) / len(abs_end) if abs_end else 0.0,
        end_error_median=_median(abs_end),
        end_error_p95=_percentile(abs_end, 95),
        total_content_lost=sum(s.content_lost_seconds for s in scores),
        total_ads_missed=sum(s.ads_missed_seconds for s in scores),
    )


# ---------------------------------------------------------------------------
# Report formatting
# ---------------------------------------------------------------------------

def format_report(results: dict[str, AggregateScore]) -> str:
    """Format a comparison table of aggregate scores across configs."""
    header = f"{'Config':<30} {'Prec':>6} {'Rec':>6} {'F1':>6} {'Start(med)':>11} {'End(med)':>11} {'Content-lost':>13} {'Ads-missed':>11}"
    lines = [header, "-" * len(header)]

    for name, agg in results.items():
        start_med = f"{agg.start_error_median:.1f}s"
        end_med = f"{agg.end_error_median:.1f}s"
        lines.append(
            f"{name:<30} {agg.precision:>6.2f} {agg.recall:>6.2f} {agg.f1:>6.2f} "
            f"{start_med:>11} {end_med:>11} {agg.total_content_lost:>12.1f}s {agg.total_ads_missed:>10.1f}s"
        )

    return "\n".join(lines)
