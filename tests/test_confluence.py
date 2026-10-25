"""Tests for confluence score computation (Phase 3)."""

from __future__ import annotations

from trader.features.confluence import confluence_score, confluence_score_both


def _long_args() -> dict:
    """All 6 groups signal long."""
    return dict(
        side="long",
        htf_aligned=True,
        htf_direction="up",
        ema_cross="up",
        rsi=60.0,
        macd_hist=0.5,
        funding_z=-2.0,
        oi_divergence=False,
        cvd_divergence_bear=False,
        cvd_divergence_bull=False,
        book_imbalance=0.2,
        regime="trend_up",
        dist_to_support_atr=0.5,
        dist_to_resistance_atr=None,
    )


def _short_args() -> dict:
    """All 6 groups signal short."""
    return dict(
        side="short",
        htf_aligned=True,
        htf_direction="down",
        ema_cross="down",
        rsi=40.0,
        macd_hist=-0.5,
        funding_z=2.0,
        oi_divergence=False,
        cvd_divergence_bear=False,
        cvd_divergence_bull=False,
        book_imbalance=-0.2,
        regime="trend_down",
        dist_to_support_atr=None,
        dist_to_resistance_atr=0.5,
    )


def _neutral_args(side: str = "long") -> dict:
    """No groups signal anything."""
    return dict(
        side=side,
        htf_aligned=False,
        htf_direction="neutral",
        ema_cross="neutral",
        rsi=50.0,
        macd_hist=0.0,
        funding_z=0.0,
        oi_divergence=False,
        cvd_divergence_bear=False,
        cvd_divergence_bull=False,
        book_imbalance=0.0,
        regime="range",
        dist_to_support_atr=None,
        dist_to_resistance_atr=None,
    )


# ---------------------------------------------------------------------------
# Basic scoring
# ---------------------------------------------------------------------------


def test_all_signals_long() -> None:
    """All 6 groups signal long → score = 6."""
    score = confluence_score(**_long_args())
    assert score == 6


def test_all_signals_short() -> None:
    """All 6 groups signal short → score = 6."""
    score = confluence_score(**_short_args())
    assert score == 6


def test_no_signals() -> None:
    """Neutral everything → score = 0."""
    score = confluence_score(**_neutral_args("long"))
    assert score == 0


def test_mixed_signals() -> None:
    """3 long, 2 short, 1 neutral → long side gets 3."""
    args = dict(
        side="long",
        htf_aligned=True,
        htf_direction="up",
        ema_cross="up",  # trend group: +1
        rsi=60.0,
        macd_hist=0.5,  # momentum: +1
        funding_z=0.0,  # derivatives: neutral
        oi_divergence=False,
        cvd_divergence_bear=False,
        cvd_divergence_bull=False,
        book_imbalance=0.2,  # orderflow: +1
        regime="range",  # regime: 0
        dist_to_support_atr=None,
        dist_to_resistance_atr=None,
    )
    score = confluence_score(**args)
    assert score == 3


# ---------------------------------------------------------------------------
# Special cases
# ---------------------------------------------------------------------------


def test_high_vol_breakout_counts_both_sides() -> None:
    """'high_vol_breakout' regime counts for both long and short regime group."""
    # Long side: regime="high_vol_breakout" should count
    long_args = dict(
        side="long",
        htf_aligned=False,
        htf_direction="neutral",
        ema_cross="neutral",
        rsi=50.0,
        macd_hist=0.0,
        funding_z=0.0,
        oi_divergence=False,
        cvd_divergence_bear=False,
        cvd_divergence_bull=False,
        book_imbalance=0.0,
        regime="high_vol_breakout",
        dist_to_support_atr=None,
        dist_to_resistance_atr=None,
    )
    short_args = {**long_args, "side": "short"}

    long_score = confluence_score(**long_args)
    short_score = confluence_score(**short_args)

    assert long_score >= 1
    assert short_score >= 1


def test_score_is_deterministic() -> None:
    """Same args → same result."""
    args = _long_args()
    r1 = confluence_score(**args)
    r2 = confluence_score(**args)
    assert r1 == r2


def test_confluence_score_both() -> None:
    """confluence_score_both returns (score_long, score_short) in one call."""
    args = _long_args()
    # Remove 'side' since both is computed for both sides
    args_no_side = {k: v for k, v in args.items() if k != "side"}
    score_long, score_short = confluence_score_both(**args_no_side)
    assert isinstance(score_long, int)
    assert isinstance(score_short, int)
    assert 0 <= score_long <= 6
    assert 0 <= score_short <= 6


def test_score_range() -> None:
    """Score is always in [0, 6]."""
    for args in [_long_args(), _short_args(), _neutral_args("long"), _neutral_args("short")]:
        score = confluence_score(**args)
        assert 0 <= score <= 6


def test_funding_z_below_threshold_no_short_signal() -> None:
    """funding_z = -2.0 (shorts paying) signals long, not short."""
    short_args = _short_args()
    short_args["funding_z"] = -2.0  # bullish for long, not short
    # This should reduce the short score
    score_modified = confluence_score(**short_args)
    # With all other signals still short, score should be 5 (not 6)
    assert score_modified == 5
