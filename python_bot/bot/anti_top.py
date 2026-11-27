"""Layer 2 — Anti-Top, Entry Corridor, Delay Projection & Orderflow Guard."""

from __future__ import annotations

import time
from typing import Optional

from .config import BotConfig, ENTRY_CORRIDORS, EntryCorridor
from .models import AntiTopResult, GateCheck, PairSnapshot


def find_corridor(
    projected_mc: float, include_midcap: bool
) -> Optional[EntryCorridor]:
    for corridor in ENTRY_CORRIDORS:
        if corridor.corridor_id in {"CORRIDOR_C_EXPANSION", "CORRIDOR_D_VIRAL_BASE"} and not include_midcap:
            continue
        if corridor.min_mc <= projected_mc <= corridor.max_mc:
            return corridor
    return None


def classify_red_zone_label(mc: float) -> str:
    if mc < 25_000:
        return "Red Zone (< $25K Micro-Cap)"
    if mc > 850_000:
        return "Late-Stage Top (> $850K)"
    return "Di Luar Koridor Entry"


def compute_effective_min_tx_24h(age_minutes: float) -> int:
    """Ambang jumlah transaksi 24h proporsional usia untuk fresh graduation (< 15m)."""
    if age_minutes < 5.0:
        return 18
    if age_minutes < 15.0:
        return 30
    return 60


def evaluate_anti_top(
    pair: PairSnapshot, cfg: BotConfig, now_ms: Optional[int] = None
) -> AntiTopResult:
    checks: list[GateCheck] = []
    now = now_ms if now_ms is not None else int(time.time() * 1000)

    spot_mc = max(0.0, float(pair.market_cap))
    liq_usd = max(0.0, float(pair.liquidity_usd))
    liq_mc_ratio_pct = (liq_usd / spot_mc * 100.0) if spot_mc > 0 else 0.0
    p_m5 = float(pair.price_change_m5)
    p_h1 = float(pair.price_change_h1)
    p_h6 = float(pair.price_change_h6)
    p_h24 = float(pair.price_change_h24)

    age_minutes = (
        max(0.1, (now - pair.pair_created_at_ms) / 60_000.0)
        if pair.pair_created_at_ms > 0
        else 999.0
    )

    tx_5m = pair.buys_m5 + pair.sells_m5
    tx_1h = pair.buys_h1 + pair.sells_h1
    tx_6h = pair.buys_h6 + pair.sells_h6
    tx_24h = pair.buys_h24 + pair.sells_h24
    buy_ratio_5m = (pair.buys_m5 / tx_5m * 100.0) if tx_5m > 0 else 50.0
    buy_ratio_1h = (pair.buys_h1 / tx_1h * 100.0) if tx_1h > 0 else 50.0
    buy_ratio_6h = (pair.buys_h6 / tx_6h * 100.0) if tx_6h > 0 else buy_ratio_1h
    avg_ticket_5m = (pair.vol_m5 / tx_5m) if tx_5m > 0 else 0.0
    avg_ticket_1h = (pair.vol_h1 / tx_1h) if tx_1h > 0 else 0.0
    turnover_h1_ratio = (pair.vol_h1 / spot_mc) if spot_mc > 0 else 0.0
    avg_5m_from_1h = (pair.vol_h1 / 12.0) if pair.vol_h1 > 0 else 0.0
    vol_m5_accel_ratio = (pair.vol_m5 / avg_5m_from_1h) if avg_5m_from_1h > 0 else 1.0
    is_daily_coin = age_minutes >= 20.0 * 60.0

    # Deteksi Viral Velocity (v4): arus modal deras seperti $Zero / PERRO / 4CHAN / PAIR
    is_viral_velocity = (
        (pair.vol_m5 >= 15_000.0 and pair.vol_h1 >= 120_000.0 and turnover_h1_ratio >= 0.70)
        or (pair.vol_m5 >= 25_000.0 and pair.vol_h1 >= 180_000.0 and turnover_h1_ratio >= 0.50)
    )
    # Ultra-Early Organic Fast-Track (v5.1): koin 6–9m dgn arus modal kuat langsung ditangkap di gelombang awal
    # tetap diverifikasi ketat di Layer 1 (>=500 holders, 0 risks, 0% dev) & Layer 2B (OHLCV 1m->2m/3m).
    is_hyper_viral_fasttrack = (
        6.0 <= age_minutes < cfg.min_pair_age_minutes
        and pair.vol_m5 >= 25_000.0
        and pair.vol_h1 >= 90_000.0
        and turnover_h1_ratio >= 1.00
        and liq_usd >= 16_000.0
        and buy_ratio_1h >= 54.0
        and buy_ratio_5m >= 52.0
        and tx_1h >= 350
    )
    # Kontraksi volume sehat saat pullback: Vol5m >= $12K tidak boleh dianggap sekarat (seperti $Zero $50K/5m di 0.43x accel!)
    is_pullback_contraction_ok = pair.vol_m5 >= 12_000.0

    # 1. Proyeksi Delay Eksekusi (default 22 detik) berbasis kecepatan momentum 5m
    positive_m5 = max(0.0, p_m5)
    drift_ratio = (positive_m5 / 100.0) * (cfg.reaction_delay_sec / 300.0) * 1.15
    projected_drift_pct = min(22.0, drift_ratio * 100.0)
    projected_entry_mc = spot_mc * (1.0 + projected_drift_pct / 100.0)

    corridor = find_corridor(projected_entry_mc, cfg.include_midcap_corridor)
    is_corridor_d = corridor is not None and corridor.corridor_id == "CORRIDOR_D_VIRAL_BASE"

    # Corridor D ($390K–$850K) HANYA diizinkan jika memenuhi syarat ketat Viral Base Breakout
    corridor_d_qualified = (
        is_corridor_d
        and pair.vol_h1 >= 250_000.0
        and pair.vol_m5 >= 15_000.0
        and liq_usd >= 40_000.0
        and 0.0 <= p_m5 <= 18.0
    )
    corridor_ok = (corridor is not None) and ((not is_corridor_d) or corridor_d_qualified)
    if is_corridor_d and not corridor_d_qualified:
        corridor_label = "Late-Stage Top (> $390K tanpa Viral Base)"
    else:
        corridor_label = (
            corridor.label if corridor else classify_red_zone_label(projected_entry_mc)
        )

    if corridor and corridor_ok:
        net_headroom_pct = (
            (corridor.ceiling_mc - projected_entry_mc) / projected_entry_mc * 100.0
            if projected_entry_mc > 0
            else 0.0
        )
    else:
        net_headroom_pct = 0.0

    if corridor_ok:
        corridor_severity = "PASS"
    elif is_corridor_d and not corridor_d_qualified:
        corridor_severity = "VETO"
    elif projected_entry_mc < 25_000 or projected_entry_mc > 850_000:
        corridor_severity = "VETO"
    else:
        corridor_severity = "WAIT_DIP"

    checks.append(
        GateCheck(
            id="entry_corridor",
            layer="ANTI_TOP",
            label="Koridor Early-Entry (A–D Viral Base) & Zona Atas",
            passed=corridor_ok,
            severity=corridor_severity,
            actual=f"Proj ${projected_entry_mc:,.0f} ({corridor_label})",
            threshold="$25K–$390K" + (" | D:$390K–$850K (Vol1h>=$250K, Liq>=$40K)" if cfg.include_midcap_corridor else ""),
            detail=(
                f"Masuk {corridor_label} setelah kompensasi delay {cfg.reaction_delay_sec}s (+{projected_drift_pct:.1f}%)."
                if corridor_ok
                else (
                    f"DITOLAK: MC ${projected_entry_mc:,.0f} di atas $390K namun belum memenuhi syarat Viral Base (wajib Vol1h >= $250K, Vol5m >= $15K, Liq >= $40K, 5m <= +18%)."
                    if is_corridor_d and not corridor_d_qualified
                    else f"Berada di {corridor_label} — di luar koridor aman."
                )
            ),
        )
    )

    # 2. Net Headroom pasca-delay menuju target tembok berikutnya
    headroom_ok = corridor_ok and net_headroom_pct >= cfg.min_net_headroom_pct
    checks.append(
        GateCheck(
            id="net_headroom",
            layer="ANTI_TOP",
            label="Ruang Napas Menuju Tembok (Headroom)",
            passed=headroom_ok,
            severity="PASS" if headroom_ok else "WAIT_DIP",
            actual=f"+{net_headroom_pct:.1f}%",
            threshold=f">= +{cfg.min_net_headroom_pct:.0f}% pasca-delay {cfg.reaction_delay_sec}s",
            detail=(
                f"Sisa ruang kenaikan bersih +{net_headroom_pct:.1f}% menuju tembok berikutnya."
                if headroom_ok
                else f"Terlalu mepet tembok psikologis (+{net_headroom_pct:.1f}%) — rawan jadi exit liquidity."
            ),
        )
    )

    # 2b. Survival Age Guard (v5.1: Persingkat ke >= 10m + jalur cepat Early Fast-Track 6–9m!)
    age_ok = age_minutes >= cfg.min_pair_age_minutes or is_hyper_viral_fasttrack
    checks.append(
        GateCheck(
            id="survival_age",
            layer="ANTI_TOP",
            label="Usia Bertahan Pasca-Migrasi (Early-Wave >=10m)",
            passed=age_ok,
            severity="PASS" if age_ok else "WAIT_DIP",
            actual=f"{age_minutes:.0f} menit" + (" (EARLY FAST-TRACK)" if is_hyper_viral_fasttrack else ""),
            threshold=f">= {cfg.min_pair_age_minutes:.0f} menit (atau >=6m Early Fast-Track)",
            detail=(
                f"Jalur Cepat Early Fast-Track aktif ({age_minutes:.0f}m, Vol5m ${pair.vol_m5:,.0f}, Vol1h ${pair.vol_h1:,.0f}) — wajib >=500 holders & 0 risks di L1."
                if is_hyper_viral_fasttrack
                else (
                    f"Sudah bertahan {age_minutes:.0f} menit pasca-migrasi (lolos dump sniper awal & masih fase early-wave)."
                    if age_ok
                    else f"Terlalu baru ({age_minutes:.0f}m < {cfg.min_pair_age_minutes:.0f}m) — tunggu lolos dump sniper 0–9m pertama."
                )
            ),
        )
    )

    # 3. Parabolic Cap & Anti-Falling-Knife Floor (v5.3: Tangkap Early Wave Koridor A +185% 1h tanpa meloloskan pisau jatuh!)
    is_proven_runner = age_minutes >= 120.0 and tx_24h >= 1_200
    is_early_corridor_a_wave = (
        age_minutes < 30.0
        and spot_mc <= 92_000.0
        and pair.vol_m5 >= 12_000.0
        and turnover_h1_ratio >= 0.90
        and buy_ratio_5m >= 54.0
        and buy_ratio_1h >= 54.0
        and (not pair.is_boosted)
    )
    effective_max_h1 = (
        cfg.max_h1_change_pct
        if is_proven_runner
        else (185.0 if is_early_corridor_a_wave else cfg.max_fresh_h1_change_pct)
    )
    effective_max_m5 = 18.0 if is_corridor_d else cfg.max_m5_change_pct

    # v5.2 Anti-Falling-Knife:
    # - Koin biasa: WAJIB 5m >= 0.0% & 1h >= -2.0% (dilarang beli candle merah yang mulai turun!)
    # - Hyper-Viral / Viral Pullback aktif (Vol5m >= $25K, Vol1h >= $250K, Buy5m >= 52% & Buy1h >= 53%): toleransi ringan hingga -1.5% 5m
    # - Koin Harian (>= 20j): wajib 5m >= +1.0% & 1h >= -4.0% (konfirmasi bangun hijau!)
    viral_mild_pullback_allowed = (
        is_viral_velocity
        and pair.vol_m5 >= 25_000.0
        and pair.vol_h1 >= 250_000.0
        and buy_ratio_5m >= 52.0
        and buy_ratio_1h >= 53.0
    )
    if is_daily_coin:
        m5_floor = 1.0
        h1_floor = -4.0
    elif viral_mild_pullback_allowed:
        m5_floor = -1.5
        h1_floor = 0.0
    else:
        m5_floor = 0.0
        h1_floor = -2.0

    m5_falling = p_m5 < m5_floor
    h1_falling = p_h1 < h1_floor
    m5_too_hot = p_m5 > effective_max_m5
    # Kunci v4/v5: koin viral ($Zero di $244K/$267K dgn +740% 1h & Vol5m $50K-$110K) TIDAK dibunuh oleh cap 1h kaku;
    # melainkan divalidasi ketat lewat Layer 2B OHLCV 5m (VWAP + Anti-Freefall Cascade)!
    h1_too_hot = (p_h1 > effective_max_h1) and (not is_viral_velocity)
    parabolic_ok = (not m5_falling) and (not h1_falling) and (not m5_too_hot) and (not h1_too_hot)

    if parabolic_ok and is_viral_velocity and p_h1 > effective_max_h1:
        parabolic_detail = (
            f"Ledakan viral terdeteksi (1h {p_h1:+.0f}%, Vol5m ${pair.vol_m5:,.0f}, 5m {p_m5:+.1f}%) — "
            "validasi pucuk vs pullback dialihkan ke struktur candle OHLCV 5m & VWAP."
        )
    elif parabolic_ok:
        parabolic_detail = "Kecepatan harga hijau terkendali (belum pucuk vertikal & tidak sedang jatuh bebas)."
    elif m5_falling or h1_falling:
        parabolic_detail = (
            f"DITOLAK (Anti-Freefall): Harga sedang melemah/merah (5m {p_m5:+.1f}% < {m5_floor:+.1f}%, 1h {p_h1:+.1f}% < {h1_floor:+.1f}%) — dilarang tangkap pisau jatuh."
        )
    else:
        parabolic_detail = (
            f"Sudah terlalu tinggi tanpa ledakan volume viral (+{p_h1:.0f}% 1h / +{p_m5:.0f}% 5m) — tunggu pullback sehat."
        )

    checks.append(
        GateCheck(
            id="parabolic_cap",
            layer="ANTI_TOP",
            label="Anti-Freefall Floor & Kontrol Kecepatan 5m/1h",
            passed=parabolic_ok,
            severity="PASS" if parabolic_ok else ("VETO" if (m5_falling or h1_falling) else "WAIT_DIP"),
            actual=f"5m {p_m5:+.1f}% · 1h {p_h1:+.1f}%" + (" (VIRAL)" if is_viral_velocity else ""),
            threshold=f"5m [{m5_floor:+.1f}%, +{effective_max_m5:.0f}%] · 1h [{h1_floor:+.1f}%, +{effective_max_h1:.0f}%] (atau Viral OHLCV)",
            detail=parabolic_detail,
        )
    )

    # 4. Likuiditas Minimum & Rasio LP/MC (Anti-Thin Pool)
    min_liq_req = 40_000.0 if is_corridor_d else cfg.min_liquidity_usd
    min_liq_ratio_req = 7.0 if is_corridor_d else 8.0
    liq_ok = liq_usd >= min_liq_req and liq_mc_ratio_pct >= min_liq_ratio_req
    checks.append(
        GateCheck(
            id="liquidity_floor",
            layer="ANTI_TOP",
            label="Kedalaman Likuiditas & Rasio LP/MC",
            passed=liq_ok,
            severity="PASS" if liq_ok else "VETO",
            actual=f"${liq_usd:,.0f} ({liq_mc_ratio_pct:.1f}% MC)",
            threshold=f">= ${min_liq_req:,.0f} & >= {min_liq_ratio_req:.1f}% MC",
            detail=(
                "Pool cukup tebal untuk menampung eksekusi masuk & keluar."
                if liq_ok
                else "DITOLAK: Pool terlalu tipis terhadap Market Cap (slippage ekstrem saat jual)."
            ),
        )
    )

    # 5. Pre-hitung Sleeper Revival / Daily Awakening agar ambang vol proporsional untuk koin harian
    avg_5m_vol_24h = (pair.vol_h24 / 288.0) if pair.vol_h24 > 0 else 0.0
    vol_wake_5m_ratio = (pair.vol_m5 / avg_5m_vol_24h) if avg_5m_vol_24h > 0 else 0.0
    vol_h1_share_24h_pct = (pair.vol_h1 / pair.vol_h24 * 100.0) if pair.vol_h24 > 0 else 0.0
    has_volume_wakeup = (
        (pair.vol_m5 >= 2_000.0 and vol_wake_5m_ratio >= 2.5)
        or (pair.vol_h1 >= 12_000.0 and vol_h1_share_24h_pct >= 25.0)
    )

    # 6. LIVE DEMAND VELOCITY (v4.1: MC-Scaled Steady Organic Climber + Pullback Contraction + Anti-Wash-Bot!)
    is_aged_wakeup = age_minutes >= 720.0 and has_volume_wakeup
    # Steady Organic Climber (seperti duk di $35K–$135K): Vol5m $4.5K–$7.9K diperbolehkan HANYA jika
    # arus 1 jam sangat tebal (Vol1h >= $45K, Turn1h >= 0.70x, Buy 1h >= 54%, ticket >= $28/tx, >=300 tx 1h)
    # dan tetap diverifikasi >=400 holders di Layer 1. Koin sampah tetap mati total!
    is_steady_organic_climber = (
        (not is_aged_wakeup)
        and age_minutes >= 45.0
        and spot_mc <= 145_000.0
        and pair.vol_m5 >= 4_500.0
        and pair.vol_h1 >= 45_000.0
        and turnover_h1_ratio >= 0.70
        and buy_ratio_1h >= 54.0
        and buy_ratio_5m >= 50.0
        and avg_ticket_5m >= 28.0
        and tx_1h >= 300
    )
    if is_aged_wakeup:
        req_vol_m5 = 2_000.0
        req_vol_h1 = 12_000.0
        req_turnover_h1 = 0.15
    elif is_steady_organic_climber:
        req_vol_m5 = 4_500.0
        req_vol_h1 = 45_000.0
        req_turnover_h1 = 0.70
    elif age_minutes < 30.0 and spot_mc <= 150_000.0:
        # v5.1 Proportional Early Volume: koin muda 6–29m di MC $25K–$150K tidak perlu menunggu volume 1 jam penuh
        age_scale = max(0.40, min(1.0, age_minutes / 30.0))
        req_vol_m5 = cfg.min_vol_m5_usd
        req_vol_h1 = max(22_000.0, round(cfg.min_vol_h1_usd * age_scale, 0))
        req_turnover_h1 = max(0.35, round(cfg.min_turnover_h1_ratio * age_scale, 2))
    else:
        req_vol_m5 = cfg.min_vol_m5_usd
        req_vol_h1 = cfg.min_vol_h1_usd
        req_turnover_h1 = cfg.min_turnover_h1_ratio

    vol_m5_ok = pair.vol_m5 >= req_vol_m5 and tx_5m >= 8
    vol_h1_ok = (age_minutes < 10.0) or (
        pair.vol_h1 >= req_vol_h1 and turnover_h1_ratio >= req_turnover_h1
    )
    # Anti-Momentum-Death (v4): kecualikan kontraksi volume sehat saat pullback jika Vol5m absolut >= $12K!
    vol_dying = (
        (age_minutes >= 25.0)
        and (pair.vol_h1 > 0)
        and (vol_m5_accel_ratio < cfg.min_vol_m5_accel_ratio)
        and (not is_pullback_contraction_ok)
    )
    wash_bot_5m = tx_5m >= 12 and avg_ticket_5m < 22.0
    sideways_churn = (not is_daily_coin) and turnover_h1_ratio >= 1.8 and p_h1 < 8.0
    # v5 Extreme Wash-Churn (kasus SuperFIONA: Turn1h 38.8x tapi harga flat +23% / 5m -0.05%)
    extreme_wash_churn = (
        (not is_daily_coin)
        and turnover_h1_ratio >= 8.0
        and (p_h1 < 60.0 or p_m5 < 1.0)
    )

    effective_min_tx = compute_effective_min_tx_24h(age_minutes)
    tx_count_ok = tx_24h >= effective_min_tx
    ticket_1h_ok = (tx_1h == 0) or (avg_ticket_1h >= 20.0)

    flow_ok = (
        vol_m5_ok
        and vol_h1_ok
        and (not vol_dying)
        and (not wash_bot_5m)
        and (not sideways_churn)
        and (not extreme_wash_churn)
        and tx_count_ok
        and ticket_1h_ok
    )
    if wash_bot_5m:
        flow_detail = (
            f"DITOLAK: Terdeteksi wash-bot/micro-bot 5m ({tx_5m} tx hanya ${pair.vol_m5:,.0f} = ${avg_ticket_5m:.1f}/tx)."
        )
    elif extreme_wash_churn:
        flow_detail = (
            f"DITOLAK: Extreme Wash-Bot Churn (turnover 1h {turnover_h1_ratio:.1f}x MC tetapi harga tidak naik proporsional: 1h {p_h1:+.1f}%, 5m {p_m5:+.1f}%)."
        )
    elif vol_dying:
        flow_detail = (
            f"DITOLAK: Volume 5m sedang sekarat/turun drastis (${pair.vol_m5:,.0f}, accel {vol_m5_accel_ratio:.2f}x dari rata-rata 1h — momentum habis)."
        )
    elif sideways_churn:
        flow_detail = (
            f"DITOLAK: Distribusi/wash sideways (turnover 1h {turnover_h1_ratio:.2f}x MC tapi harga hanya {p_h1:+.1f}%)."
        )
    elif not vol_m5_ok:
        flow_detail = (
            f"DITOLAK: Permintaan 5m terlalu lemah (${pair.vol_m5:,.0f} < ${req_vol_m5:,.0f} / {tx_5m} tx) — rawan langsung mati."
        )
    elif not vol_h1_ok:
        flow_detail = (
            f"DITOLAK: Arus modal 1h terlalu lemah (${pair.vol_h1:,.0f} < ${req_vol_h1:,.0f}, turnover {turnover_h1_ratio:.2f}x < {req_turnover_h1:.2f}x MC)."
        )
    elif not tx_count_ok or not ticket_1h_ok:
        flow_detail = (
            f"DITOLAK: Aktivitas organik kurang ({tx_24h} tx, avg 1h ${avg_ticket_1h:.0f}/tx)."
        )
    elif is_pullback_contraction_ok and vol_m5_accel_ratio < cfg.min_vol_m5_accel_ratio:
        flow_detail = (
            f"Kontraksi volume pullback sehat: Vol5m tetap tebal ${pair.vol_m5:,.0f} (accel {vol_m5_accel_ratio:.2f}x · Turn1h {turnover_h1_ratio:.2f}x MC)."
        )
    else:
        flow_detail = (
            f"Demand viral/organik kuat: Vol5m ${pair.vol_m5:,.0f} (accel {vol_m5_accel_ratio:.2f}x, ${avg_ticket_5m:.0f}/tx) · Turn1h {turnover_h1_ratio:.2f}x."
        )

    checks.append(
        GateCheck(
            id="orderflow_quality",
            layer="ANTI_TOP",
            label="Kecepatan Arus Modal, Kontraksi Pullback & Anti-Wash-Bot",
            passed=flow_ok,
            severity="PASS" if flow_ok else "VETO",
            actual=f"5m ${pair.vol_m5:,.0f} ({vol_m5_accel_ratio:.2f}x · ${avg_ticket_5m:.0f}/tx) · 1h ${pair.vol_h1:,.0f} ({turnover_h1_ratio:.2f}x)",
            threshold=f"5m>=${req_vol_m5:,.0f}, 1h>=${req_vol_h1:,.0f}, Turn>={req_turnover_h1:.2f}x, >=$22/tx",
            detail=flow_detail,
        )
    )

    # 7. Tekanan Beli Net-Positive (v5: Buyer WAJIB menang >= 52%!) & Anti-Whale-Siphon
    whale_siphon = pair.buys_m5 >= 12 and buy_ratio_5m >= 62.0 and p_m5 < -0.5
    whale_absorption = (
        p_m5 >= 3.0
        and pair.vol_m5 >= 15_000.0
        and 44.0 <= buy_ratio_5m < 52.0
        and buy_ratio_1h >= 51.0
    )
    h6_sell_bleed = age_minutes >= 360.0 and tx_6h >= 20 and buy_ratio_6h < 49.0

    if whale_siphon:
        siphon_ok = False
        siphon_sev = "VETO"
        siphon_detail = (
            f"DITOLAK: Whale Siphon — jumlah klik beli tinggi ({buy_ratio_5m:.0f}%) tetapi harga justru turun ({p_m5:+.1f}%)."
        )
    elif h6_sell_bleed:
        siphon_ok = False
        siphon_sev = "VETO"
        siphon_detail = (
            f"DITOLAK: Distribusi multi-jam (6h buy hanya {buy_ratio_6h:.0f}% < 49%) — rawan dead-cat bounce lalu terjun bebas."
        )
    elif whale_absorption:
        siphon_ok = True
        siphon_sev = "PASS"
        siphon_detail = (
            f"Akumulasi Whale terdeteksi (5m {p_m5:+.1f}% dengan {buy_ratio_5m:.0f}% buy tx & Vol5m ${pair.vol_m5:,.0f} — order beli besar menyerap retail TP)."
        )
    else:
        siphon_ok = buy_ratio_5m >= 52.0 and buy_ratio_1h >= 52.0
        siphon_sev = "PASS" if siphon_ok else ("VETO" if (buy_ratio_5m < 47.0 or buy_ratio_1h < 48.0) else "WAIT_DIP")
        siphon_detail = (
            f"Dominasi buyer bersih (5m {buy_ratio_5m:.0f}% · 1h {buy_ratio_1h:.0f}% buy)."
            if siphon_ok
            else f"Tekanan beli belum dominan (5m {buy_ratio_5m:.0f}%, 1h {buy_ratio_1h:.0f}% < 52%) — rawan distribusi/turun bebas."
        )

    checks.append(
        GateCheck(
            id="buy_pressure",
            layer="ANTI_TOP",
            label="Dominasi Buyer Net-Positive (>=52%) & Anti-Siphon",
            passed=siphon_ok,
            severity=siphon_sev,
            actual=f"5m {buy_ratio_5m:.0f}% · 1h {buy_ratio_1h:.0f}% · 6h {buy_ratio_6h:.0f}% buy",
            threshold="5m>=52% & 1h>=52% (atau Whale Absorption +3%), 6h>=49%",
            detail=siphon_detail,
        )
    )

    # 7b. Paid-Boost Exit-Liquidity Trap Guard (v5: Seluruh raksasa organik mihao/STONK/$Zero/PAIR/duk = Non-Boosted!)
    from .config import ORGANIC_FEED_SOURCES

    organic_feeds_cnt = sum(1 for s in pair.feed_sources if s in ORGANIC_FEED_SOURCES)
    boost_override_ok = (
        organic_feeds_cnt >= 2
        and pair.pool_count >= 2
        and pair.vol_m5 >= 25_000.0
        and buy_ratio_5m >= 55.0
        and p_m5 >= 2.0
    )
    paid_boost_ok = (not pair.is_boosted) or boost_override_ok
    checks.append(
        GateCheck(
            id="paid_boost_guard",
            layer="ANTI_TOP",
            label="Anti-Jebakan Paid Boost Exit-Liquidity",
            passed=paid_boost_ok,
            severity="PASS" if paid_boost_ok else "VETO",
            actual="Organik (Tanpa Boost)" if not pair.is_boosted else f"PAID BOOST ({organic_feeds_cnt} org, {pair.pool_count} pool)",
            threshold="Non-Boosted (atau >=2 org feed + >=2 pool + Vol5m>=$25K + Buy>=55%)",
            detail=(
                "100% pertumbuhan organik tanpa iklan Paid Boost DexScreener."
                if not pair.is_boosted
                else (
                    "Ada Paid Boost tetapi terkonfirmasi kuat oleh multi-pool & arus organik masif."
                    if paid_boost_ok
                    else "DITOLAK (Jebakan Paid Boost): Token membeli boost DexScreener tanpa konfirmasi multi-pool & demand organik kuat — sangat rawan dump bebas."
                )
            ),
        )
    )

    # 8. Pre-ATH & Anti-Late-Parabolic Extension Guard (v5.1: Blokir entry telat setelah naik +1000% s/d +5000%!)
    is_extreme_parabolic_late = (not is_daily_coin) and (
        p_h24 >= 2_000.0 or p_h6 >= 1_200.0 or p_h1 >= 900.0
    )
    is_intraday_late_non_base = (
        (not is_daily_coin)
        and age_minutes >= 60.0
        and (not corridor_d_qualified)
        and (p_h24 >= 850.0 or p_h6 >= 600.0)
    )
    is_near_local_ath = age_minutes >= 360.0 and (p_h6 > 160.0 or p_h24 > 260.0)
    late_ath_hard_veto = is_extreme_parabolic_late or is_intraday_late_non_base
    pre_ath_ok = (not is_near_local_ath) and (not late_ath_hard_veto)
    checks.append(
        GateCheck(
            id="pre_ath_extension",
            layer="ANTI_TOP",
            label="Anti-Late-ATH & Pre-ATH Extension Guard (Bukan Setelah +1000%–5000%)",
            passed=pre_ath_ok,
            severity="PASS" if pre_ath_ok else ("VETO" if late_ath_hard_veto else "WAIT_DIP"),
            actual=f"1h {p_h1:+.0f}% · 6h {p_h6:+.0f}% · 24h {p_h24:+.0f}% (usia {age_minutes / 60.0:.1f}j)",
            threshold="Intraday < +850% 24h (ekstrem < +2000%) · Koin >=6j: 6h <= +160% & 24h <= +260%",
            detail=(
                "Masih di fase awal/sebelum zona puncak ekstrem — ruang kenaikan masih lebar."
                if pre_ath_ok
                else (
                    f"DITOLAK (Terlalu Telat / Post-Parabolic ATH): Koin sudah melesat terlalu jauh (1h {p_h1:+.0f}%, 6h {p_h6:+.0f}%, 24h {p_h24:+.0f}%) — bukan lagi fase awal!"
                    if late_ath_hard_veto
                    else "Koin tua sudah naik terlalu tinggi dalam 6–24 jam terakhir (dekat ATH lokal) — tunggu base konsolidasi baru."
                )
            ),
        )
    )

    # 9. Deteksi Sleeper Revival / Daily Consolidation Candidate (usia >= 12 jam yg baru bangun)
    is_early_pre_ath_wave = (
        1.0 <= p_m5 <= 22.0
        and -2.0 <= p_h1 <= 85.0
        and p_h6 <= 120.0
        and p_h24 <= 220.0
    )
    has_revival_buyers = (
        buy_ratio_5m >= 52.0 and buy_ratio_1h >= 52.0 and buy_ratio_6h >= 50.0 and pair.buys_m5 >= 5
    )
    is_sleeper_revival = (
        age_minutes >= 720.0
        and corridor_ok
        and headroom_ok
        and pre_ath_ok
        and paid_boost_ok
        and has_volume_wakeup
        and is_early_pre_ath_wave
        and has_revival_buyers
    )
    has_community_presence = bool(pair.has_socials or pair.has_website)

    passed = all(c.passed for c in checks)
    has_hard_veto = any((not c.passed) and c.severity == "VETO" for c in checks)
    wait_dip_only = (not passed) and (not has_hard_veto)

    return AntiTopResult(
        passed=passed,
        wait_dip_only=wait_dip_only,
        checks=tuple(checks),
        corridor_id=corridor.corridor_id if (corridor and corridor_ok) else None,
        corridor_label=corridor_label,
        spot_mc=round(spot_mc, 2),
        projected_entry_mc=round(projected_entry_mc, 2),
        projected_delay_drift_pct=round(projected_drift_pct, 2),
        net_headroom_pct=round(net_headroom_pct, 2),
        age_minutes=round(age_minutes, 1),
        liq_usd=round(liq_usd, 2),
        liq_mc_ratio_pct=round(liq_mc_ratio_pct, 2),
        p_m5=round(p_m5, 2),
        p_h1=round(p_h1, 2),
        tx_24h=tx_24h,
        effective_min_tx_24h=effective_min_tx,
        avg_ticket_1h_usd=round(avg_ticket_1h, 2),
        buy_ratio_5m_pct=round(buy_ratio_5m, 1),
        buy_ratio_1h_pct=round(buy_ratio_1h, 1),
        p_h6=round(p_h6, 2),
        p_h24=round(p_h24, 2),
        is_sleeper_revival=is_sleeper_revival,
        vol_wake_5m_ratio=round(vol_wake_5m_ratio, 2),
        vol_h1_share_24h_pct=round(vol_h1_share_24h_pct, 1),
        avg_ticket_5m_usd=round(avg_ticket_5m, 2),
        turnover_h1_ratio=round(turnover_h1_ratio, 2),
        whale_absorption=whale_absorption,
        vol_m5_accel_ratio=round(vol_m5_accel_ratio, 2),
        buy_ratio_6h_pct=round(buy_ratio_6h, 1),
        is_viral_velocity=is_viral_velocity,
        is_pullback_contraction_ok=is_pullback_contraction_ok,
        is_corridor_d_viral_base=corridor_d_qualified,
        is_hyper_viral_fasttrack=is_hyper_viral_fasttrack,
        is_steady_organic_climber=is_steady_organic_climber,
        has_community_presence=has_community_presence,
    )
