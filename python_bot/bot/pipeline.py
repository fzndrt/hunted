"""Orkestrator Pipeline 4-Layer (Batch Scan & Single-Mint Pre-Trade Audit)."""

from __future__ import annotations

import asyncio
from typing import Optional
import httpx

from .anti_top import evaluate_anti_top
from .config import BotConfig
from .execution import verify_jupiter_execution
from .feeds import discover_solana_candidates, fetch_pair_for_mint
from .models import AuditResult, ExecutionResult, GateCheck, PairSnapshot, SecurityResult
from .scoring import score_and_finalize
from .security import evaluate_security, fetch_rugcheck_report


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
                threshold="Wajib lolos Layer 1 Security terlebih dahulu",
                detail=reason,
            ),
        ),
        round_trip_loss_01_sol_pct=None,
        depth_loss_1_sol_pct=None,
        buy_impact_01_sol_pct=None,
        sell_impact_01_sol_pct=None,
    )


def _skipped_security_result(pair: PairSnapshot, reason: str) -> SecurityResult:
    from .models import SecurityResult

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
        return score_and_finalize(pair, security, anti_top, execution, cfg)

    rugcheck = await fetch_rugcheck_report(client, pair.mint)
    security = evaluate_security(pair, rugcheck)

    if (security.passed and anti_top.passed) or always_run_jupiter:
        execution = await verify_jupiter_execution(client, pair.mint, cfg)
    elif not security.passed:
        execution = _skipped_execution_result(
            "Dilewati untuk hemat kuota API Jupiter karena token ditolak di Layer 1 Security.",
            status="SKIPPED_SECURITY_VETO",
        )
    else:
        execution = _skipped_execution_result(
            "Dilewati pada scan batch karena token berstatus WAIT_DIP (aman on-chain, tunggu pullback).",
            status="SKIPPED_WAIT_DIP",
        )

    return score_and_finalize(pair, security, anti_top, execution, cfg)


async def inspect_mint(
    client: httpx.AsyncClient, mint: str, cfg: BotConfig
) -> Optional[AuditResult]:
    pair = await fetch_pair_for_mint(client, mint, feed_sources=("manual_inspect",))
    if not pair:
        return None
    return await audit_pair(client, pair, cfg, always_run_jupiter=True)


async def run_scan_cycle(
    client: httpx.AsyncClient, cfg: BotConfig, max_stage2: int = 16
) -> tuple[list[AuditResult], list[str]]:
    from .config import ORGANIC_FEED_SOURCES

    candidates, feed_notes = await discover_solana_candidates(client, max_candidates=60)

    # Prioritaskan token organik di koridor $25K–$450K yang benar-benar punya kecepatan volume 5m hidup
    def _candidate_rank(p: PairSnapshot) -> tuple[int, int, float, float]:
        organic_hits = sum(1 for src in p.feed_sources if src in ORGANIC_FEED_SOURCES)
        in_corridor = 1 if (22_000.0 <= p.market_cap <= 450_000.0 and p.liquidity_usd >= 10_000.0) else 0
        has_live_vol = 1 if p.vol_m5 >= 1_200.0 else 0
        return (in_corridor, has_live_vol, organic_hits * 25_000.0 + p.vol_m5, p.vol_h1)

    eligible = [
        p
        for p in candidates
        if 20_000.0 <= p.market_cap <= 600_000.0 and p.liquidity_usd >= 8_000.0
    ]
    eligible.sort(key=_candidate_rank, reverse=True)

    effective_limit = min(max_stage2, 12) if cfg.is_vercel else max_stage2
    to_audit = eligible[:effective_limit]
    sem = asyncio.Semaphore(5 if cfg.is_vercel else 4)

    async def bounded_audit(pair: PairSnapshot) -> AuditResult:
        async with sem:
            return await audit_pair(client, pair, cfg, always_run_jupiter=False)

    results = list(await asyncio.gather(*(bounded_audit(p) for p in to_audit)))
    rank = {"ENTRY_READY": 0, "WATCH_PULLBACK": 1, "REJECTED": 2}
    results.sort(key=lambda r: (rank[r.verdict], -r.total_score))
    return results, feed_notes

