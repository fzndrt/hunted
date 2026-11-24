"""Unit test verifikasi seluruh perbaikan kritis arsitektur 5-layer."""

from __future__ import annotations

import asyncio
import math
import tempfile
import os

from bot.anti_top import compute_effective_min_tx_24h, evaluate_anti_top
from bot.config import BotConfig, SOL_MINT
from bot.execution import evaluate_execution_quotes
from bot.exit_guard import (
    ExitGuardStore,
    compute_real_lp_retention_pct,
    evaluate_position_exit,
)
from bot.models import PairSnapshot, TrackedPosition
from bot.pumpportal_stream import (
    build_subscribe_token_trade_payload,
    compute_pumpfun_bonding_progress_pct,
    parse_pumpportal_trade_event,
)
from bot.security import evaluate_security


def _make_pair(**overrides) -> PairSnapshot:
    now_ms = 1_750_000_000_000
    base = dict(
        mint="BzPyyYCTmQNq2BcfQ4ANAvCYk54rnmQEXMFvFT8RhWMi",
        pair_address="Pair1111111111111111111111111111111111111111",
        dex_id="pumpswap",
        symbol="MCAT",
        name="Moon Cat",
        quote_mint=SOL_MINT,
        quote_symbol="SOL",
        price_usd=0.00005,
        market_cap=50_000.0,
        liquidity_usd=18_000.0,
        vol_m5=8_500.0,
        vol_h1=22_000.0,
        vol_h24=25_000.0,
        buys_m5=25,
        sells_m5=15,
        buys_h1=45,
        sells_h1=25,
        buys_h24=45,
        sells_h24=25,
        price_change_m5=12.0,
        price_change_h1=40.0,
        price_change_h24=40.0,
        pair_created_at_ms=now_ms - 10 * 60_000,
        feed_sources=("gecko_new_pools", "dex_pumpswap"),
    )
    base.update(overrides)
    return PairSnapshot(**base)


def _clean_rugcheck() -> dict:
    return {
        "rugged": False,
        "score_normalised": 12,
        "creator": "DevWallet1111111111111111111111111111111111",
        "token": {"mintAuthority": None, "freezeAuthority": None},
        "token_extensions": {},
        "risks": [],
        "markets": [{"marketType": "raydium", "lp": {"lpLockedPct": 98.5, "lpLockedUSD": 24000}}],
        "topHolders": [
            {"owner": "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1", "pct": 35.0},  # Raydium LP
            {"owner": "HolderA11111111111111111111111111111111111", "pct": 3.8},
            {"owner": "HolderB11111111111111111111111111111111111", "pct": 2.9},
            {"owner": "HolderC11111111111111111111111111111111111", "pct": 2.1},
            {"owner": "HolderD11111111111111111111111111111111111", "pct": 1.6},
            {"owner": "HolderE11111111111111111111111111111111111", "pct": 1.2},
        ],
    }


def test_hard_quote_spoof_is_vetoed_even_with_high_liquidity() -> None:
    pair = _make_pair(
        quote_mint="FakeQuoteMint1111111111111111111111111111111",
        quote_symbol="USDC",
        liquidity_usd=250_000.0,
    )
    res = evaluate_security(pair, _clean_rugcheck())
    assert not res.passed
    assert any(c.id == "hard_quote_mint" and not c.passed for c in res.checks)


def test_pareto_detects_nano_swarm_uniform_holders() -> None:
    rc = _clean_rugcheck()
    rc["topHolders"] = [
        {"owner": f"SwarmWallet{i}1111111111111111111111111111111", "pct": 2.05 - i * 0.03}
        for i in range(6)
    ]
    res = evaluate_security(_make_pair(), rc)
    assert not res.passed
    assert res.pareto_status == "NANO_SWARM_DETECTED"


def test_fresh_graduation_age_proportional_tx_threshold() -> None:
    assert compute_effective_min_tx_24h(3.0) == 18
    assert compute_effective_min_tx_24h(10.0) == 30
    assert compute_effective_min_tx_24h(25.0) == 60

    now_ms = 1_750_000_000_000
    fresh_pair = _make_pair(
        buys_h1=14,
        sells_h1=8,
        buys_h24=14,
        sells_h24=8,
        vol_h1=1500.0,
        pair_created_at_ms=now_ms - 4 * 60_000,
    )
    res = evaluate_anti_top(fresh_pair, BotConfig(), now_ms=now_ms)
    assert res.effective_min_tx_24h == 18
    assert res.passed


def test_jupiter_1sol_quote_failure_is_strictly_fail_closed() -> None:
    cfg = BotConfig()
    buy_01 = {"outAmount": "1000000", "priceImpactPct": "0.005"}
    sell_01 = {"outAmount": "98000000", "priceImpactPct": "0.006"}  # -2% loss
    # 1.0 SOL gagal / 429 -> wajib DITOLAK (tidak boleh di-clamp ke 12.5%)
    res = evaluate_execution_quotes(buy_01, sell_01, None, None, cfg)
    assert not res.passed
    assert res.depth_loss_1_sol_pct is None
    assert res.status == "DEPTH_CLIFF_FAIL"


def test_amm_lp_retention_math_and_hard_stop_loss() -> None:
    # Harga naik 4x -> di AMM x*y=k tanpa tarikan LP, USD liquidity naik 2x (sqrt(4))
    assert compute_real_lp_retention_pct(20_000, 40_000, 50_000, 200_000) == 100.0
    # Jika harga tetap tapi LP turun 30% -> LP retention = 70% (< 80% DARURAT)
    assert compute_real_lp_retention_pct(20_000, 14_000, 50_000, 50_000) == 70.0

    pos = TrackedPosition(
        id="pos1",
        mint="BzPyyYCTmQNq2BcfQ4ANAvCYk54rnmQEXMFvFT8RhWMi",
        pair_address="Pair1",
        symbol="MCAT",
        name="Moon Cat",
        entry_mc=50_000.0,
        entry_price_usd=0.00005,
        entry_liq_usd=20_000.0,
        position_usd=200.0,
        hard_stop_pct=15.0,
        tp1_pct=45.0,
        tp2_pct=100.0,
        peak_mc=50_000.0,
    )
    # Slow bleed -20% (LP normal sesuai sqrt(0.8)) -> Wajib picu HARD_STOP_LOSS
    bleed_pair = _make_pair(
        market_cap=40_000.0,
        liquidity_usd=20_000.0 * math.sqrt(40_000.0 / 50_000.0),
    )
    signal, _, pnl, lp_ret, _, _ = evaluate_position_exit(pos, bleed_pair)
    assert signal == "HARD_STOP_LOSS"
    assert pnl == -20.0
    assert lp_ret == 100.0


def test_pumpfun_bonding_curve_and_protocol_exclusion() -> None:
    assert compute_pumpfun_bonding_progress_pct(30.0) == 0.0
    assert compute_pumpfun_bonding_progress_pct(72.5) == 50.0
    assert compute_pumpfun_bonding_progress_pct(115.0) == 100.0

    payload = build_subscribe_token_trade_payload(["BzPyyYCTmQNq2BcfQ4ANAvCYk54rnmQEXMFvFT8RhWMi", "bad"])
    assert payload == {
        "method": "subscribeTokenTrade",
        "keys": ["BzPyyYCTmQNq2BcfQ4ANAvCYk54rnmQEXMFvFT8RhWMi"],
    }

    # Raydium Authority harus diabaikan dari alert whale
    ignored = parse_pumpportal_trade_event(
        {
            "mint": "BzPyyYCTmQNq2BcfQ4ANAvCYk54rnmQEXMFvFT8RhWMi",
            "traderPublicKey": "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1",
            "solAmount": 12.0,
            "txType": "buy",
        },
        sol_price_usd=180.0,
    )
    assert ignored is None


def test_sqlite_exit_guard_persistence() -> None:
    async def _run() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "test.db")
            store = ExitGuardStore(db)
            created = await store.add_position(
                mint="BzPyyYCTmQNq2BcfQ4ANAvCYk54rnmQEXMFvFT8RhWMi",
                symbol="MCAT",
                name="Moon Cat",
                entry_mc=50_000.0,
                entry_liq_usd=18_000.0,
                position_usd=250.0,
            )
            listed = await store.list_positions()
            assert len(listed) == 1
            assert listed[0].id == created.id
            assert await store.should_send_alert(created.mint, "ENTRY_READY", 30) is True
            assert await store.should_send_alert(created.mint, "ENTRY_READY", 30) is False

    asyncio.run(_run())


def test_sleeper_revival_aged_token_pre_ath_gets_full_score_and_fast_tp1() -> None:
    from bot.scoring import score_and_finalize

    now_ms = 1_750_000_000_000
    cfg = BotConfig()
    # Koin lahir 4 hari lalu, baru bangun dengan volume 5m 6x rata-rata 24h, belum dekat ATH
    aged_sleeper = _make_pair(
        market_cap=48_000.0,
        liquidity_usd=18_000.0,
        vol_m5=3_200.0,
        vol_h1=14_500.0,
        vol_h6=18_000.0,
        vol_h24=38_000.0,  # avg 5m = $131.9 -> vol_wake_5m_ratio = 24.2x, vol_h1_share = 38.2%
        buys_m5=18,
        sells_m5=9,
        buys_h1=65,
        sells_h1=40,
        buys_h24=140,
        sells_h24=110,
        price_change_m5=9.5,
        price_change_h1=24.0,
        price_change_h6=28.0,
        price_change_h24=35.0,
        pair_created_at_ms=now_ms - 4 * 24 * 60 * 60_000,  # usia 4 hari
        feed_sources=("gecko_trending_1h", "dex_boosts_top"),
    )
    sec = evaluate_security(aged_sleeper, _clean_rugcheck())
    at = evaluate_anti_top(aged_sleeper, cfg, now_ms=now_ms)
    ex = evaluate_execution_quotes(
        {"outAmount": "1000000", "priceImpactPct": "0.004"},
        {"outAmount": "98500000", "priceImpactPct": "0.005"},
        {"outAmount": "10000000", "priceImpactPct": "0.025"},
        {"outAmount": "940000000", "priceImpactPct": "0.025"},
        cfg,
    )
    audit = score_and_finalize(aged_sleeper, sec, at, ex, cfg)

    assert at.passed
    assert at.is_sleeper_revival is True
    assert at.vol_wake_5m_ratio >= 3.5
    assert audit.verdict == "ENTRY_READY"
    assert audit.pillars[4].score == 10  # Skor penuh Pilar 5 (5 revival + 5 multi-feed)
    assert audit.trade_plan is not None
    assert audit.trade_plan.tp1_pct == 30.0  # Target realistis puluhan persen sebelum ATH
    assert audit.trade_plan.tp2_pct == 65.0
    assert audit.trade_plan.hard_stop_pct == 15.0


def test_aged_token_already_near_ath_is_blocked_to_watch_pullback() -> None:
    now_ms = 1_750_000_000_000
    cfg = BotConfig()
    # Koin usia 4 hari tapi sudah naik +210% dalam 6h / +380% dalam 24h (zona ATH lokal)
    near_ath = _make_pair(
        market_cap=52_000.0,
        liquidity_usd=19_000.0,
        vol_m5=4_000.0,
        vol_h1=20_000.0,
        vol_h6=85_000.0,
        vol_h24=140_000.0,
        buys_m5=20,
        sells_m5=10,
        buys_h1=80,
        sells_h1=45,
        buys_h24=240,
        sells_h24=180,
        price_change_m5=8.0,
        price_change_h1=35.0,
        price_change_h6=210.0,
        price_change_h24=380.0,
        pair_created_at_ms=now_ms - 4 * 24 * 60 * 60_000,
    )
    at = evaluate_anti_top(near_ath, cfg, now_ms=now_ms)
    assert not at.passed
    assert at.wait_dip_only is True
    assert at.is_sleeper_revival is False
    assert any(c.id == "pre_ath_extension" and not c.passed for c in at.checks)


def test_vercel_env_auto_tmp_path_and_cron_secret_auth() -> None:
    from api.scan import is_authorized_cron_request
    from bot.storage import validate_upstash_rest_url

    old_vercel = os.environ.get("VERCEL")
    old_db = os.environ.get("DB_PATH")
    try:
        os.environ["VERCEL"] = "1"
        os.environ["DB_PATH"] = "./data/corridor_desk.db"
        vcfg = BotConfig.from_env()
        assert vcfg.is_vercel is True
        assert vcfg.db_path == "/tmp/corridor_desk.db"
    finally:
        if old_vercel is None:
            os.environ.pop("VERCEL", None)
        else:
            os.environ["VERCEL"] = old_vercel
        if old_db is None:
            os.environ.pop("DB_PATH", None)
        else:
            os.environ["DB_PATH"] = old_db

    # CRON_SECRET auth checks (fail-closed)
    assert is_authorized_cron_request("my-secret-123", "Bearer my-secret-123", "") is True
    assert is_authorized_cron_request("my-secret-123", "", "my-secret-123") is True
    assert is_authorized_cron_request("my-secret-123", "Bearer wrong", "") is False
    assert is_authorized_cron_request("", "Bearer anything", "") is False

    # Upstash URL SSRF/HTTPS validation
    assert validate_upstash_rest_url("https://happy-cat-12345.upstash.io/") == "https://happy-cat-12345.upstash.io"
    assert validate_upstash_rest_url("http://happy-cat-12345.upstash.io") is None
    assert validate_upstash_rest_url("https://127.0.0.1:8080") is None
    assert validate_upstash_rest_url("https://169.254.169.254/latest") is None



