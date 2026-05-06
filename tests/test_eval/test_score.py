"""Tests for segment matching and scoring."""

import pytest

from podcast_etl.detectors import AdSegment

from eval.score import (
    AggregateScore,
    EpisodeScore,
    MatchedPair,
    MatchResult,
    aggregate_scores,
    format_report,
    match_segments,
    overlap_fraction_matcher,
    score_episode,
)


def _gold(start, end, label="Ad"):
    return AdSegment(start=start, end=end, confidence=1.0, detector="gold", label=label)


def _pred(start, end, label="Ad"):
    return AdSegment(start=start, end=end, confidence=0.9, detector="transcription", label=label)


# ---------------------------------------------------------------------------
# overlap_fraction_matcher
# ---------------------------------------------------------------------------

class TestOverlapFractionMatcher:
    def test_full_overlap_matches(self):
        assert overlap_fraction_matcher(_pred(0, 30), _gold(0, 30), threshold=0.5) is True

    def test_sufficient_overlap_matches(self):
        # Pred covers 20 of 30 gold seconds = 66%
        assert overlap_fraction_matcher(_pred(10, 40), _gold(0, 30), threshold=0.5) is True

    def test_insufficient_overlap_no_match(self):
        # Pred covers 5 of 30 gold seconds = 16%
        assert overlap_fraction_matcher(_pred(25, 50), _gold(0, 30), threshold=0.5) is False

    def test_no_overlap(self):
        assert overlap_fraction_matcher(_pred(100, 130), _gold(0, 30), threshold=0.5) is False

    def test_zero_duration_gold(self):
        assert overlap_fraction_matcher(_pred(0, 10), _gold(5, 5), threshold=0.5) is False


# ---------------------------------------------------------------------------
# match_segments
# ---------------------------------------------------------------------------

class TestMatchSegments:
    def test_perfect_match(self):
        gold = [_gold(0, 30), _gold(100, 130)]
        pred = [_pred(0, 30), _pred(100, 130)]
        result = match_segments(pred, gold)

        assert len(result.matched) == 2
        assert len(result.false_positives) == 0
        assert len(result.false_negatives) == 0

    def test_false_positive(self):
        gold = [_gold(0, 30)]
        pred = [_pred(0, 30), _pred(200, 230)]
        result = match_segments(pred, gold)

        assert len(result.matched) == 1
        assert len(result.false_positives) == 1
        assert result.false_positives[0].start == 200

    def test_false_negative(self):
        gold = [_gold(0, 30), _gold(100, 130)]
        pred = [_pred(0, 30)]
        result = match_segments(pred, gold)

        assert len(result.matched) == 1
        assert len(result.false_negatives) == 1
        assert result.false_negatives[0].start == 100

    def test_empty_predictions(self):
        gold = [_gold(0, 30)]
        result = match_segments([], gold)
        assert len(result.false_negatives) == 1
        assert len(result.matched) == 0

    def test_empty_gold(self):
        pred = [_pred(0, 30)]
        result = match_segments(pred, [])
        assert len(result.false_positives) == 1
        assert len(result.matched) == 0

    def test_both_empty(self):
        result = match_segments([], [])
        assert len(result.matched) == 0
        assert len(result.false_positives) == 0
        assert len(result.false_negatives) == 0

    def test_best_overlap_wins(self):
        # Two predictions overlap same gold -- best overlap wins
        gold = [_gold(0, 30)]
        pred = [_pred(20, 50), _pred(0, 30)]
        result = match_segments(pred, gold)

        assert len(result.matched) == 1
        assert result.matched[0].predicted.start == 0  # exact match wins
        assert len(result.false_positives) == 1


# ---------------------------------------------------------------------------
# score_episode
# ---------------------------------------------------------------------------

class TestScoreEpisode:
    def test_perfect_detection(self):
        gold = [_gold(0, 30)]
        pred = [_pred(0, 30)]
        score = score_episode(pred, gold)

        assert score.true_positives == 1
        assert score.false_positives == 0
        assert score.false_negatives == 0
        assert score.precision == 1.0
        assert score.recall == 1.0

    def test_boundary_errors_computed(self):
        gold = [_gold(10, 50)]
        pred = [_pred(12, 48)]  # starts 2s late, ends 2s early
        score = score_episode(pred, gold)

        assert score.true_positives == 1
        assert len(score.start_errors) == 1
        assert score.start_errors[0] == pytest.approx(2.0)   # pred - gold
        assert score.end_errors[0] == pytest.approx(-2.0)     # pred - gold

    def test_content_lost_from_false_positives(self):
        gold = []
        pred = [_pred(100, 115)]  # 15s of content falsely flagged
        score = score_episode(pred, gold)

        assert score.false_positives == 1
        assert score.content_lost_seconds == pytest.approx(15.0)

    def test_ads_missed_from_false_negatives(self):
        gold = [_gold(0, 30)]
        pred = []
        score = score_episode(pred, gold)

        assert score.false_negatives == 1
        assert score.ads_missed_seconds == pytest.approx(30.0)

    def test_precision_recall_with_mixed_results(self):
        gold = [_gold(0, 30), _gold(100, 130)]
        pred = [_pred(0, 30), _pred(200, 230)]  # 1 TP, 1 FP, 1 FN
        score = score_episode(pred, gold)

        assert score.true_positives == 1
        assert score.false_positives == 1
        assert score.false_negatives == 1
        assert score.precision == pytest.approx(0.5)
        assert score.recall == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# aggregate_scores
# ---------------------------------------------------------------------------

class TestAggregateScores:
    def test_aggregates_multiple_episodes(self):
        scores = [
            EpisodeScore(
                true_positives=2, false_positives=1, false_negatives=0,
                start_errors=[1.0, -0.5], end_errors=[-1.0, 0.5],
                content_lost_seconds=10.0, ads_missed_seconds=0.0,
            ),
            EpisodeScore(
                true_positives=1, false_positives=0, false_negatives=1,
                start_errors=[2.0], end_errors=[-1.5],
                content_lost_seconds=0.0, ads_missed_seconds=30.0,
            ),
        ]
        agg = aggregate_scores(scores)

        assert agg.total_tp == 3
        assert agg.total_fp == 1
        assert agg.total_fn == 1
        assert agg.precision == pytest.approx(3 / 4)
        assert agg.recall == pytest.approx(3 / 4)
        assert agg.total_content_lost == pytest.approx(10.0)
        assert agg.total_ads_missed == pytest.approx(30.0)

    def test_empty_scores(self):
        agg = aggregate_scores([])
        assert agg.total_tp == 0
        assert agg.precision == 1.0  # no predictions = vacuously precise
        assert agg.start_error_mean == 0.0

    def test_boundary_errors_use_absolute_values(self):
        scores = [
            EpisodeScore(
                true_positives=2, false_positives=0, false_negatives=0,
                start_errors=[-3.0, 3.0], end_errors=[1.0, -1.0],
                content_lost_seconds=0.0, ads_missed_seconds=0.0,
            ),
        ]
        agg = aggregate_scores(scores)
        assert agg.start_error_mean == pytest.approx(3.0)
        assert agg.start_error_median == pytest.approx(3.0)
        assert agg.end_error_mean == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# format_report
# ---------------------------------------------------------------------------

class TestFormatReport:
    def test_formats_table(self):
        results = {
            "config-a": AggregateScore(
                total_tp=10, total_fp=2, total_fn=1,
                precision=0.83, recall=0.91, f1=0.87,
                start_error_mean=1.5, start_error_median=1.2, start_error_p95=3.0,
                end_error_mean=0.8, end_error_median=0.6, end_error_p95=2.0,
                total_content_lost=12.5, total_ads_missed=30.0,
            ),
        }
        report = format_report(results)
        assert "config-a" in report
        assert "0.83" in report
        assert "0.91" in report

    def test_handles_empty_results(self):
        report = format_report({})
        assert "Config" in report  # header still present

    def test_does_not_show_plus_sign_for_magnitudes(self):
        results = {
            "config-a": AggregateScore(
                total_tp=1, total_fp=0, total_fn=0,
                precision=1.0, recall=1.0, f1=1.0,
                start_error_mean=0.0, start_error_median=2.5, start_error_p95=0.0,
                end_error_mean=0.0, end_error_median=1.7, end_error_p95=0.0,
                total_content_lost=0.0, total_ads_missed=0.0,
            ),
        }
        report = format_report(results)
        # Median values are absolute magnitudes (always >= 0), should not have a + sign
        assert "+2.5s" not in report
        assert "+1.7s" not in report
        assert "2.5s" in report
        assert "1.7s" in report
