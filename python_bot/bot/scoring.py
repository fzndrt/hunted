"""Layer 4 — 5-Pillar Additive Scoring (0–100) & Deterministic Trade Plan."""

from __future__ import annotations

import time
from typing import Optional

from .config import BotConfig, ENTRY_CORRIDORS
from .models import (
    AntiTopResult,
    AuditResult,
    ExecutionResult,
    PairSnapshot,
    PillarScore,
    SecurityResult,
    StructureResult,
    TradePlan,
    Verdict,
)


def compute_position_size_usd(
    capital_usd: float, risk_per_trade_pct: float, hard_stop_pct: float
) -> float:
    """Hitung ukuran entry real-money mundur dari risiko maksimal per trade."""
    if capital_usd <= 0 or hard_stop_pct <= 0:
        return 0.0
    max_risk_usd = capital_usd * (min(20.0, max(0.25, risk_per_trade_pct)) / 100.0)
    return round(min(capital_usd, max_risk_usd / (hard_stop_pct / 100.0)), 2)


def build_trade_plan(
    anti_top: AntiTopResult, structure: Optional[StructureResult] = None
) -> TradePlan:
    corridor = next(
        (c for c in ENTRY_CORRIDORS if c.corridor_id == anti_top.corridor_id),
        ENTRY_CORRIDORS[0],
    )
    base_mc = max(anti_top.projected_entry_mc, anti_top.spot_mc, 1.0)
    max_safe_entry_mc = min(corridor.max_mc, base_mc * 1.08)

    is_daily_breakout = (
        structure is not None and structure.regime == "BULLISH_CONSOLIDATION_BREAKOUT"
    ) or anti_top.is_sleeper_revival

    # Untuk Daily Consolidation Breakout / Sleeper Revival: target TP terukur sebelum resistensi ATH lama
    if is_daily_breakout:
        if corridor.corridor_id == "CORRIDOR_A_EARLY":
            sl_pct, tp1_pct, tp2_pct = 15.0, 30.0, 65.0
        elif corridor.corridor_id == "CORRIDOR_B_POST100K":
            sl_pct, tp1_pct, tp2_pct = 14.0, 28.0, 60.0
        else:
            sl_pct, tp1_pct, tp2_pct = 12.0, 25.0, 50.0
    else:
        sl_pct = corridor.default_hard_stop_pct
        tp1_pct = corridor.default_tp1_pct
        tp2_pct = corridor.default_tp2_pct

    return TradePlan(
        corridor_label=corridor.label,
        max_safe_entry_mc=round(max_safe_entry_mc, 2),
        hard_stop_mc=round(base_mc * (1.0 - sl_pct / 100.0), 2),
        hard_stop_pct=sl_pct,
        tp1_mc=round(base_mc * (1.0 + tp1_pct / 100.0), 2),
        tp1_pct=tp1_pct,
        tp2_mc=round(base_mc * (1.0 + tp2_pct / 100.0), 2),
        tp2_pct=tp2_pct,
        risk_reward_to_tp1=round(tp1_pct / sl_pct, 2),
        risk_reward_to_tp2=round(tp2_pct / sl_pct, 2),
    )


def score_and_finalize(
    pair: PairSnapshot,
    security: SecurityResult,
    anti_top: AntiTopResult,
    execution: ExecutionResult,
    cfg: BotConfig,
    structure: Optional[StructureResult] = None,
) -> AuditResult:
    # Pilar 1: Keamanan, Distribusi & Kecepatan Adopsi Holder (25 poin)
    p1 = 0
    if security.passed:
        p1 += 13
        if (security.top10_pct or 100.0) <= 22.0:
            p1 += 5
        elif (security.top10_pct or 100.0) <= 28.0:
            p1 += 3
        if (security.dev_pct or 100.0) <= 1.0:
            p1 += 3
        elif (security.dev_pct or 100.0) <= 2.0:
            p1 += 2
        if security.total_holders >= 1_500 or security.holders_per_min >= 10.0:
            p1 += 4
        elif security.total_holders >= 400 or security.holders_per_min >= 4.0:
            p1 += 3
        elif security.total_holders >= 220:
            p1 += 1
    else:
        p1 = sum(2 for c in security.checks if c.passed)
        p1 = min(p1, 10)

    # Pilar 2: Posisi Entry (Corridor A–D) & Headroom (25 poin)
    p2 = 0
    if anti_top.corridor_id == "CORRIDOR_A_EARLY":
        p2 += 14
    elif anti_top.corridor_id == "CORRIDOR_B_POST100K":
        p2 += 13
    elif anti_top.corridor_id == "CORRIDOR_C_EXPANSION":
        p2 += 12
    elif anti_top.corridor_id == "CORRIDOR_D_VIRAL_BASE":
        p2 += 12
    if anti_top.net_headroom_pct >= 35.0:
        p2 += 11
    elif anti_top.net_headroom_pct >= 25.0:
        p2 += 8
    elif anti_top.net_headroom_pct >= cfg.min_net_headroom_pct:
        p2 += 6

    # Pilar 3: Viral Demand Velocity, Kontraksi Pullback & Whale Absorption (20 poin)
    p3 = 0
    if 0.0 <= anti_top.p_m5 <= cfg.max_m5_change_pct:
        p3 += 6
    elif -3.5 <= anti_top.p_m5 < 0.0:
        p3 += 4
    if anti_top.buy_ratio_5m_pct >= 55.0 or anti_top.whale_absorption:
        p3 += 5
    elif anti_top.buy_ratio_5m_pct >= 48.0:
        p3 += 4
    if (
        anti_top.is_viral_velocity
        or anti_top.vol_m5_accel_ratio >= 1.15
        or anti_top.turnover_h1_ratio >= 1.0
    ):
        p3 += 5
    elif (
        anti_top.is_pullback_contraction_ok
        or anti_top.vol_m5_accel_ratio >= 0.75
        or anti_top.turnover_h1_ratio >= 0.45
    ):
        p3 += 3
    elif anti_top.turnover_h1_ratio >= cfg.min_turnover_h1_ratio:
        p3 += 2
    if anti_top.avg_ticket_5m_usd >= 60.0 or anti_top.avg_ticket_1h_usd >= 60.0:
        p3 += 4
    elif anti_top.avg_ticket_5m_usd >= 28.0 or anti_top.avg_ticket_1h_usd >= 28.0:
        p3 += 2

    # Pilar 4: Likuiditas & Kedalaman Jupiter (20 poin)
    p4 = 0
    if execution.passed and execution.round_trip_loss_01_sol_pct is not None:
        if execution.round_trip_loss_01_sol_pct <= 2.0:
            p4 += 8
        elif execution.round_trip_loss_01_sol_pct <= 3.5:
            p4 += 6
        else:
            p4 += 4
        if (execution.depth_loss_1_sol_pct or 99.0) <= 7.0:
            p4 += 8
        elif (execution.depth_loss_1_sol_pct or 99.0) <= 11.0:
            p4 += 6
        else:
            p4 += 4
        if anti_top.liq_mc_ratio_pct >= 14.0:
            p4 += 4
        elif anti_top.liq_mc_ratio_pct >= 9.0:
            p4 += 2

    # Pilar 5: Struktur Chart OHLCV, Multi-Pool Smart LP & Organik (10 poin)
    from .config import ORGANIC_FEED_SOURCES

    organic_feeds_count = sum(1 for s in pair.feed_sources if s in ORGANIC_FEED_SOURCES)
    total_pools = max(int(pair.pool_count or 1), int(security.active_markets_count or 1))
    p5 = 0
    if structure is not None and structure.regime == "BULLISH_CONSOLIDATION_BREAKOUT":
        p5 += 5
        age_days = anti_top.age_minutes / 1440.0
        p5_summary = f"DAILY BREAKOUT ({age_days:.1f}hri · Box {structure.base_range_pct:.0f}% · GreenVol {structure.green_vol_share_pct:.0f}%)"
    elif structure is not None and structure.passed and structure.ohlcv_verified:
        p5 += 5
        p5_summary = f"{structure.regime} · {total_pools} pool · {organic_feeds_count} organik"
    elif anti_top.is_sleeper_revival:
        p5 += 5
        age_days = anti_top.age_minutes / 1440.0
        age_str = f"{age_days:.1f}hri" if age_days >= 1.0 else f"{anti_top.age_minutes / 60.0:.0f}j"
        p5_summary = f"SLEEPER REVIVAL ({age_str} · Vol x{anti_top.vol_wake_5m_ratio:.1f}) · {organic_feeds_count} organik"
    elif structure is not None and not structure.passed:
        p5 += 0
        p5_summary = f"{structure.regime} · {organic_feeds_count} organik"
    else:
        if 20.0 <= anti_top.age_minutes <= 360.0:
            p5 += 4
        elif anti_top.age_minutes > 360.0:
            p5 += 3
        else:
            p5 += 1
        p5_summary = f"Usia {anti_top.age_minutes:.0f}m · {total_pools} pool · {organic_feeds_count} organik"

    organic_pts = organic_feeds_count * 2 + (1 if organic_feeds_count > 0 else 0)
    if total_pools >= 2:
        organic_pts += 1
    if not pair.is_boosted:
        organic_pts += 1
    p5 += min(5, organic_pts)

    pillars = (
        PillarScore(
            "Keamanan & Adopsi",
            p1,
            25,
            f"{security.pareto_status} · {security.total_holders:,}H ({security.holders_per_min:.1f}/m)",
        ),
        PillarScore("Koridor & Headroom", p2, 25, f"Net +{anti_top.net_headroom_pct:.1f}%"),
        PillarScore(
            "Viral Demand & Flow",
            p3,
            20,
            f"5m ${pair.vol_m5:,.0f} · Turn1h {anti_top.turnover_h1_ratio:.2f}x",
        ),
        PillarScore("Likuiditas & Jupiter", p4, 20, execution.status),
        PillarScore("Chart OHLCV & Smart LP", p5, 10, p5_summary),
    )
    total_score = sum(p.score for p in pillars)

    structure_ok = structure is None or structure.passed
    structure_wait_only = structure is not None and (not structure.passed) and structure.wait_dip_only
    structure_hard_veto = structure is not None and (not structure.passed) and (not structure.wait_dip_only)

    # Keputusan akhir TANPA bypass tersembunyi
    verdict: Verdict
    trade_plan: Optional[TradePlan] = None
    if not security.passed or structure_hard_veto or (
        not execution.passed and execution.status not in {"SKIPPED_WAIT_DIP", "SKIPPED_STRUCTURE_VETO"}
    ):
        verdict = "REJECTED"
        if not security.passed:
            first_fail = next((c for c in security.checks if not c.passed), None)
            headline = first_fail.detail if first_fail else "Ditolak pada Layer 1 Security."
        elif structure_hard_veto and structure is not None:
            first_fail = next((c for c in structure.checks if not c.passed), None)
            headline = first_fail.detail if first_fail else f"Ditolak struktur OHLCV ({structure.regime})."
        else:
            first_fail = next((c for c in execution.checks if not c.passed), None)
            headline = first_fail.detail if first_fail else "Ditolak pada simulasi eksekusi Jupiter."
    elif anti_top.passed and structure_ok and execution.passed and total_score >= cfg.min_score:
        verdict = "ENTRY_READY"
        trade_plan = build_trade_plan(anti_top, structure)
        if structure is not None and structure.regime == "BULLISH_CONSOLIDATION_BREAKOUT":
            age_days = anti_top.age_minutes / 1440.0
            headline = (
                f"DAILY CONSOLIDATION BREAKOUT ({total_score}/100 · Usia {age_days:.1f} hari · Box {structure.base_range_pct:.0f}%) — "
                f"Target TP1 +{trade_plan.tp1_pct:.0f}% / TP2 +{trade_plan.tp2_pct:.0f}%, SL -{trade_plan.hard_stop_pct:.0f}%."
            )
        elif anti_top.is_sleeper_revival:
            age_days = anti_top.age_minutes / 1440.0
            age_str = f"{age_days:.1f} hari" if age_days >= 1.0 else f"{anti_top.age_minutes / 60.0:.0f} jam"
            headline = (
                f"SLEEPER REVIVAL PRE-ATH ({total_score}/100 · Usia {age_str} · Vol Wake x{anti_top.vol_wake_5m_ratio:.1f}) — "
                f"Target cepat TP1 +{trade_plan.tp1_pct:.0f}% / TP2 +{trade_plan.tp2_pct:.0f}%, SL -{trade_plan.hard_stop_pct:.0f}%."
            )
        else:
            regime_tag = structure.regime if structure else "CONTINUATION"
            headline = (
                f"LOLOS 5 GERBANG ({total_score}/100 · {regime_tag}) — Masuk {anti_top.corridor_label}. "
                f"Batas entry <= ${trade_plan.max_safe_entry_mc:,.0f}, SL -{trade_plan.hard_stop_pct:.0f}%."
            )
    elif anti_top.wait_dip_only or structure_wait_only:
        verdict = "WATCH_PULLBACK"
        trade_plan = build_trade_plan(anti_top, structure)
        if structure_wait_only and structure is not None:
            first_wait = next((c for c in structure.checks if not c.passed), None)
            headline = (
                f"Aman on-chain, tunggu konfirmasi chart: "
                f"{first_wait.detail if first_wait else structure.summary}"
            )
        else:
            first_wait = next((c for c in anti_top.checks if not c.passed), None)
            headline = (
                f"Aman on-chain & Jupiter, namun tunggu pullback/breakout: "
                f"{first_wait.detail if first_wait else 'di luar koridor ideal.'}"
            )
    elif anti_top.passed and structure_ok:
        verdict = "WATCH_PULLBACK"
        trade_plan = build_trade_plan(anti_top, structure)
        headline = f"Lolos gerbang keras, skor {total_score}/100 masih di bawah ambang minimal ({cfg.min_score})."
    else:
        verdict = "REJECTED"
        first_fail = next((c for c in anti_top.checks if not c.passed), None)
        headline = first_fail.detail if first_fail else "Ditolak oleh filter Anti-Top."

    return AuditResult(
        pair=pair,
        security=security,
        anti_top=anti_top,
        execution=execution,
        pillars=pillars,
        total_score=total_score,
        verdict=verdict,
        headline=headline,
        trade_plan=trade_plan,
        audited_at_ms=int(time.time() * 1000),
        structure=structure,
    )
