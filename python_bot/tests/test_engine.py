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
        vol_m5=12_000.0,
        vol_h1=70_000.0,
        vol_h24=95_000.0,
        buys_m5=25,
        sells_m5=15,
        buys_h1=45,
        sells_h1=25,
        buys_h6=120,
        sells_h6=80,
        buys_h24=120,
        sells_h24=80,
        price_change_m5=12.0,
        price_change_h1=40.0,
        price_change_h24=40.0,
        pair_created_at_ms=now_ms - 45 * 60_000,
        feed_sources=("gecko_trending_5m", "dex_pumpswap"),
    )
    base.update(overrides)
    return PairSnapshot(**base)


def _clean_rugcheck() -> dict:
    return {
        "rugged": False,
        "score_normalised": 12,
        "totalHolders": 420,
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
        vol_m5=9_500.0,
        vol_h1=60_000.0,
        pair_created_at_ms=now_ms - 4 * 60_000,
    )
    # Secara default koin 4 menit ditahan survival_age >= 20m; jika min_pair_age_minutes=0, cek tx proporsional tetap 18
    res_default = evaluate_anti_top(fresh_pair, BotConfig(), now_ms=now_ms)
    assert not res_default.passed
    assert any(c.id == "survival_age" and not c.passed for c in res_default.checks)

    res_override = evaluate_anti_top(fresh_pair, BotConfig(min_pair_age_minutes=0.0), now_ms=now_ms)
    assert res_override.effective_min_tx_24h == 18
    assert res_override.passed


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
        vol_h1=16_000.0,
        vol_h6=20_000.0,
        vol_h24=42_000.0,  # avg 5m = $145.8 -> vol_wake_5m_ratio = 21.9x, vol_h1_share = 38.1%
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
        feed_sources=("gecko_trending_1h", "dex_pumpswap"),
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


def test_alert_cooldown_release_and_upstash_fail_open() -> None:
    from bot.storage import UpstashExitGuardStore

    async def _run() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ExitGuardStore(os.path.join(tmp, "cd.db"))
            mint = "CYhq3NsNoYwQN2T4tZrscyzp4NCJft76sBPFf9Shpump"
            assert await store.should_send_alert(mint, "ENTRY_READY", 30) is True
            assert await store.should_send_alert(mint, "ENTRY_READY", 30) is False
            # Jika Telegram gagal kirim -> release cooldown agar bisa dicoba lagi
            await store.release_alert_cooldown(mint, "ENTRY_READY")
            assert await store.should_send_alert(mint, "ENTRY_READY", 30) is True

        # Simulasi Upstash error/timeout -> wajib fail-open (True), sedangkan NX hit (True, None) -> False
        up = UpstashExitGuardStore("https://demo-redis.upstash.io", "token-xyz")

        async def _fake_err(*_args):
            return (False, None)

        async def _fake_exists(*_args):
            return (True, None)

        async def _fake_ok(*_args):
            return (True, "OK")

        up._cmd_with_status = _fake_err  # type: ignore[method-assign]
        assert await up.should_send_alert("mintA", "ENTRY_READY", 30) is True

        up._cmd_with_status = _fake_exists  # type: ignore[method-assign]
        assert await up.should_send_alert("mintA", "ENTRY_READY", 30) is False

        up._cmd_with_status = _fake_ok  # type: ignore[method-assign]
        assert await up.should_send_alert("mintA", "ENTRY_READY", 30) is True

    asyncio.run(_run())


def test_pumpswap_dynamic_amm_vault_excluded_via_known_accounts_and_markets() -> None:
    """Reproduksi koin Holders: vault Pump Fun AMM (14.77% supply) tidak boleh diveto sebagai Whale."""
    rc = _clean_rugcheck()
    rc["totalHolders"] = 16_886
    rc["markets"] = [
        {
            "marketType": "pump_fun_amm",
            "pubkey": "LBgqGiqxPumpAmmPoolVault11111111111111111111",
            "lp": {"lpLockedPct": 100.0, "lpLockedUSD": 20700},
        }
    ]
    rc["knownAccounts"] = {
        "LBgqGiqxPumpAmmPoolVault11111111111111111111": {"name": "Pump Fun AMM", "type": "AMM"},
    }
    rc["topHolders"] = [
        {"owner": "LBgqGiqxPumpAmmPoolVault11111111111111111111", "pct": 14.77},
        {"owner": "HolderA11111111111111111111111111111111111", "pct": 4.10},
        {"owner": "HolderB11111111111111111111111111111111111", "pct": 3.05},
        {"owner": "HolderC11111111111111111111111111111111111", "pct": 2.20},
        {"owner": "HolderD11111111111111111111111111111111111", "pct": 1.45},
    ]
    pair = _make_pair(market_cap=74_000.0, liquidity_usd=20_700.0)
    res = evaluate_security(pair, rc)
    assert res.passed, f"Expected PumpSwap AMM vault to be excluded, got: {[c.detail for c in res.checks if not c.passed]}"
    assert res.top1_pct == 4.1
    assert res.total_holders == 16_886


def test_dead_coin_low_5m_velocity_is_hard_vetoed() -> None:
    """Reproduksi koin mati (DUVAL): Vol5m $549 dan tren -6.6% 1h wajib kena HARD VETO."""
    now_ms = 1_750_000_000_000
    dead_pair = _make_pair(
        market_cap=58_000.0,
        liquidity_usd=19_000.0,
        vol_m5=549.0,
        vol_h1=4_200.0,
        vol_h24=18_000.0,
        buys_m5=4,
        sells_m5=3,
        price_change_m5=-2.1,
        price_change_h1=-6.6,
        pair_created_at_ms=now_ms - 45 * 60_000,
    )
    res = evaluate_anti_top(dead_pair, BotConfig(), now_ms=now_ms)
    assert not res.passed
    assert not res.wait_dip_only  # Harus HARD VETO, bukan WATCH_PULLBACK
    assert any(c.id == "orderflow_quality" and not c.passed and "lemah" in c.detail for c in res.checks)


def test_5m_wash_bot_tiny_ticket_is_hard_vetoed() -> None:
    """Reproduksi wash-bot (DONJENSEN): 885 tx dalam 5 menit tapi volume cuma $1,901 ($2.15/tx) wajib VETO."""
    now_ms = 1_750_000_000_000
    wash_pair = _make_pair(
        market_cap=65_000.0,
        liquidity_usd=21_000.0,
        vol_m5=1_901.0,
        vol_h1=28_000.0,
        vol_h24=52_000.0,
        buys_m5=450,
        sells_m5=435,
        price_change_m5=4.0,
        price_change_h1=15.0,
        pair_created_at_ms=now_ms - 50 * 60_000,
    )
    res = evaluate_anti_top(wash_pair, BotConfig(), now_ms=now_ms)
    assert not res.passed
    assert not res.wait_dip_only
    assert res.avg_ticket_5m_usd < 18.0
    assert any(c.id == "orderflow_quality" and not c.passed and "wash-bot" in c.detail for c in res.checks)


def test_whale_absorption_rising_price_passes_anti_top() -> None:
    """Reproduksi koin 4CHAN/PERRO: 104B/119S (46.6% buy count) tapi harga +8.2% 5m & Vol5m besar = Whale Absorption."""
    now_ms = 1_750_000_000_000
    whale_pair = _make_pair(
        market_cap=266_000.0,
        liquidity_usd=39_000.0,
        vol_m5=51_000.0,
        vol_h1=420_000.0,
        vol_h24=1_200_000.0,
        buys_m5=104,
        sells_m5=119,  # 46.6% buy tx count (banyak retail TP kecil, whale makan beli besar)
        buys_h1=2400,
        sells_h1=2111,  # 53.2% buy tx count 1h
        buys_h24=5200,
        sells_h24=4800,
        price_change_m5=8.2,
        price_change_h1=185.0,
        price_change_h6=190.0,
        price_change_h24=210.0,
        pair_created_at_ms=now_ms - 3 * 60 * 60_000,
        feed_sources=("gecko_trending_5m", "dex_pumpswap"),
    )
    res = evaluate_anti_top(whale_pair, BotConfig(), now_ms=now_ms)
    assert res.passed, f"Expected Whale Absorption runner to pass, got: {[c.detail for c in res.checks if not c.passed]}"
    assert res.whale_absorption is True
    assert res.corridor_id == "CORRIDOR_C_EXPANSION"


def test_fresh_overextended_coin_and_dying_volume_are_blocked() -> None:
    """Koin baru (35 menit) yg sudah naik +165% 1h atau volumenya sekarat (accel < 0.55x) wajib diblokir."""
    now_ms = 1_750_000_000_000
    cfg = BotConfig()

    # 1. Koin muda 35m non-viral (Vol5m $8.5K, Vol1h $60K) sudah +165% 1h -> melampaui batas fresh +115% -> WAIT_DIP!
    overextended_fresh = _make_pair(
        market_cap=88_000.0,
        liquidity_usd=22_000.0,
        vol_m5=8_500.0,
        vol_h1=60_000.0,
        price_change_m5=6.0,
        price_change_h1=165.0,
        pair_created_at_ms=now_ms - 35 * 60_000,
    )
    res_peak = evaluate_anti_top(overextended_fresh, cfg, now_ms=now_ms)
    assert not res_peak.passed
    assert any(c.id == "parabolic_cap" and not c.passed for c in res_peak.checks)

    # 2. Koin usia 50m yg Vol1h $180K ($15K/5m) tapi Vol5m anjlok ke $8,100 (accel 0.54x & < $12K) -> HARD VETO!
    dying_vol = _make_pair(
        market_cap=68_000.0,
        liquidity_usd=20_000.0,
        vol_m5=8_100.0,
        vol_h1=180_000.0,
        price_change_m5=2.0,
        price_change_h1=45.0,
        pair_created_at_ms=now_ms - 50 * 60_000,
    )
    res_dying = evaluate_anti_top(dying_vol, cfg, now_ms=now_ms)
    assert not res_dying.passed
    assert not res_dying.wait_dip_only
    assert res_dying.vol_m5_accel_ratio < 0.55
    assert any(c.id == "orderflow_quality" and not c.passed and "sekarat" in c.detail for c in res_dying.checks)


def test_ohlcv_blowoff_top_and_volume_exhaustion_are_hard_vetoed() -> None:
    """Layer 2B OHLCV 5m: jarum pucuk blow-off (wick >= 48% + DD >= 10%) dan bensin habis wajib HARD VETO."""
    from bot.structure import evaluate_candle_structure

    now_ms = 1_750_000_000_000
    cfg = BotConfig()
    pair = _make_pair(
        market_cap=110_000.0,
        liquidity_usd=26_000.0,
        vol_m5=9_000.0,
        vol_h1=95_000.0,
        price_change_m5=5.0,
        price_change_h1=92.0,
        pair_created_at_ms=now_ms - 55 * 60_000,
    )
    at = evaluate_anti_top(pair, cfg, now_ms=now_ms)
    assert at.passed

    # Candle 5m [ts, O, H, L, C, V]: lonjakan besar lalu wick atas panjang (High 0.0020, Close 0.00145) & vol anjlok
    blowoff_candles = [
        [1, 0.0008, 0.0010, 0.00078, 0.00098, 45_000],
        [2, 0.00098, 0.00135, 0.00095, 0.00130, 80_000],
        [3, 0.00130, 0.00190, 0.00128, 0.00178, 120_000],  # peak candle vol
        [4, 0.00178, 0.00205, 0.00150, 0.00162, 28_000],   # wick atas 51% + DD 21% + vol collapse!
        [5, 0.00162, 0.00172, 0.00152, 0.00158, 18_000],
    ]
    st = evaluate_candle_structure(pair, at, blowoff_candles, [], cfg)
    assert not st.passed
    assert not st.wait_dip_only  # HARD VETO!
    assert st.regime == "EXHAUSTION_TOP"


def test_daily_consolidation_distinguishes_bullish_accumulation_vs_bearish_distribution() -> None:
    """Layer 2B Daily Classifier: wajib meloloskan konsolidasi AKUMULASI (naik) & menolak DISTRIBUSI (turun)."""
    from bot.scoring import score_and_finalize
    from bot.structure import evaluate_candle_structure

    now_ms = 1_750_000_000_000
    cfg = BotConfig()

    # --- KASUS A: KOIN HARIAN (3 HARI) KONSOLIDASI AKUMULASI (AKAN NAIK) ---
    # Box ketat ~22%, Higher Lows (low naik dari 0.00100 -> 0.00108), volume hijau dominan, wick atas kecil,
    # candle terakhir breakout di posisi ~104% box dengan ekspansi volume 2.8x!
    bullish_1h = [
        [1, 0.00105, 0.00116, 0.00100, 0.00104, 4_200],
        [2, 0.00104, 0.00114, 0.00101, 0.00110, 5_100],
        [3, 0.00110, 0.00118, 0.00104, 0.00107, 3_600],
        [4, 0.00107, 0.00115, 0.00103, 0.00112, 4_800],
        [5, 0.00112, 0.00120, 0.00108, 0.00116, 5_500],  # paruh ke-2: Higher Low (0.00108 > 0.00100)
        [6, 0.00116, 0.00121, 0.00110, 0.00114, 3_200],  # koreksi ringan volume kecil
        [7, 0.00114, 0.00121, 0.00111, 0.00119, 6_400],  # akumulasi hijau
        [8, 0.00119, 0.00122, 0.00114, 0.00120, 6_800],
        [9, 0.00120, 0.00125, 0.00118, 0.00123, 16_500],  # BREAKOUT bersih di atas box (pos ~104%, vol 3.4x)
    ]
    bull_pair = _make_pair(
        market_cap=115_000.0,
        liquidity_usd=28_000.0,
        vol_m5=4_500.0,
        vol_h1=32_000.0,
        vol_h6=62_000.0,
        vol_h24=105_000.0,
        buys_m5=28,
        sells_m5=15,
        buys_h1=130,
        sells_h1=82,
        buys_h6=320,
        sells_h6=220,  # 59.3% buy 6h -> akumulasi nyata!
        buys_h24=720,
        sells_h24=580,
        price_change_m5=5.5,
        price_change_h1=14.0,
        price_change_h6=18.0,
        price_change_h24=24.0,
        pair_created_at_ms=now_ms - 3 * 24 * 60 * 60_000,
        feed_sources=("gecko_trending_6h", "dex_pumpswap"),
    )
    sec_b = evaluate_security(bull_pair, _clean_rugcheck())
    at_b = evaluate_anti_top(bull_pair, cfg, now_ms=now_ms)
    st_b = evaluate_candle_structure(bull_pair, at_b, [], bullish_1h, cfg)
    ex_b = evaluate_execution_quotes(
        {"outAmount": "1000000", "priceImpactPct": "0.004"},
        {"outAmount": "98600000", "priceImpactPct": "0.005"},
        {"outAmount": "10000000", "priceImpactPct": "0.020"},
        {"outAmount": "945000000", "priceImpactPct": "0.022"},
        cfg,
    )
    audit_b = score_and_finalize(bull_pair, sec_b, at_b, ex_b, cfg, structure=st_b)
    assert at_b.passed, f"Bullish daily anti_top failed: {[c.detail for c in at_b.checks if not c.passed]}"
    assert st_b.passed, f"Bullish daily structure failed: {[c.detail for c in st_b.checks if not c.passed]}"
    assert st_b.regime == "BULLISH_CONSOLIDATION_BREAKOUT"
    assert audit_b.verdict == "ENTRY_READY"
    assert audit_b.total_score >= cfg.min_score

    # --- KASUS B: KOIN HARIAN (3 HARI) KONSOLIDASI DISTRIBUSI (AKAN TURUN) ---
    # Lower Highs, wick atas panjang (> 45%), volume merah besar, harga duduk di bawah box (pos ~20%) -> HARD VETO!
    bearish_1h = [
        [1, 0.00125, 0.00145, 0.00120, 0.00126, 9_500],   # wick atas panjang
        [2, 0.00126, 0.00142, 0.00118, 0.00121, 12_000],  # merah volume besar
        [3, 0.00121, 0.00136, 0.00117, 0.00119, 11_000],  # lower high + merah
        [4, 0.00119, 0.00130, 0.00115, 0.00118, 8_500],
        [5, 0.00118, 0.00126, 0.00113, 0.00114, 13_000],  # lower high + merah berat
        [6, 0.00114, 0.00125, 0.00112, 0.00113, 14_000],  # wick atas panjang
        [7, 0.00113, 0.00123, 0.00111, 0.00112, 12_500],  # wick atas panjang + merah
        [8, 0.00112, 0.00121, 0.00110, 0.00111, 13_500],  # distribusi terus
        [9, 0.00111, 0.00122, 0.00110, 0.00114, 7_500],   # dead-cat bounce kecil di bawah box (wick 67%!)
    ]
    bear_pair = _make_pair(
        market_cap=110_000.0,
        liquidity_usd=27_000.0,
        vol_m5=3_500.0,
        vol_h1=28_000.0,
        vol_h6=85_000.0,
        vol_h24=180_000.0,
        buys_m5=20,
        sells_m5=15,
        buys_h1=90,
        sells_h1=88,
        buys_h6=210,
        sells_h6=270,  # 43.8% buy 6h -> distribusi turun!
        buys_h24=600,
        sells_h24=650,
        price_change_m5=2.5,
        price_change_h1=1.0,
        price_change_h6=-8.0,
        price_change_h24=-14.0,
        pair_created_at_ms=now_ms - 3 * 24 * 60 * 60_000,
    )
    at_bear = evaluate_anti_top(bear_pair, cfg, now_ms=now_ms)
    st_bear = evaluate_candle_structure(bear_pair, at_bear, [], bearish_1h, cfg)
    assert not st_bear.passed
    assert not st_bear.wait_dip_only  # Wajib HARD VETO (bukan WAIT_DIP!)
    assert st_bear.regime == "BEARISH_DISTRIBUTION"
    assert not at_bear.passed  # Juga kena veto 6h sell bleed di Layer 2A!


def test_zero_replay_passes_all_three_golden_entries_with_smart_viral_pareto() -> None:
    """Replay forensik $Zero (DTSkmo3s...pump) pada 3 titik Golden Entry sebelum terbang ke $1.30M!"""
    from bot.scoring import score_and_finalize
    from bot.structure import evaluate_candle_structure
    from bot.telegram_notifier import format_entry_alert

    now_ms = 1_750_000_000_000
    cfg = BotConfig()
    exec_ok = evaluate_execution_quotes(
        {"outAmount": "1000000", "priceImpactPct": "0.003"},
        {"outAmount": "98800000", "priceImpactPct": "0.004"},
        {"outAmount": "10000000", "priceImpactPct": "0.015"},
        {"outAmount": "955000000", "priceImpactPct": "0.018"},
        cfg,
    )

    def _zero_rc(holders: int) -> dict:
        return {
            "rugged": False,
            "score_normalised": 1,
            "totalHolders": holders,
            "creator": "DevZero1111111111111111111111111111111111111",
            "token": {"mintAuthority": None, "freezeAuthority": None},
            "token_extensions": {},
            "risks": [],
            "markets": [
                {
                    "marketType": "pump_fun_amm",
                    "pubkey": "PumpAmmZero11111111111111111111111111111111",
                    "lp": {"lpLockedPct": 100.0, "lpLockedUSD": 76_748, "baseUSD": 38_000, "quoteUSD": 38_000},
                },
                {
                    "marketType": "meteoraDlmm",
                    "pubkey": "MetZeroA11111111111111111111111111111111111",
                    "lp": {"lpLockedPct": 0.0, "lpLockedUSD": 0, "baseUSD": 14_000, "quoteUSD": 14_000},
                },
            ],
            "knownAccounts": {
                "PumpAmmZero11111111111111111111111111111111": {"name": "Pump Fun AMM", "type": "AMM"},
            },
            "topHolders": [
                {"owner": "Czrp1c6Whale1111111111111111111111111111111", "pct": 10.30, "insider": False},
                {"owner": "PumpAmmZero11111111111111111111111111111111", "pct": 4.71, "insider": False},
                {"owner": "HolderZ311111111111111111111111111111111111", "pct": 3.35, "insider": False},
                {"owner": "HolderZ411111111111111111111111111111111111", "pct": 1.78, "insider": False},
                {"owner": "HolderZ511111111111111111111111111111111111", "pct": 1.40, "insider": False},
                {"owner": "HolderZ611111111111111111111111111111111111", "pct": 1.25, "insider": False},
            ],
        }

    # --- GOLDEN ENTRY #1: $244K (age 20m, 1h +740%, pause sehat 5m +0.3%, Vol5m $110K) ---
    zero_e1 = _make_pair(
        mint="DTSkmo3sCCKLCh91dJLiWCpwcKtK3yDBZMqZGSV1pump",
        symbol="Zero",
        name="ZeroSumEnjoyer",
        market_cap=244_000.0,
        liquidity_usd=42_000.0,
        vol_m5=110_000.0,
        vol_h1=480_000.0,
        vol_h24=480_000.0,
        buys_m5=320,
        sells_m5=290,
        buys_h1=1_800,
        sells_h1=1_400,
        buys_h24=1_800,
        sells_h24=1_400,
        price_change_m5=0.3,
        price_change_h1=740.0,
        price_change_h24=740.0,
        pair_created_at_ms=now_ms - 20 * 60_000,
        feed_sources=("gecko_trending_5m", "gecko_trending_1h", "dex_pumpswap"),
        pool_count=3,
        is_boosted=False,
    )
    candles_e1 = [
        [1, 0.000029, 0.000055, 0.000029, 0.000049, 62_000],
        [2, 0.000049, 0.000072, 0.000046, 0.000057, 106_000],
        [3, 0.000057, 0.000175, 0.000056, 0.000168, 131_000],
        [4, 0.000168, 0.000255, 0.000165, 0.000243, 173_000],
        [5, 0.000243, 0.000265, 0.000215, 0.000244, 110_000],  # Pause sehat, upWick ~23%
    ]
    sec1 = evaluate_security(zero_e1, _zero_rc(1_450), now_ms=now_ms)
    at1 = evaluate_anti_top(zero_e1, cfg, now_ms=now_ms)
    st1 = evaluate_candle_structure(zero_e1, at1, candles_e1, [], cfg)
    audit1 = score_and_finalize(zero_e1, sec1, at1, exec_ok, cfg, structure=st1)
    assert sec1.passed and sec1.clean_viral_pareto and sec1.pareto_status == "VIRAL_PARETO_CLEAN"
    assert at1.passed and at1.is_viral_velocity
    assert st1.passed
    assert audit1.verdict == "ENTRY_READY"
    assert audit1.total_score >= 82

    # --- GOLDEN ENTRY #2: $267K (age 45m, pullback -40% dr $443K, Vol5m $50K, accel 0.43x, reclaim hijau!) ---
    zero_e2 = _make_pair(
        mint="DTSkmo3sCCKLCh91dJLiWCpwcKtK3yDBZMqZGSV1pump",
        symbol="Zero",
        name="ZeroSumEnjoyer",
        market_cap=267_000.0,
        liquidity_usd=45_000.0,
        vol_m5=50_000.0,
        vol_h1=1_400_000.0,  # avg 5m = $116.7K -> accel 0.43x (kontraksi pullback sehat!)
        vol_h24=1_400_000.0,
        buys_m5=210,
        sells_m5=180,
        buys_h1=4_200,
        sells_h1=3_600,
        buys_h24=4_200,
        sells_h24=3_600,
        price_change_m5=6.8,
        price_change_h1=820.0,
        price_change_h24=820.0,
        pair_created_at_ms=now_ms - 45 * 60_000,
        feed_sources=("gecko_trending_5m", "gecko_trending_1h", "gecko_trending_6h", "dex_pumpswap"),
        pool_count=4,
        is_boosted=False,
    )
    candles_e2 = [
        [1, 0.000168, 0.000255, 0.000165, 0.000244, 173_000],
        [2, 0.000244, 0.000380, 0.000235, 0.000360, 195_000],
        [3, 0.000360, 0.000443, 0.000340, 0.000410, 220_000],  # Peak $443K
        [4, 0.000410, 0.000415, 0.000310, 0.000320, 115_000],  # Pullback leg 1
        [5, 0.000320, 0.000325, 0.000248, 0.000250, 62_000],   # Higher Low di $248K (> $165K)
        [6, 0.000250, 0.000270, 0.000249, 0.000267, 50_000],   # Reclaim hijau (upWick ~14%)!
    ]
    sec2 = evaluate_security(zero_e2, _zero_rc(2_800), now_ms=now_ms)
    at2 = evaluate_anti_top(zero_e2, cfg, now_ms=now_ms)
    st2 = evaluate_candle_structure(zero_e2, at2, candles_e2, [], cfg)
    audit2 = score_and_finalize(zero_e2, sec2, at2, exec_ok, cfg, structure=st2)
    assert at2.passed and at2.is_pullback_contraction_ok and at2.vol_m5_accel_ratio < 0.55
    assert st2.passed and st2.regime == "PULLBACK_RECLAIM"
    assert audit2.verdict == "ENTRY_READY"
    alert_text = format_entry_alert(audit2)
    assert "5 Bukti Bahan Bakar Kenaikan" in alert_text
    assert "4 pool aktif" in alert_text

    # --- GOLDEN ENTRY #3: $580K Corridor D Viral Base (age 95m, Vol1h $520K, Liq $62K, Holders 4,800) ---
    zero_e3 = _make_pair(
        mint="DTSkmo3sCCKLCh91dJLiWCpwcKtK3yDBZMqZGSV1pump",
        symbol="Zero",
        name="ZeroSumEnjoyer",
        market_cap=580_000.0,
        liquidity_usd=62_000.0,
        vol_m5=42_000.0,
        vol_h1=520_000.0,
        vol_h24=2_400_000.0,
        buys_m5=185,
        sells_m5=145,
        buys_h1=2_100,
        sells_h1=1_800,
        buys_h24=7_200,
        sells_h24=6_100,
        price_change_m5=3.5,
        price_change_h1=140.0,
        price_change_h24=1_900.0,
        pair_created_at_ms=now_ms - 95 * 60_000,
        feed_sources=("gecko_trending_5m", "gecko_trending_1h", "gecko_trending_6h", "dex_pumpswap"),
        pool_count=4,
        is_boosted=False,
    )
    candles_e3 = [
        [1, 0.000540, 0.000575, 0.000530, 0.000560, 48_000],
        [2, 0.000560, 0.000585, 0.000548, 0.000558, 39_000],
        [3, 0.000558, 0.000580, 0.000552, 0.000565, 36_000],
        [4, 0.000565, 0.000590, 0.000560, 0.000580, 42_000],
    ]
    sec3 = evaluate_security(zero_e3, _zero_rc(4_800), now_ms=now_ms)
    at3 = evaluate_anti_top(zero_e3, cfg, now_ms=now_ms)
    st3 = evaluate_candle_structure(zero_e3, at3, candles_e3, [], cfg)
    audit3 = score_and_finalize(zero_e3, sec3, at3, exec_ok, cfg, structure=st3)
    assert sec3.passed
    assert at3.passed and at3.corridor_id == "CORRIDOR_D_VIRAL_BASE" and at3.is_corridor_d_viral_base
    assert st3.passed
    assert audit3.verdict == "ENTRY_READY"


def test_weak_mediocre_launch_and_unqualified_corridor_d_are_hard_vetoed() -> None:
    """v4: Koin lemah ($3.2K/5m, 120H), koin $540K non-viral, & Top1=10.3% berisiko wajib HARD VETO!"""
    now_ms = 1_750_000_000_000
    cfg = BotConfig()

    # 1. Koin lemah/sepi yg dulu lolos v3 -> wajib HARD VETO di Layer 1 & Layer 2A v4!
    weak_pair = _make_pair(
        market_cap=65_000.0,
        liquidity_usd=18_000.0,
        vol_m5=3_200.0,
        vol_h1=22_000.0,
        vol_h24=26_000.0,
        price_change_m5=5.0,
        price_change_h1=45.0,
        pair_created_at_ms=now_ms - 35 * 60_000,
    )
    weak_rc = _clean_rugcheck()
    weak_rc["totalHolders"] = 120
    sec_w = evaluate_security(weak_pair, weak_rc, now_ms=now_ms)
    at_w = evaluate_anti_top(weak_pair, cfg, now_ms=now_ms)
    assert not sec_w.passed  # Holders 120 < 220!
    assert not at_w.passed and not at_w.wait_dip_only  # Vol5m $3.2K < $8K & Vol1h $22K < $55K -> HARD VETO!

    # 2. Koin $540K biasa tanpa volume viral base -> wajib HARD VETO (tidak boleh masuk Corridor D)
    fake_d = _make_pair(
        market_cap=540_000.0,
        liquidity_usd=28_000.0,
        vol_m5=9_000.0,
        vol_h1=95_000.0,
        vol_h24=240_000.0,
        price_change_m5=4.0,
        price_change_h1=30.0,
        pair_created_at_ms=now_ms - 180 * 60_000,
    )
    at_d = evaluate_anti_top(fake_d, cfg, now_ms=now_ms)
    assert not at_d.passed and not at_d.wait_dip_only and not at_d.is_corridor_d_viral_base

    # 3. Top1 = 10.3% pada koin berisiko (RC score=20 / holders=350) -> TIDAK boleh dapat Smart Viral Pareto!
    risky_rc = _clean_rugcheck()
    risky_rc["score_normalised"] = 20
    risky_rc["totalHolders"] = 350
    risky_rc["topHolders"] = [
        {"owner": "WhaleBad11111111111111111111111111111111111", "pct": 10.30},
        {"owner": "HolderB11111111111111111111111111111111111", "pct": 3.00},
        {"owner": "HolderC11111111111111111111111111111111111", "pct": 2.10},
    ]
    sec_r = evaluate_security(_make_pair(), risky_rc, now_ms=now_ms)
    assert not sec_r.passed
    assert not sec_r.clean_viral_pareto
    assert sec_r.pareto_status == "WHALE_CONCENTRATED"


def test_pair_hyper_viral_green_absorption_and_fasttrack_passes_v41() -> None:
    """Replay forensik PAIR (32rBxcFj...pump): lolos di menit 17 (Fast-Track + 1m OHLCV) & menit 23 (Green Viral Absorption wick 59%)."""
    from bot.scoring import score_and_finalize
    from bot.structure import aggregate_1m_to_3m_candles, evaluate_candle_structure

    now_ms = 1_750_000_000_000
    cfg = BotConfig()
    exec_ok = evaluate_execution_quotes(
        {"outAmount": "1000000", "priceImpactPct": "0.003"},
        {"outAmount": "98800000", "priceImpactPct": "0.004"},
        {"outAmount": "10000000", "priceImpactPct": "0.015"},
        {"outAmount": "955000000", "priceImpactPct": "0.018"},
        cfg,
    )
    pair_rc = _clean_rugcheck()
    pair_rc["score_normalised"] = 1
    pair_rc["totalHolders"] = 1_972
    pair_rc["markets"] = [
        {
            "marketType": "pump_fun_amm",
            "pubkey": "qkiYfkcm2QGfpNbEpuXvA5wiXq4F6ZdUWnZk2Y19cym",
            "lp": {"lpLockedPct": 100.0, "lpLockedUSD": 26_786, "baseUSD": 13_400, "quoteUSD": 13_400},
        }
    ]
    pair_rc["topHolders"] = [
        {"owner": "qkiYfkcm2QGfpNbEpuXvA5wiXq4F6ZdUWnZk2Y19cym", "pct": 9.99},
        {"owner": "HolderP1111111111111111111111111111111111111", "pct": 4.37},
        {"owner": "HolderP2111111111111111111111111111111111111", "pct": 2.40},
        {"owner": "HolderP3111111111111111111111111111111111111", "pct": 2.36},
        {"owner": "HolderP4111111111111111111111111111111111111", "pct": 2.27},
        {"owner": "HolderP5111111111111111111111111111111111111", "pct": 1.96},
    ]

    # --- KASUS A: PAIR pada menit ke-23 (5 candle 5m HIJAU beruntun, wick 55-59%, GreenVol 85%, Vol5m $72.9K) ---
    pair_23m = _make_pair(
        mint="32rBxcFjCFnyqZ41CEPM5AL67X1dcw8w83vZt6Kxpump",
        pair_address="qkiYfkcm2QGfpNbEpuXvA5wiXq4F6ZdUWnZk2Y19cym",
        symbol="PAIR",
        name="Paircoin",
        market_cap=128_731.0,
        liquidity_usd=25_984.0,
        vol_m5=72_920.0,
        vol_h1=363_250.0,
        vol_h24=363_250.0,
        buys_m5=1_060,
        sells_m5=800,
        buys_h1=5_543,
        sells_h1=4_215,
        buys_h24=5_543,
        sells_h24=4_215,
        price_change_m5=-0.8,
        price_change_h1=351.0,
        price_change_h24=351.0,
        pair_created_at_ms=now_ms - 23 * 60_000,
        feed_sources=("gecko_trending_5m", "dex_token_profiles"),
    )
    pair_candles_5m = [
        [1, 0.0000282, 0.0000528, 0.0000173, 0.0000493, 48_826],
        [2, 0.0000493, 0.0001135, 0.0000450, 0.0000682, 102_114],
        [3, 0.0000682, 0.0001293, 0.0000582, 0.0001125, 77_341],
        [4, 0.0001125, 0.0001608, 0.0000994, 0.0001265, 142_239],  # HIJAU +12.5%, wick 56%
        [5, 0.0001265, 0.0001549, 0.0001131, 0.0001326, 28_318],   # HIJAU +4.8%, wick 53%
    ]
    sec23 = evaluate_security(pair_23m, pair_rc, now_ms=now_ms)
    at23 = evaluate_anti_top(pair_23m, cfg, now_ms=now_ms)
    st23 = evaluate_candle_structure(pair_23m, at23, pair_candles_5m, [], cfg)
    audit23 = score_and_finalize(pair_23m, sec23, at23, exec_ok, cfg, structure=st23)
    assert sec23.passed
    assert at23.passed and at23.is_viral_velocity
    assert st23.passed, f"PAIR 23m green viral absorption should pass, got: {[c.detail for c in st23.checks if not c.passed]}"
    assert audit23.verdict == "ENTRY_READY"

    # --- KASUS B: PAIR pada menit ke-15 (Hyper-Viral Fast-Track 12-19m + agregasi candle 1m->3m) ---
    pair_15m = _make_pair(
        mint="32rBxcFjCFnyqZ41CEPM5AL67X1dcw8w83vZt6Kxpump",
        pair_address="qkiYfkcm2QGfpNbEpuXvA5wiXq4F6ZdUWnZk2Y19cym",
        symbol="PAIR",
        name="Paircoin",
        market_cap=115_000.0,
        liquidity_usd=24_500.0,
        vol_m5=68_000.0,
        vol_h1=240_000.0,
        vol_h24=240_000.0,
        buys_m5=950,
        sells_m5=720,
        buys_h1=3_800,
        sells_h1=2_900,
        buys_h24=3_800,
        sells_h24=2_900,
        price_change_m5=12.0,
        price_change_h1=310.0,
        price_change_h24=310.0,
        pair_created_at_ms=now_ms - 15 * 60_000,
        feed_sources=("gecko_trending_5m", "dex_token_profiles"),
    )
    candles_1m = [
        [i, 0.000023 + i * 0.000006, 0.000026 + i * 0.0000065, 0.000022 + i * 0.0000058, 0.000025 + i * 0.0000062, 16_000]
        for i in range(15)
    ]
    synth_3m = aggregate_1m_to_3m_candles(candles_1m)
    assert len(synth_3m) == 5

    at15 = evaluate_anti_top(pair_15m, cfg, now_ms=now_ms)
    sec15 = evaluate_security(pair_15m, pair_rc, now_ms=now_ms, require_hyper_viral_holders=at15.is_hyper_viral_fasttrack)
    st15 = evaluate_candle_structure(pair_15m, at15, synth_3m, [], cfg)
    audit15 = score_and_finalize(pair_15m, sec15, at15, exec_ok, cfg, structure=st15)
    assert at15.passed and at15.is_hyper_viral_fasttrack
    assert sec15.passed
    assert st15.passed
    assert audit15.verdict == "ENTRY_READY"

    # Pastikan koin < 20m yang holder sedikit (300H) DITOLAK di Layer 1 meski lolos Fast-Track L2A
    low_h_rc = dict(pair_rc)
    low_h_rc["totalHolders"] = 300
    sec_low = evaluate_security(pair_15m, low_h_rc, now_ms=now_ms, require_hyper_viral_holders=True)
    assert not sec_low.passed


def test_duk_steady_organic_climber_and_partial_candle_fix_passes_v41() -> None:
    """Replay forensik duk (DG2EJSEt...pump): Steady Organic Climber ($6.9K/5m, $109K/1h) & partial candle terakhir tidak salah VETO."""
    from bot.scoring import score_and_finalize
    from bot.structure import evaluate_candle_structure

    now_ms = 1_750_000_000_000
    cfg = BotConfig()
    exec_ok = evaluate_execution_quotes(
        {"outAmount": "1000000", "priceImpactPct": "0.004"},
        {"outAmount": "98600000", "priceImpactPct": "0.005"},
        {"outAmount": "10000000", "priceImpactPct": "0.018"},
        {"outAmount": "950000000", "priceImpactPct": "0.020"},
        cfg,
    )
    duk_pair = _make_pair(
        mint="DG2EJSEtPBge5F7zBCTZG78p27LTCiq8tUkJ39qupump",
        pair_address="4XVReVDHQDFUM6FfvTmo7FdmEq7ZGWxZ1Jhj7qVA68wx",
        symbol="duk",
        name="duk",
        market_cap=119_887.0,
        liquidity_usd=27_151.0,
        vol_m5=6_982.0,
        vol_h1=109_288.0,
        vol_h6=199_026.0,
        vol_h24=199_026.0,
        buys_m5=76,
        sells_m5=58,
        buys_h1=1_620,
        sells_h1=1_151,
        buys_h6=2_200,
        sells_h6=1_580,
        buys_h24=2_200,
        sells_h24=1_580,
        price_change_m5=8.0,
        price_change_h1=156.0,
        price_change_h6=326.0,
        price_change_h24=326.0,
        pair_created_at_ms=now_ms - 220 * 60_000,
        feed_sources=("watchlist_rescan", "gecko_trending_5m_p2"),
    )
    duk_rc = _clean_rugcheck()
    duk_rc["score_normalised"] = 1
    duk_rc["totalHolders"] = 1_281
    duk_rc["markets"] = [
        {
            "marketType": "pump_fun_amm",
            "pubkey": "4XVReVDHQDFUM6FfvTmo7FdmEq7ZGWxZ1Jhj7qVA68wx",
            "lp": {"lpLockedPct": 100.0, "lpLockedUSD": 26_685},
        }
    ]
    # Data candle 5m asli duk: candle terakhir baru jalan 2 menit (Vol=$1,871 << pair.vol_m5 $6,982)!
    duk_candles_5m = [
        [1, 0.0000390, 0.0000579, 0.0000324, 0.0000556, 7_871],
        [2, 0.0000556, 0.0001008, 0.0000487, 0.0000932, 13_246],
        [3, 0.0000932, 0.0001195, 0.0000783, 0.0001010, 26_975],
        [4, 0.0001010, 0.0001116, 0.0000896, 0.0000974, 15_528],
        [5, 0.0000974, 0.0001184, 0.0000833, 0.0000888, 11_092],
        [6, 0.0000888, 0.0000925, 0.0000775, 0.0000816, 7_226],
        [7, 0.0000816, 0.0000907, 0.0000765, 0.0000778, 5_478],
        [8, 0.0000778, 0.0000978, 0.0000776, 0.0000917, 2_971],
        [9, 0.0000917, 0.0001146, 0.0000903, 0.0001115, 3_693],
        [10, 0.0001115, 0.0001231, 0.0001100, 0.0001205, 1_871],  # Partial candle 2 menit!
    ]
    at_d = evaluate_anti_top(duk_pair, cfg, now_ms=now_ms)
    sec_d = evaluate_security(duk_pair, duk_rc, now_ms=now_ms, require_steady_climber_holders=at_d.is_steady_organic_climber)
    st_d = evaluate_candle_structure(duk_pair, at_d, duk_candles_5m, [], cfg)
    audit_d = score_and_finalize(duk_pair, sec_d, at_d, exec_ok, cfg, structure=st_d)

    assert at_d.passed and at_d.is_steady_organic_climber, f"duk L2A failed: {[c.detail for c in at_d.checks if not c.passed]}"
    assert sec_d.passed
    assert st_d.passed, f"duk L2B failed (partial candle bug?): {[c.detail for c in st_d.checks if not c.passed]}"
    assert audit_d.verdict == "ENTRY_READY"


def test_watchlist_auto_rescan_persistence_sqlite_and_upstash() -> None:
    """Verifikasi Watchlist Auto-Rescan (simpan, list, auto-prune kedaluwarsa, hapus) di SQLite & Upstash."""
    import time as _time
    from bot.storage import UpstashExitGuardStore

    async def _run() -> None:
        m1 = "DG2EJSEtPBge5F7zBCTZG78p27LTCiq8tUkJ39qupump"
        m2 = "32rBxcFjCFnyqZ41CEPM5AL67X1dcw8w83vZt6Kxpump"
        with tempfile.TemporaryDirectory() as tmp:
            store = ExitGuardStore(os.path.join(tmp, "wl.db"))
            await store.add_watchlist_mint(m1, ttl_minutes=120)
            await store.add_watchlist_mint(m2, ttl_minutes=120)
            listed = await store.list_watchlist_mints(limit=15)
            assert set(listed) == {m1, m2}
            await store.remove_watchlist_mint(m1)
            assert await store.list_watchlist_mints(limit=15) == [m2]

        # Simulasi Upstash Redis HGETALL dengan 1 mint aktif + 1 mint kedaluwarsa
        up = UpstashExitGuardStore("https://demo-redis.upstash.io", "token-xyz")
        future_exp = str(int(_time.time()) + 3600)
        past_exp = str(int(_time.time()) - 60)
        deleted_keys: list[str] = []

        async def _fake_cmd(*args):
            if args[0] == "HGETALL":
                return (True, [m1, future_exp, m2, past_exp])
            if args[0] == "HDEL":
                deleted_keys.extend(args[2:])
                return (True, 1)
            return (True, 1)

        up._cmd_with_status = _fake_cmd  # type: ignore[method-assign]
        active = await up.list_watchlist_mints(limit=15)
        assert active == [m1]
        assert deleted_keys == [m2]

    asyncio.run(_run())


def test_v5_freefall_dead_cat_bounce_below_vwap_and_two_red_cascade_are_vetoed() -> None:
    """v5: Koin terjun bebas (dead-cat micro-bounce di bawah 5m VWAP atau 2 candle merah beruntun) WAJIB HARD VETO."""
    from bot.structure import evaluate_candle_structure

    now_ms = 1_750_000_000_000
    cfg = BotConfig()
    pair = _make_pair(
        market_cap=145_000.0,
        liquidity_usd=26_000.0,
        vol_m5=18_000.0,
        vol_h1=195_000.0,
        vol_h24=240_000.0,
        buys_m5=110,
        sells_m5=95,
        buys_h1=650,
        sells_h1=540,
        price_change_m5=1.2,
        price_change_h1=45.0,
        price_change_h24=180.0,
        pair_created_at_ms=now_ms - 55 * 60_000,
    )
    at = evaluate_anti_top(pair, cfg, now_ms=now_ms)
    assert at.passed

    # Kasus A: Pump dari $30K ke $250K lalu terjun -42% ke $145K (di bawah 5m VWAP ~$190K) + pantulan tipis dead-cat
    freefall_dead_cat = [
        [1, 0.000030, 0.000060, 0.000028, 0.000055, 15_000],
        [2, 0.000055, 0.000140, 0.000052, 0.000135, 35_000],
        [3, 0.000135, 0.000250, 0.000130, 0.000240, 95_000],  # Puncak besar -> VWAP tinggi (~0.00019)
        [4, 0.000240, 0.000245, 0.000180, 0.000185, 65_000],  # Dump merah 1
        [5, 0.000185, 0.000190, 0.000140, 0.000144, 52_000],  # Dump merah 2
        [6, 0.000144, 0.000150, 0.000141, 0.000146, 12_000],  # Dead-cat micro bounce +1.4% jauh di bawah VWAP
    ]
    st_dead = evaluate_candle_structure(pair, at, freefall_dead_cat, [], cfg)
    assert not st_dead.passed
    assert st_dead.regime == "BREAKDOWN_DUMP"
    assert st_dead.vwap_deviation_pct < -6.0
    assert any(c.id == "anti_freefall_vwap" and not c.passed for c in st_dead.checks)

    # Kasus B: 2 candle 5m merah beruntun pasca-puncak (cascade) meski belum jatuh terlalu dalam
    two_red_cascade = [
        [1, 0.000080, 0.000105, 0.000078, 0.000102, 18_000],
        [2, 0.000102, 0.000130, 0.000100, 0.000126, 26_000],
        [3, 0.000126, 0.000152, 0.000124, 0.000148, 34_000],
        [4, 0.000148, 0.000155, 0.000140, 0.000144, 22_000],
        [5, 0.000144, 0.000146, 0.000135, 0.000137, 25_000],  # Merah 1
        [6, 0.000137, 0.000139, 0.000128, 0.000131, 24_000],  # Merah 2 (Lower Low)
    ]
    st_red = evaluate_candle_structure(pair, at, two_red_cascade, [], cfg)
    assert not st_red.passed
    assert st_red.regime == "BREAKDOWN_DUMP"
    assert any(c.id == "anti_freefall_vwap" and not c.passed for c in st_red.checks)


def test_v5_paid_boost_exit_liquidity_trap_is_hard_vetoed() -> None:
    """v5: Koin DexScreener Paid Boost (single pool / momentum biasa) WAJIB HARD VETO sebagai jebakan exit liquidity."""
    now_ms = 1_750_000_000_000
    cfg = BotConfig()

    boosted_trap = _make_pair(
        market_cap=110_000.0,
        liquidity_usd=22_000.0,
        vol_m5=14_000.0,
        vol_h1=95_000.0,
        buys_m5=72,
        sells_m5=60,
        buys_h1=410,
        sells_h1=350,
        price_change_m5=1.8,
        price_change_h1=38.0,
        is_boosted=True,
        pool_count=1,
        feed_sources=("gecko_trending_5m",),
        pair_created_at_ms=now_ms - 50 * 60_000,
    )
    at_trap = evaluate_anti_top(boosted_trap, cfg, now_ms=now_ms)
    assert not at_trap.passed
    assert any(c.id == "paid_boost_guard" and not c.passed for c in at_trap.checks)

    # Exception ketat: Paid Boost hanya boleh lewat jika multi-pool + 2 feed organik + Vol5m >= $25K + Buy5m >= 55% + 5m >= +2%
    boosted_viral_multi = _make_pair(
        market_cap=165_000.0,
        liquidity_usd=36_000.0,
        vol_m5=34_000.0,
        vol_h1=210_000.0,
        buys_m5=210,
        sells_m5=140,
        buys_h1=1_200,
        sells_h1=880,
        price_change_m5=5.5,
        price_change_h1=65.0,
        is_boosted=True,
        pool_count=2,
        feed_sources=("gecko_trending_5m", "dex_meteora"),
        pair_created_at_ms=now_ms - 55 * 60_000,
    )
    at_ok = evaluate_anti_top(boosted_viral_multi, cfg, now_ms=now_ms)
    assert at_ok.passed, f"Qualified viral multi-pool boosted breakout failed: {[c.detail for c in at_ok.checks if not c.passed]}"


def test_v5_hollow_cabal_shell_and_atx_nano_swarm_are_hard_vetoed() -> None:
    """v5: Hollow Cabal Shell (MC/Holder > $320) & pola ATX (Ya9PEJ: Top10 0.44%, High holder correlation, RC 12901) WAJIB VETO."""
    now_ms = 1_750_000_000_000

    # Kasus A: Hollow Cabal Shell ($180K MC dengan hanya 360 holders = $500/holder > $320/holder)
    hollow_pair = _make_pair(
        market_cap=180_000.0,
        liquidity_usd=32_000.0,
        pair_created_at_ms=now_ms - 50 * 60_000,
    )
    hollow_rc = _clean_rugcheck()
    hollow_rc["totalHolders"] = 360
    sec_hollow = evaluate_security(hollow_pair, hollow_rc, now_ms=now_ms)
    assert not sec_hollow.passed
    assert sec_hollow.mc_per_holder_usd == 500.0
    assert any(c.id == "organic_holders" and not c.passed for c in sec_hollow.checks)

    # Kasus B: Replay forensik ATX (Ya9PEJA9UZmbT5aim46TA6cPCkTNfsqEn1aEVrnpump) — Bundled Nano-Swarm Anomaly!
    atx_pair = _make_pair(
        mint="Ya9PEJA9UZmbT5aim46TA6cPCkTNfsqEn1aEVrnpump",
        symbol="ATX",
        name="ATX",
        market_cap=3_250_000_000.0,
        liquidity_usd=520_000.0,
        vol_m5=1_113.0,
        pair_created_at_ms=now_ms - 72 * 3_600_000,
    )
    atx_rc = _clean_rugcheck()
    atx_rc["score_normalised"] = 12_901
    atx_rc["totalHolders"] = 3_278
    atx_rc["risks"] = [
        {"name": "High holder correlation", "level": "danger"},
        {"name": "High market cap per holder", "level": "warn"},
    ]
    atx_rc["topHolders"] = [
        {"owner": f"AtxSwarm{i}11111111111111111111111111111111", "pct": 0.06 - i * 0.002}
        for i in range(10)
    ]
    sec_atx = evaluate_security(atx_pair, atx_rc, now_ms=now_ms)
    assert not sec_atx.passed
    assert sec_atx.pareto_status == "NANO_SWARM_DETECTED"
    assert any(c.id == "rugcheck_integrity" and not c.passed for c in sec_atx.checks)
    assert any(c.id == "organic_holders" and not c.passed for c in sec_atx.checks)


def test_v5_extreme_wash_churn_superfiona_trap_is_hard_vetoed() -> None:
    """v5: Jebakan wash-bot churn ekstrem ala SuperFIONA (Turn1h=38x, 1h=+23%, 5m=-0.05%) WAJIB HARD VETO."""
    now_ms = 1_750_000_000_000
    cfg = BotConfig()
    wash_pair = _make_pair(
        symbol="SuperFIONA",
        market_cap=140_000.0,
        liquidity_usd=28_000.0,
        vol_m5=82_000.0,
        vol_h1=5_400_000.0,  # Turnover 1h = 38.6x MC!
        vol_h24=5_400_000.0,
        buys_m5=210,
        sells_m5=180,
        buys_h1=5_200,
        sells_h1=4_800,
        price_change_m5=-0.05,
        price_change_h1=23.0,
        price_change_h24=23.0,
        pair_created_at_ms=now_ms - 55 * 60_000,
    )
    at_wash = evaluate_anti_top(wash_pair, cfg, now_ms=now_ms)
    assert not at_wash.passed
    assert any(c.id == "orderflow_quality" and not c.passed for c in at_wash.checks)


def test_v5_early_mihao_and_stonk_winner_dna_pass_with_high_score() -> None:
    """v5: Simulasi fase awal koin DNA Raksasa Organik (mihao / STONK): Non-boosted, Socials+Web, Multi-Pool, $89/holder, Above-VWAP."""
    from bot.scoring import score_and_finalize
    from bot.structure import evaluate_candle_structure

    now_ms = 1_750_000_000_000
    cfg = BotConfig()
    exec_ok = evaluate_execution_quotes(
        {"outAmount": "1000000", "priceImpactPct": "0.003"},
        {"outAmount": "98900000", "priceImpactPct": "0.004"},
        {"outAmount": "10000000", "priceImpactPct": "0.012"},
        {"outAmount": "962000000", "priceImpactPct": "0.014"},
        cfg,
    )
    winner_pair = _make_pair(
        mint="4CxjZyMNoF1p6eXHXHv1TWpS2X47CuUmLwtGe18spump",
        symbol="mihao",
        name="ni hao cat",
        market_cap=165_000.0,
        liquidity_usd=36_500.0,
        vol_m5=28_500.0,
        vol_h1=195_000.0,
        vol_h6=290_000.0,
        vol_h24=290_000.0,
        buys_m5=185,
        sells_m5=125,  # 59.7% buy
        buys_h1=1_280,
        sells_h1=940,  # 57.7% buy
        buys_h6=1_850,
        sells_h6=1_350,
        buys_h24=1_850,
        sells_h24=1_350,
        price_change_m5=6.5,
        price_change_h1=72.0,
        price_change_h24=165.0,
        pool_count=3,
        is_boosted=False,
        has_socials=True,
        social_count=2,
        has_website=True,
        has_image=True,
        pair_created_at_ms=now_ms - 85 * 60_000,
        feed_sources=("gecko_trending_1h", "dex_meteora", "dex_pumpswap"),
    )
    winner_rc = _clean_rugcheck()
    winner_rc["score_normalised"] = 1
    winner_rc["totalHolders"] = 1_850  # $89.2/holder!
    winner_rc["risks"] = []
    winner_rc["topHolders"] = [
        {"owner": "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1", "pct": 14.0},  # LP Vault
        {"owner": "HolderW11111111111111111111111111111111111", "pct": 3.4},
        {"owner": "HolderW21111111111111111111111111111111111", "pct": 2.6},
        {"owner": "HolderW31111111111111111111111111111111111", "pct": 2.0},
        {"owner": "HolderW41111111111111111111111111111111111", "pct": 1.6},
        {"owner": "HolderW51111111111111111111111111111111111", "pct": 1.3},
    ]
    winner_candles_5m = [
        [1, 0.000095, 0.000112, 0.000092, 0.000110, 14_000],
        [2, 0.000110, 0.000130, 0.000108, 0.000126, 18_500],
        [3, 0.000126, 0.000142, 0.000122, 0.000138, 21_000],
        [4, 0.000138, 0.000145, 0.000131, 0.000136, 12_500],  # Pullback sehat volume tipis
        [5, 0.000136, 0.000156, 0.000135, 0.000154, 24_000],  # Reclaim hijau kuat
        [6, 0.000154, 0.000168, 0.000152, 0.000165, 28_500],  # Breakout Above-VWAP
    ]
    sec = evaluate_security(winner_pair, winner_rc, now_ms=now_ms)
    at = evaluate_anti_top(winner_pair, cfg, now_ms=now_ms)
    st = evaluate_candle_structure(winner_pair, at, winner_candles_5m, [], cfg)
    audit = score_and_finalize(winner_pair, sec, at, exec_ok, cfg, structure=st)

    assert sec.passed, f"Winner L1 failed: {[c.detail for c in sec.checks if not c.passed]}"
    assert sec.mc_per_holder_usd < 100.0
    assert at.passed, f"Winner L2A failed: {[c.detail for c in at.checks if not c.passed]}"
    assert st.passed and st.vwap_deviation_pct > 0.0
    assert audit.verdict == "ENTRY_READY"
    assert audit.total_score >= 85, f"Expected >= 85 score for Winner-DNA, got {audit.total_score}"

