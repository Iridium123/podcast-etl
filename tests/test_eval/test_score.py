"""Tests for eval.score: segment matching, scoring, and report formatting."""

from __future__ import annotations

import pytest

from podcast_etl.detectors import AdSegment

from eval.score import (
    AggregateScore,
    EpisodeScore,
    MatchedPair,
    MatchResult,
    _median,
    _percentile,
    aggregate_scores,
    format_report,
    match_segments,
    overlap_fraction_matcher,
    score_episode,
)


def _seg(start: float, end: float, confidence: float = 0.9) -> AdSegment:
    return AdSegment(start=start, end=end, confidence=confidence, detector="test")


# ---------------------------------------------------------------------------
# overlap_fraction_matcher
# ---------------------------------------------------------------------------

class TestOverlapFractionMatcher:
    def test_exact_overlap_above_threshold(self):
        p = _seg(0.0, 60.0)
        g = _seg(0.0, 60.0)
        assert overlap_fraction_matcher(p, g, threshold=0.5) is True

    def test_no_overlap_below_threshold(self):
        p = _seg(100.0, 200.0)
        g = _seg(0.0, 60.0)
        assert overlap_fraction_matcher(p, g, threshold=0.5) is False

    def test_partial_overlap_above_threshold(self):
        # predicted covers [40, 100], gold covers [0, 60].
        # overlap = [40, 60] = 20s, gold_duration = 60s -> 20/60 ≈ 0.33 < 0.5
        p = _seg(40.0, 100.0)
        g = _seg(0.0, 60.0)
        assert overlap_fraction_matcher(p, g, threshold=0.5) is False

    def test_partial_overlap_at_threshold(self):
        # overlap = [30, 60] = 30s, gold = 60s -> 0.5 == threshold
        p = _seg(30.0, 100.0)
        g = _seg(0.0, 60.0)
        assert overlap_fraction_matcher(p, g, threshold=0.5) is True

    def test_zero_duration_gold_returns_false(self):
        p = _seg(0.0, 60.0)
        g = _seg(10.0, 10.0)  # zero duration
        assert overlap_fraction_matcher(p, g, threshold=0.0) is False

    def test_threshold_zero_any_overlap_matches(self):
        # 1s overlap over 60s gold -> 1/60 >= 0
        p = _seg(59.0, 100.0)
        g = _seg(0.0, 60.0)
        assert overlap_fraction_matcher(p, g, threshold=0.0) is True

    def test_no_overlap_at_all(self):
        p = _seg(0.0, 10.0)
        g = _seg(20.0, 40.0)
        assert overlap_fraction_matcher(p, g, threshold=0.5) is False


# ---------------------------------------------------------------------------
# match_segments
# ---------------------------------------------------------------------------

class TestMatchSegments:
    def test_empty_inputs(self):
        result = match_segments([], [])
        assert result.matched == []
        assert result.false_positives == []
        assert result.false_negatives == []

    def test_empty_predicted_all_false_negatives(self):
        gold = [_seg(0.0, 30.0), _seg(60.0, 90.0)]
        result = match_segments([], gold)
        assert result.matched == []
        assert result.false_positives == []
        assert result.false_negatives == gold

    def test_empty_gold_all_false_positives(self):
        pred = [_seg(0.0, 30.0)]
        result = match_segments(pred, [])
        assert result.matched == []
        assert result.false_positives == pred
        assert result.false_negatives == []

    def test_perfect_match(self):
        pred = [_seg(0.0, 30.0)]
        gold = [_seg(0.0, 30.0)]
        result = match_segments(pred, gold)
        assert len(result.matched) == 1
        assert result.matched[0].predicted == pred[0]
        assert result.matched[0].gold == gold[0]
        assert result.false_positives == []
        assert result.false_negatives == []

    def test_greedy_best_overlap_wins(self):
        # Two predictions competing for one gold segment; the one with more
        # overlap wins the match, the other is a false positive.
        gold = [_seg(0.0, 60.0)]
        pred_partial = _seg(0.0, 40.0)   # 40/60 ≈ 0.67 overlap
        pred_full = _seg(0.0, 60.0)       # 60/60 = 1.0 overlap
        result = match_segments([pred_partial, pred_full], gold)
        assert len(result.matched) == 1
        assert result.matched[0].predicted == pred_full
        assert result.false_positives == [pred_partial]

    def test_one_prediction_cannot_match_two_golds(self):
        # One prediction that spans both gold segments; matches best one.
        pred = [_seg(0.0, 90.0)]
        gold = [_seg(0.0, 30.0), _seg(60.0, 90.0)]
        result = match_segments(pred, gold)
        # Should match exactly one gold and leave the other as false negative.
        assert len(result.matched) == 1
        assert len(result.false_negatives) == 1

    def test_multiple_perfect_matches(self):
        pred = [_seg(0.0, 30.0), _seg(60.0, 90.0)]
        gold = [_seg(0.0, 30.0), _seg(60.0, 90.0)]
        result = match_segments(pred, gold)
        assert len(result.matched) == 2
        assert result.false_positives == []
        assert result.false_negatives == []

    def test_unmatched_prediction_is_false_positive(self):
        pred = [_seg(0.0, 30.0), _seg(200.0, 210.0)]
        gold = [_seg(0.0, 30.0)]
        result = match_segments(pred, gold)
        assert len(result.matched) == 1
        assert len(result.false_positives) == 1
        assert result.false_positives[0] == _seg(200.0, 210.0)

    def test_unmatched_gold_is_false_negative(self):
        pred = [_seg(0.0, 30.0)]
        gold = [_seg(0.0, 30.0), _seg(200.0, 210.0)]
        result = match_segments(pred, gold)
        assert len(result.matched) == 1
        assert len(result.false_negatives) == 1
        assert result.false_negatives[0] == _seg(200.0, 210.0)


# ---------------------------------------------------------------------------
# score_episode
# ---------------------------------------------------------------------------

class TestScoreEpisode:
    def test_perfect_match_all_tp(self):
        pred = [_seg(0.0, 30.0)]
        gold = [_seg(0.0, 30.0)]
        score = score_episode(pred, gold)
        assert score.true_positives == 1
        assert score.false_positives == 0
        assert score.false_negatives == 0
        assert score.start_errors == [0.0]
        assert score.end_errors == [0.0]
        assert score.content_lost_seconds == 0.0
        assert score.ads_missed_seconds == 0.0

    def test_false_positive_counts_content_lost(self):
        pred = [_seg(100.0, 130.0)]
        gold = []
        score = score_episode(pred, gold)
        assert score.false_positives == 1
        assert score.content_lost_seconds == 30.0
        assert score.ads_missed_seconds == 0.0

    def test_false_negative_counts_ads_missed(self):
        pred = []
        gold = [_seg(0.0, 60.0)]
        score = score_episode(pred, gold)
        assert score.false_negatives == 1
        assert score.ads_missed_seconds == 60.0
        assert score.content_lost_seconds == 0.0

    def test_start_end_errors_on_match(self):
        pred = [_seg(5.0, 35.0)]
        gold = [_seg(0.0, 30.0)]
        score = score_episode(pred, gold)
        assert score.true_positives == 1
        assert score.start_errors == [5.0]  # predicted.start - gold.start
        assert score.end_errors == [5.0]    # predicted.end - gold.end

    def test_empty_both_empty_episode(self):
        score = score_episode([], [])
        assert score.true_positives == 0
        assert score.false_positives == 0
        assert score.false_negatives == 0

    def test_precision_recall_f1_properties(self):
        pred = [_seg(0.0, 30.0), _seg(100.0, 130.0)]
        gold = [_seg(0.0, 30.0), _seg(200.0, 230.0)]
        score = score_episode(pred, gold)
        # TP=1, FP=1 (100-130 not in gold), FN=1 (200-230 not found)
        assert score.true_positives == 1
        assert score.false_positives == 1
        assert score.false_negatives == 1
        assert score.precision == pytest.approx(0.5)
        assert score.recall == pytest.approx(0.5)
        assert score.f1 == pytest.approx(0.5)

    def test_precision_one_when_no_predictions(self):
        # No predictions: precision = 1.0 by convention (no false alarms)
        score = score_episode([], [_seg(0.0, 30.0)])
        assert score.precision == 1.0

    def test_recall_one_when_no_gold(self):
        # No gold: recall = 1.0 by convention (nothing to miss)
        score = score_episode([_seg(0.0, 30.0)], [])
        assert score.recall == 1.0

    def test_f1_zero_when_both_precision_and_recall_zero(self):
        # If somehow p=0 and r=0 we get 0 (edge case, hard to trigger naturally
        # because when TP=0 and FP>0 and FN>0 p=0 and r=0)
        # Manufacture directly via the formula check: both 0 -> f1=0
        score = EpisodeScore(
            true_positives=0, false_positives=1, false_negatives=1,
            start_errors=[], end_errors=[],
            content_lost_seconds=0.0, ads_missed_seconds=0.0,
        )
        assert score.precision == 0.0
        assert score.recall == 0.0
        assert score.f1 == 0.0

    def test_multiple_fp_content_lost_summed(self):
        pred = [_seg(100.0, 110.0), _seg(200.0, 220.0)]
        gold = []
        score = score_episode(pred, gold)
        assert score.content_lost_seconds == pytest.approx(30.0)

    def test_multiple_fn_ads_missed_summed(self):
        pred = []
        gold = [_seg(0.0, 60.0), _seg(100.0, 130.0)]
        score = score_episode(pred, gold)
        assert score.ads_missed_seconds == pytest.approx(90.0)


# ---------------------------------------------------------------------------
# aggregate_scores
# ---------------------------------------------------------------------------

class TestAggregateScores:
    def test_empty_list(self):
        agg = aggregate_scores([])
        assert agg.total_tp == 0
        assert agg.total_fp == 0
        assert agg.total_fn == 0
        assert agg.precision == 1.0
        assert agg.recall == 1.0
        assert agg.episode_count == 0
        assert agg.start_error_mean == 0.0
        assert agg.start_error_p95 == 0.0

    def test_episode_count(self):
        scores = [score_episode([_seg(0.0, 30.0)], [_seg(0.0, 30.0)]) for _ in range(3)]
        agg = aggregate_scores(scores)
        assert agg.episode_count == 3

    def test_totals_summed(self):
        s1 = EpisodeScore(
            true_positives=2, false_positives=1, false_negatives=0,
            start_errors=[1.0, -2.0], end_errors=[0.5, -0.5],
            content_lost_seconds=10.0, ads_missed_seconds=0.0,
        )
        s2 = EpisodeScore(
            true_positives=1, false_positives=0, false_negatives=1,
            start_errors=[3.0], end_errors=[2.0],
            content_lost_seconds=0.0, ads_missed_seconds=20.0,
        )
        agg = aggregate_scores([s1, s2])
        assert agg.total_tp == 3
        assert agg.total_fp == 1
        assert agg.total_fn == 1
        assert agg.total_content_lost == pytest.approx(10.0)
        assert agg.total_ads_missed == pytest.approx(20.0)
        assert agg.episode_count == 2

    def test_precision_recall_f1_calculation(self):
        # TP=4, FP=1, FN=1 across all episodes
        s = EpisodeScore(
            true_positives=4, false_positives=1, false_negatives=1,
            start_errors=[], end_errors=[],
            content_lost_seconds=0.0, ads_missed_seconds=0.0,
        )
        agg = aggregate_scores([s])
        assert agg.precision == pytest.approx(4 / 5)
        assert agg.recall == pytest.approx(4 / 5)

    def test_start_error_median_and_p95(self):
        # abs start errors: [1, 2, 3, 4, 5]
        s = EpisodeScore(
            true_positives=5, false_positives=0, false_negatives=0,
            start_errors=[1.0, -2.0, 3.0, -4.0, 5.0],
            end_errors=[],
            content_lost_seconds=0.0, ads_missed_seconds=0.0,
        )
        agg = aggregate_scores([s])
        assert agg.start_error_median == pytest.approx(3.0)  # median of [1,2,3,4,5]
        assert agg.start_error_p95 == pytest.approx(4.8)    # 95th of [1,2,3,4,5]
        assert agg.start_error_mean == pytest.approx(3.0)   # mean of [1,2,3,4,5]

    def test_end_error_statistics(self):
        s = EpisodeScore(
            true_positives=1, false_positives=0, false_negatives=0,
            start_errors=[0.0],
            end_errors=[-5.0],
            content_lost_seconds=0.0, ads_missed_seconds=0.0,
        )
        agg = aggregate_scores([s])
        assert agg.end_error_mean == pytest.approx(5.0)
        assert agg.end_error_median == pytest.approx(5.0)
        assert agg.end_error_p95 == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# _median and _percentile helpers
# ---------------------------------------------------------------------------

class TestMedianAndPercentile:
    def test_median_empty(self):
        assert _median([]) == 0.0

    def test_median_odd(self):
        assert _median([3.0, 1.0, 2.0]) == 2.0

    def test_median_even(self):
        assert _median([1.0, 2.0, 3.0, 4.0]) == pytest.approx(2.5)

    def test_percentile_empty(self):
        assert _percentile([], 95) == 0.0

    def test_percentile_single(self):
        assert _percentile([7.0], 95) == pytest.approx(7.0)

    def test_percentile_100(self):
        assert _percentile([1.0, 2.0, 3.0], 100) == pytest.approx(3.0)

    def test_percentile_0(self):
        assert _percentile([1.0, 2.0, 3.0], 0) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# format_report
# ---------------------------------------------------------------------------

class TestFormatReport:
    def test_header_and_separator_present(self):
        results: dict[str, AggregateScore] = {}
        report = format_report(results)
        lines = report.splitlines()
        assert len(lines) == 2
        assert "Config" in lines[0]
        assert "Prec" in lines[0]
        assert "-" * 10 in lines[1]

    def test_normal_row_has_config_name(self):
        agg = AggregateScore(
            total_tp=1, total_fp=0, total_fn=0,
            precision=1.0, recall=1.0, f1=1.0,
            start_error_mean=0.0, start_error_median=0.0, start_error_p95=0.0,
            end_error_mean=0.0, end_error_median=0.0, end_error_p95=0.0,
            total_content_lost=0.0, total_ads_missed=0.0,
            episode_count=1,
        )
        report = format_report({"my-config": agg})
        assert "my-config" in report
        assert "1.00" in report  # precision

    def test_zero_episode_count_shows_special_message(self):
        agg = AggregateScore(
            total_tp=0, total_fp=0, total_fn=0,
            precision=1.0, recall=1.0, f1=0.0,
            start_error_mean=0.0, start_error_median=0.0, start_error_p95=0.0,
            end_error_mean=0.0, end_error_median=0.0, end_error_p95=0.0,
            total_content_lost=0.0, total_ads_missed=0.0,
            episode_count=0,
        )
        report = format_report({"empty-config": agg})
        assert "no episodes scored" in report

    def test_multiple_configs_multiple_rows(self):
        def _agg(ep_count: int) -> AggregateScore:
            return AggregateScore(
                total_tp=1, total_fp=0, total_fn=0,
                precision=1.0, recall=1.0, f1=1.0,
                start_error_mean=0.0, start_error_median=0.0, start_error_p95=0.0,
                end_error_mean=0.0, end_error_median=0.0, end_error_p95=0.0,
                total_content_lost=0.0, total_ads_missed=0.0,
                episode_count=ep_count,
            )

        report = format_report({"config-a": _agg(1), "config-b": _agg(2)})
        assert "config-a" in report
        assert "config-b" in report
