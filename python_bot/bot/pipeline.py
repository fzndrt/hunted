"""Orkestrator Pipeline 4-Layer + Layer 2B OHLCV Structure (Batch Scan & Single-Mint Audit)."""

from __future__ import annotations

import asyncio
import time
from typing import Optional
import httpx

from .anti_top import evaluate_anti_top
from .config import BotConfig
from .execution import verify_jupiter_execution
from .feeds import discover_solana_candidates, fetch_pair_for_mint
from .models import (
    AuditResult,
    ExecutionResult,
    GateCheck,
    PairSnapshot,
    SecurityResult,
    StructureResult,
)
from .scoring import score_and_finalize
from .security import evaluate_security, fetch_rugcheck_report
from .structure import fetch_and_evaluate_structure


def _skipped_execution_result(
    reason: str, status: str = "SKIPPED_SECURITY_VETO"
) -> ExecutionResult:
    return ExecutionResult(
        passed=False,
        status=status,
        checks=(
            GateCheck(
                id="jup_skipped",
                layer="EXECUTION",
                label="Simulasi Jupiter Dilewati",
                passed=False,
                severity="VETO",
                actual="SKIPPED",
                threshold="Wajib lolos Layer 1, 2 & 2B OHLCV terlebih dahulu",
                detail=reason,
            ),
        ),
        round_trip_loss_01_sol_pct=None,
        depth_loss_1_sol_pct=None,
        buy_impact_01_sol_pct=None,
        sell_impact_01_sol_pct=None,
    )


def _skipped_security_result(pair: PairSnapshot, reason: str) -> SecurityResult:
    return SecurityResult(
        passed=False,
        checks=(
            GateCheck(
                id="sec_skipped_l2",
                layer="SECURITY",
                label="Audit RugCheck Dilewati (Veto Layer 2)",
                passed=False,
                severity="VETO",
                actual="SKIPPED",
                threshold="Wajib lolos saringan awal Layer 2",
                detail=reason,
            ),
        ),
        hard_quote_symbol=pair.quote_symbol,
        rugcheck_score=None,
        dev_pct=None,
        top1_pct=None,
        top10_pct=None,
        lp_locked_pct=None,
        lp_locked_usd=None,
        is_meteora_nft_lp=False,
        pareto_status="SKIPPED",
    )


async def audit_pair(
    client: httpx.AsyncClient,
    pair: PairSnapshot,
    cfg: BotConfig,
    always_run_jupiter: bool = False,
) -> AuditResult:
    anti_top = evaluate_anti_top(pair, cfg)

    # Pada scan batch (bukan /inspect), hemat waktu & kuota API untuk token yang sudah terkena Hard Veto Layer 2
    if not always_run_jupiter and not anti_top.passed and not anti_top.wait_dip_only:
        first_veto = next((c.detail for c in anti_top.checks if not c.passed), "Veto Layer 2.")
        security = _skipped_security_result(pair, first_veto)
        execution = _skipped_execution_result("Dilewati karena ditolak pada Layer 2 Anti-Top.")
        return score_and_finalize(pair, security, anti_top, execution, cfg, structure=None)

    rugcheck = await fetch_rugcheck_report(client, pair.mint)
    security = evaluate_security(
        pair,
        rugcheck,
        require_hyper_viral_holders=anti_top.is_hyper_viral_fasttrack,
        require_steady_climber_holders=anti_top.is_steady_organic_climber,
    )

    # Layer 2B: Verifikasi Struktur Candle OHLCV asli (Anti-Pucuk Blow-Off & Klasifikasi Konsolidasi Harian)
    structure: Optional[StructureResult] = None
    if (security.passed and anti_top.passed) or always_run_jupiter:
        structure = await fetch_and_evaluate_structure(client, pair, anti_top, cfg)

    if (
        security.passed
        and anti_top.passed
        and (structure is None or structure.passed)
    ) or always_run_jupiter:
        execution = await verify_jupiter_execution(client, pair.mint, cfg)
    elif not security.passed:
        execution = _skipped_execution_result(
            "Dilewati untuk hemat kuota API Jupiter karena token ditolak di Layer 1 Security.",
            status="SKIPPED_SECURITY_VETO",
        )
    elif structure is not None and not structure.passed:
        execution = _skipped_execution_result(
            f"Dilewati karena struktur OHLCV ditolak/menunggu ({structure.regime}): {structure.summary}",
            status="SKIPPED_WAIT_DIP" if structure.wait_dip_only else "SKIPPED_STRUCTURE_VETO",
        )
    else:
        execution = _skipped_execution_result(
            "Dilewati pada scan batch karena token berstatus WAIT_DIP (aman on-chain, tunggu pullback).",
            status="SKIPPED_WAIT_DIP",
        )

    return score_and_finalize(pair, security, anti_top, execution, cfg, structure=structure)


async def inspect_mint(
    client: httpx.AsyncClient, mint: str, cfg: BotConfig
) -> Optional[AuditResult]:
    pair = await fetch_pair_for_mint(client, mint, feed_sources=("manual_inspect",))
    if not pair:
        return None
    return await audit_pair(client, pair, cfg, always_run_jupiter=True)


async def run_scan_cycle(
    client: httpx.AsyncClient,
    cfg: BotConfig,
    max_stage2: int = 16,
    watchlist_mints: Optional[list[str]] = None,
) -> tuple[list[AuditResult], list[str]]:
    from .config import ORGANIC_FEED_SOURCES

    candidates, feed_notes = await discover_solana_candidates(
        client, max_candidates=75, watchlist_mints=watchlist_mints
    )
    now_ms = int(time.time() * 1000)

    def _age_min(p: PairSnapshot) -> float:
        if p.pair_created_at_ms <= 0:
            return 999.0
        return max(0.1, (now_ms - p.pair_created_at_ms) / 60_000.0)

    def _candidate_rank(p: PairSnapshot) -> tuple[int, int, int, int, int, float, float]:
        is_wl = 1 if "watchlist_rescan" in p.feed_sources else 0
        non_boosted = 0 if p.is_boosted else 1
        organic_hits = sum(1 for src in p.feed_sources if src in ORGANIC_FEED_SOURCES)
        in_core_corridor = 22_000.0 <= p.market_cap <= 395_000.0 and p.liquidity_usd >= 10_000.0
        in_viral_base_d = (
            395_000.0 < p.market_cap <= 880_000.0
            and p.vol_h1 >= 220_000.0
            and p.liquidity_usd >= 38_000.0
        )
        in_corridor = 1 if (in_core_corridor or in_viral_base_d) else 0
        age_m = _age_min(p)
        survived_sniper = 1 if (
            age_m >= cfg.min_pair_age_minutes
            or (6.0 <= age_m < cfg.min_pair_age_minutes and p.vol_m5 >= 25_000.0 and p.vol_h1 >= 90_000.0)
        ) else 0
        has_live_vol = 1 if (
            p.vol_m5 >= 4_500.0 or (age_m >= 720.0 and p.vol_m5 >= 1_800.0)
        ) else 0
        early_wave_bonus = (
            28_000.0
            if (25_000.0 <= p.market_cap <= 180_000.0 and 6.0 <= age_m <= 180.0 and p.price_change_h24 < 450.0)
            else 0.0
        )
        community_bonus = 18_000.0 if (p.has_socials or p.has_website) else 0.0
        multi_pool_bonus = 20_000.0 if p.pool_count >= 2 else 0.0
        green_bonus = 15_000.0 if p.price_change_m5 >= 0.5 else 0.0
        return (
            in_corridor,
            non_boosted,
            is_wl,
            survived_sniper,
            has_live_vol,
            organic_hits * 25_000.0 + early_wave_bonus + community_bonus + multi_pool_bonus + green_bonus + p.vol_m5,
            p.vol_h1,
        )

    eligible = [
        p
        for p in candidates
        if (
            (20_000.0 <= p.market_cap <= 450_000.0 and p.liquidity_usd >= 10_000.0)
            or (450_000.0 < p.market_cap <= 900_000.0 and p.vol_h1 >= 220_000.0 and p.liquidity_usd >= 38_000.0)
        )
        and not (_age_min(p) < 20.0 * 60.0 and (p.price_change_h24 >= 2_000.0 or p.price_change_h6 >= 1_200.0))
    ]

    # Bagi slot Stage-2 antara koin Intraday (6m–20j) dan koin Konsolidasi Harian (>= 20j)
    # agar koin harian yang sedang konsolidasi naik TIDAK kalah slot dari koin baru!
    daily_pool = [p for p in eligible if _age_min(p) >= 20.0 * 60.0]
    intraday_pool = [p for p in eligible if _age_min(p) < 20.0 * 60.0]

    daily_pool.sort(key=_candidate_rank, reverse=True)
    intraday_pool.sort(key=_candidate_rank, reverse=True)

    effective_limit = min(max_stage2, 12) if cfg.is_vercel else max_stage2
    daily_slots = min(len(daily_pool), 4)
    intraday_slots = max(0, effective_limit - daily_slots)

    to_audit: list[PairSnapshot] = intraday_pool[:intraday_slots] + daily_pool[:daily_slots]
    if len(to_audit) < effective_limit:
        seen = {p.mint for p in to_audit}
        for p in intraday_pool[intraday_slots:] + daily_pool[daily_slots:]:
            if p.mint not in seen and len(to_audit) < effective_limit:
                to_audit.append(p)
                seen.add(p.mint)

    # Gunakan concurrency 3 agar GeckoTerminal OHLCV pada finalis tidak terkena 429 burst
    sem = asyncio.Semaphore(3)

    async def bounded_audit(pair: PairSnapshot) -> AuditResult:
        async with sem:
            return await audit_pair(client, pair, cfg, always_run_jupiter=False)

    results = list(await asyncio.gather(*(bounded_audit(p) for p in to_audit)))
    rank = {"ENTRY_READY": 0, "WATCH_PULLBACK": 1, "REJECTED": 2}
    results.sort(key=lambda r: (rank[r.verdict], -r.total_score))
    return results, feed_notes


