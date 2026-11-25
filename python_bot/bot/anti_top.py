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
        if corridor.corridor_id == "CORRIDOR_C_EXPANSION" and not include_midcap:
            continue
        if corridor.min_mc <= projected_mc <= corridor.max_mc:
            return corridor
    return None


def classify_red_zone_label(mc: float) -> str:
    if mc < 25_000:
        return "Red Zone (< $25K Micro-Cap)"
    if mc > 390_000:
        return "Late-Stage Top (> $390K)"
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
    tx_24h = pair.buys_h24 + pair.sells_h24
    buy_ratio_5m = (pair.buys_m5 / tx_5m * 100.0) if tx_5m > 0 else 50.0
    buy_ratio_1h = (pair.buys_h1 / tx_1h * 100.0) if tx_1h > 0 else 50.0
    avg_ticket_5m = (pair.vol_m5 / tx_5m) if tx_5m > 0 else 0.0
    avg_ticket_1h = (pair.vol_h1 / tx_1h) if tx_1h > 0 else 0.0
    turnover_h1_ratio = (pair.vol_h1 / spot_mc) if spot_mc > 0 else 0.0

    # 1. Proyeksi Delay Eksekusi (default 22 detik) berbasis kecepatan momentum 5m
    positive_m5 = max(0.0, p_m5)
    drift_ratio = (positive_m5 / 100.0) * (cfg.reaction_delay_sec / 300.0) * 1.15
    projected_drift_pct = min(22.0, drift_ratio * 100.0)
    projected_entry_mc = spot_mc * (1.0 + projected_drift_pct / 100.0)

    corridor = find_corridor(projected_entry_mc, cfg.include_midcap_corridor)
    corridor_ok = corridor is not None
    corridor_label = (
        corridor.label if corridor else classify_red_zone_label(projected_entry_mc)
    )

    if corridor:
        net_headroom_pct = (
            (corridor.ceiling_mc - projected_entry_mc) / projected_entry_mc * 100.0
            if projected_entry_mc > 0
            else 0.0
        )
    else:
        net_headroom_pct = 0.0

    corridor_severity = (
        "PASS"
        if corridor_ok
        else ("VETO" if projected_entry_mc < 25_000 or projected_entry_mc > 450_000 else "WAIT_DIP")
    )
    checks.append(
        GateCheck(
            id="entry_corridor",
            layer="ANTI_TOP",
            label="Koridor Early-Entry PumpSwap & Zona Atas",
            passed=corridor_ok,
            severity=corridor_severity,
            actual=f"Proj ${projected_entry_mc:,.0f} ({corridor_label})",
            threshold="$25K–$92K | $92K–$210K" + (" | $210K–$390K" if cfg.include_midcap_corridor else ""),
            detail=(
                f"Masuk {corridor_label} setelah kompensasi delay {cfg.reaction_delay_sec}s (+{projected_drift_pct:.1f}%)."
                if corridor_ok
                else f"Berada di {corridor_label} — tunggu pullback ke koridor aman."
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

    # 3. Parabolic Candle 5m/1h Cap + Anti-Dead-Bounce Floor (Blokir koin sekarat yg minus tajam!)
    m5_falling = p_m5 < -3.5
    h1_falling = p_h1 < -10.0
    m5_too_hot = p_m5 > cfg.max_m5_change_pct
    h1_too_hot = p_h1 > cfg.max_h1_change_pct
    parabolic_ok = (not m5_falling) and (not h1_falling) and (not m5_too_hot) and (not h1_too_hot)
    checks.append(
        GateCheck(
            id="parabolic_cap",
            layer="ANTI_TOP",
            label="Batas Candle Parabolik & Anti-Koin Sekarat",
            passed=parabolic_ok,
            severity="PASS" if parabolic_ok else ("VETO" if (m5_falling or h1_falling) else "WAIT_DIP"),
            actual=f"5m {p_m5:+.1f}% · 1h {p_h1:+.1f}%",
            threshold=f"5m [-3.5%, +{cfg.max_m5_change_pct:.0f}%] · 1h [-10%, +{cfg.max_h1_change_pct:.0f}%]",
            detail=(
                "Kecepatan harga sehat (aktif naik/konsolidasi, bukan pucuk vertikal atau koin jatuh)."
                if parabolic_ok
                else (
                    f"DITOLAK: Tren sedang jatuh/sekarat (5m {p_m5:+.1f}%, 1h {p_h1:+.1f}%) — jangan beli koin mati."
                    if (m5_falling or h1_falling)
                    else "Candle sudah terlalu vertikal — tunggu koreksi sehat sebelum entry."
                )
            ),
        )
    )

    # 4. Likuiditas Minimum & Rasio LP/MC (Anti-Thin Pool)
    liq_ok = liq_usd >= cfg.min_liquidity_usd and liq_mc_ratio_pct >= 8.0
    checks.append(
        GateCheck(
            id="liquidity_floor",
            layer="ANTI_TOP",
            label="Kedalaman Likuiditas & Rasio LP/MC",
            passed=liq_ok,
            severity="PASS" if liq_ok else "VETO",
            actual=f"${liq_usd:,.0f} ({liq_mc_ratio_pct:.1f}% MC)",
            threshold=f">= ${cfg.min_liquidity_usd:,.0f} & >= 8.0% MC",
            detail=(
                "Pool cukup tebal untuk menampung eksekusi masuk & keluar."
                if liq_ok
                else "DITOLAK: Pool terlalu tipis terhadap Market Cap (slippage ekstrem saat jual)."
            ),
        )
    )

    # 5. Pre-hitung Sleeper Revival Wake-up agar ambang vol 5m proporsional
    avg_5m_vol_24h = (pair.vol_h24 / 288.0) if pair.vol_h24 > 0 else 0.0
    vol_wake_5m_ratio = (pair.vol_m5 / avg_5m_vol_24h) if avg_5m_vol_24h > 0 else 0.0
    vol_h1_share_24h_pct = (pair.vol_h1 / pair.vol_h24 * 100.0) if pair.vol_h24 > 0 else 0.0
    has_volume_wakeup = (
        (pair.vol_m5 >= 1_200.0 and vol_wake_5m_ratio >= 3.5)
        or (pair.vol_h1 >= 6_000.0 and vol_h1_share_24h_pct >= 30.0)
    )

    # 6. LIVE DEMAND VELOCITY & ANTI-WASH-BOT 5m (Penyaring utama anti-koin gagal naik & mati!)
    req_vol_m5 = (
        min(cfg.min_vol_m5_usd, 1_200.0)
        if (age_minutes < 10.0 or (age_minutes >= 720.0 and has_volume_wakeup))
        else cfg.min_vol_m5_usd
    )
    vol_m5_ok = pair.vol_m5 >= req_vol_m5 and tx_5m >= 8
    vol_h1_ok = (age_minutes < 15.0) or (
        (pair.vol_h1 >= cfg.min_vol_h1_usd and turnover_h1_ratio >= cfg.min_turnover_h1_ratio)
        or (age_minutes >= 720.0 and has_volume_wakeup and pair.vol_h1 >= 6_000.0)
    )
    wash_bot_5m = tx_5m >= 12 and avg_ticket_5m < 18.0
    sideways_churn = turnover_h1_ratio >= 1.8 and p_h1 < 8.0

    effective_min_tx = compute_effective_min_tx_24h(age_minutes)
    tx_count_ok = tx_24h >= effective_min_tx
    ticket_1h_ok = (tx_1h == 0) or (avg_ticket_1h >= 18.0)

    flow_ok = (
        vol_m5_ok
        and vol_h1_ok
        and (not wash_bot_5m)
        and (not sideways_churn)
        and tx_count_ok
        and ticket_1h_ok
    )
    if wash_bot_5m:
        flow_detail = (
            f"DITOLAK: Terdeteksi wash-bot 5m ({tx_5m} tx hanya ${pair.vol_m5:,.0f} = ${avg_ticket_5m:.1f}/tx)."
        )
    elif sideways_churn:
        flow_detail = (
            f"DITOLAK: Distribusi/wash sideways (turnover 1h {turnover_h1_ratio:.2f}x MC tapi harga hanya {p_h1:+.1f}%)."
        )
    elif not vol_m5_ok:
        flow_detail = (
            f"DITOLAK: Permintaan 5m terlalu lemah (${pair.vol_m5:,.0f} / {tx_5m} tx) — rawan langsung mati."
        )
    elif not vol_h1_ok:
        flow_detail = (
            f"DITOLAK: Volume/perputaran 1h lesu (${pair.vol_h1:,.0f}, turnover {turnover_h1_ratio:.2f}x MC)."
        )
    elif not tx_count_ok or not ticket_1h_ok:
        flow_detail = (
            f"DITOLAK: Aktivitas organik kurang ({tx_24h} tx, avg 1h ${avg_ticket_1h:.0f}/tx)."
        )
    else:
        flow_detail = (
            f"Demand organik aktif: Vol5m ${pair.vol_m5:,.0f} (${avg_ticket_5m:.0f}/tx) · Turnover 1h {turnover_h1_ratio:.2f}x MC."
        )

    checks.append(
        GateCheck(
            id="orderflow_quality",
            layer="ANTI_TOP",
            label="Kecepatan Demand Organik & Anti-Wash-Bot",
            passed=flow_ok,
            severity="PASS" if flow_ok else "VETO",
            actual=f"5m ${pair.vol_m5:,.0f} (${avg_ticket_5m:.0f}/tx) · 1h ${pair.vol_h1:,.0f} ({turnover_h1_ratio:.2f}x)",
            threshold=f"5m >= ${req_vol_m5:,.0f} (>=8 tx, >=$18/tx) · 1h >= ${cfg.min_vol_h1_usd:,.0f} (>={cfg.min_turnover_h1_ratio:.2f}x MC)",
            detail=flow_detail,
        )
    )

    # 7. Tekanan Beli & Whale Absorption (Mendukung akumulasi whale saat retail TP + blokir Whale Siphon!)
    whale_siphon = pair.buys_m5 >= 12 and buy_ratio_5m >= 62.0 and p_m5 < -1.0
    whale_absorption = (
        p_m5 >= 2.0 and 42.0 <= buy_ratio_5m < 48.0 and buy_ratio_1h >= 48.0
    )
    if whale_siphon:
        siphon_ok = False
        siphon_sev = "VETO"
        siphon_detail = (
            f"DITOLAK: Whale Siphon — jumlah klik beli tinggi ({buy_ratio_5m:.0f}%) tetapi harga justru turun ({p_m5:+.1f}%)."
        )
    elif p_m5 >= 2.0:
        # Harga 5m naik >= +2%: izinkan buy_ratio_5m >= 42% (whale beli ukuran besar, retail TP ukuran kecil)
        siphon_ok = buy_ratio_5m >= 42.0 and buy_ratio_1h >= 48.0
        siphon_sev = "PASS" if siphon_ok else "WAIT_DIP"
        siphon_detail = (
            f"Akumulasi Whale terdeteksi (5m {p_m5:+.1f}% dengan {buy_ratio_5m:.0f}% buy tx — order beli lebih besar dari jual)."
            if whale_absorption and siphon_ok
            else (
                "Dominasi buyer sehat tanpa pola distribusi diam-diam."
                if siphon_ok
                else "Tekanan jual 1h/5m masih terlalu berat — tunggu penyerapan selesai."
            )
        )
    elif p_m5 < 0.0:
        siphon_ok = buy_ratio_5m >= 52.0 and buy_ratio_1h >= 48.0
        siphon_sev = "PASS" if siphon_ok else "WAIT_DIP"
        siphon_detail = (
            "Pullback ringan masih diserap buyer."
            if siphon_ok
            else "Harga 5m minus disertai dominasi jual — tunggu buyer kembali memegang kendali."
        )
    else:
        siphon_ok = buy_ratio_5m >= 48.0 and buy_ratio_1h >= 48.0
        siphon_sev = "PASS" if siphon_ok else "WAIT_DIP"
        siphon_detail = (
            "Dominasi buyer sehat tanpa pola distribusi diam-diam."
            if siphon_ok
            else "Tekanan jual sedang mendominasi — tunggu penyerapan seller selesai."
        )

    checks.append(
        GateCheck(
            id="buy_pressure",
            layer="ANTI_TOP",
            label="Tekanan Beli & Whale Absorption",
            passed=siphon_ok,
            severity=siphon_sev,
            actual=f"5m {buy_ratio_5m:.0f}% buy ({p_m5:+.1f}%) · 1h {buy_ratio_1h:.0f}% buy",
            threshold=">=48% buy (atau >=42% jika 5m >= +2% Whale Absorption) & anti-siphon",
            detail=siphon_detail,
        )
    )

    # 8. Pre-ATH Extension Guard (Khusus koin berusia >= 6 jam: jangan masuk dekat ATH lokal)
    is_near_local_ath = age_minutes >= 360.0 and (p_h6 > 180.0 or p_h24 > 300.0)
    pre_ath_ok = not is_near_local_ath
    checks.append(
        GateCheck(
            id="pre_ath_extension",
            layer="ANTI_TOP",
            label="Pre-ATH Extension Guard (Koin Berusia >= 6 Jam)",
            passed=pre_ath_ok,
            severity="PASS" if pre_ath_ok else "WAIT_DIP",
            actual=f"6h {p_h6:+.1f}% · 24h {p_h24:+.1f}% (usia {age_minutes / 60.0:.1f}j)",
            threshold="6h <= +180% & 24h <= +300% (sebelum zona ATH lokal)",
            detail=(
                "Masih di fase awal sebelum zona ATH — ruang ambil profit puluhan persen tetap terbuka."
                if pre_ath_ok
                else "Koin tua sudah naik terlalu tinggi dalam 6–24 jam terakhir (dekat ATH lokal) — tunggu pullback/base baru."
            ),
        )
    )

    # 9. Deteksi Sleeper Revival / Aged Awakening (koin lahir >= 12 jam / beberapa hari lalu yg BARU bangun)
    is_early_pre_ath_wave = (
        1.0 <= p_m5 <= 22.0
        and 2.0 <= p_h1 <= 85.0
        and p_h6 <= 140.0
        and p_h24 <= 220.0
    )
    has_revival_buyers = (
        buy_ratio_5m >= 52.0 and buy_ratio_1h >= 50.0 and pair.buys_m5 >= 5
    )
    is_sleeper_revival = (
        age_minutes >= 720.0
        and corridor_ok
        and headroom_ok
        and pre_ath_ok
        and has_volume_wakeup
        and is_early_pre_ath_wave
        and has_revival_buyers
    )

    passed = all(c.passed for c in checks)
    has_hard_veto = any((not c.passed) and c.severity == "VETO" for c in checks)
    wait_dip_only = (not passed) and (not has_hard_veto)

    return AntiTopResult(
        passed=passed,
        wait_dip_only=wait_dip_only,
        checks=tuple(checks),
        corridor_id=corridor.corridor_id if corridor else None,
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
    )
