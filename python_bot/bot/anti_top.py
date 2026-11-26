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
    # Hyper-Viral Fast-Track (v4.1): koin 12–19m dgn arus modal masif seperti PAIR ($73K/5m, $256K/1h)
    # akan tetap diverifikasi ketat di Layer 1 (>=750 holders, 0 risks, 0% dev) & Layer 2B (OHLCV 5m/1m).
    is_hyper_viral_fasttrack = (
        12.0 <= age_minutes < cfg.min_pair_age_minutes
        and pair.vol_m5 >= 35_000.0
        and pair.vol_h1 >= 150_000.0
        and turnover_h1_ratio >= 1.20
        and liq_usd >= 20_000.0
        and buy_ratio_1h >= 52.0
        and buy_ratio_5m >= 48.0
        and tx_1h >= 600
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
        and -3.5 <= p_m5 <= 18.0
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

    # 2b. Survival Age Guard (Blokir koin ultra-baru < 20m, kecuali jalur ketat Hyper-Viral Fast-Track 12–19m!)
    age_ok = age_minutes >= cfg.min_pair_age_minutes or is_hyper_viral_fasttrack
    checks.append(
        GateCheck(
            id="survival_age",
            layer="ANTI_TOP",
            label="Usia Bertahan Pasca-Migrasi (Anti-Sniper Dump)",
            passed=age_ok,
            severity="PASS" if age_ok else "WAIT_DIP",
            actual=f"{age_minutes:.0f} menit" + (" (FAST-TRACK)" if is_hyper_viral_fasttrack else ""),
            threshold=f">= {cfg.min_pair_age_minutes:.0f} menit (atau >=12m Hyper-Viral Fast-Track)",
            detail=(
                f"Jalur Cepat Hyper-Viral aktif ({age_minutes:.0f}m, Vol5m ${pair.vol_m5:,.0f}, Vol1h ${pair.vol_h1:,.0f}) — wajib >=750 holders & 0 risks di L1."
                if is_hyper_viral_fasttrack
                else (
                    f"Sudah bertahan {age_minutes:.0f} menit pasca-migrasi (melewati gelombang dump sniper awal)."
                    if age_ok
                    else f"Terlalu baru ({age_minutes:.0f}m < {cfg.min_pair_age_minutes:.0f}m) — tunggu lolos dump sniper pertama & membentuk base."
                )
            ),
        )
    )

    # 3. Parabolic Candle 5m/1h Cap (v4: Viral Velocity menyerahkan uji pucuk vs pullback ke Layer 2B OHLCV!)
    is_proven_runner = age_minutes >= 120.0 and tx_24h >= 1_200
    effective_max_h1 = cfg.max_h1_change_pct if is_proven_runner else cfg.max_fresh_h1_change_pct
    effective_max_m5 = 18.0 if is_corridor_d else cfg.max_m5_change_pct
    h1_floor = -12.0 if (is_daily_coin and p_m5 >= 1.5) else -10.0

    m5_falling = p_m5 < -3.5
    h1_falling = p_h1 < h1_floor
    m5_too_hot = p_m5 > effective_max_m5
    # Kunci v4: koin viral ($Zero di $244K/$267K dgn +740% 1h & Vol5m $50K-$110K) TIDAK langsung dibunuh oleh cap 1h kaku;
    # melainkan diloloskan ke Layer 2B OHLCV 5m untuk membedakan Pullback/Base sehat vs Blow-Off Top!
    h1_too_hot = (p_h1 > effective_max_h1) and (not is_viral_velocity)
    parabolic_ok = (not m5_falling) and (not h1_falling) and (not m5_too_hot) and (not h1_too_hot)

    if parabolic_ok and is_viral_velocity and p_h1 > effective_max_h1:
        parabolic_detail = (
            f"Ledakan viral terdeteksi (1h {p_h1:+.0f}%, Vol5m ${pair.vol_m5:,.0f}, 5m {p_m5:+.1f}%) — "
            "validasi pucuk vs pullback dialihkan ke struktur candle OHLCV 5m."
        )
    elif parabolic_ok:
        parabolic_detail = "Kecepatan harga sehat (belum pucuk vertikal dan tidak sedang jatuh)."
    elif m5_falling or h1_falling:
        parabolic_detail = (
            f"DITOLAK: Tren sedang jatuh/sekarat (5m {p_m5:+.1f}%, 1h {p_h1:+.1f}%) — jangan beli pisau jatuh."
        )
    else:
        parabolic_detail = (
            f"Sudah terlalu tinggi tanpa ledakan volume viral (+{p_h1:.0f}% 1h / +{p_m5:.0f}% 5m) — tunggu pullback sehat."
        )

    checks.append(
        GateCheck(
            id="parabolic_cap",
            layer="ANTI_TOP",
            label="Kontrol Kecepatan 5m/1h & Delegasi Viral ke OHLCV",
            passed=parabolic_ok,
            severity="PASS" if parabolic_ok else ("VETO" if (m5_falling or h1_falling) else "WAIT_DIP"),
            actual=f"5m {p_m5:+.1f}% · 1h {p_h1:+.1f}%" + (" (VIRAL)" if is_viral_velocity else ""),
            threshold=f"5m [-3.5%, +{effective_max_m5:.0f}%] · 1h <= +{effective_max_h1:.0f}% (atau Viral OHLCV)",
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
    else:
        req_vol_m5 = cfg.min_vol_m5_usd
        req_vol_h1 = cfg.min_vol_h1_usd
        req_turnover_h1 = cfg.min_turnover_h1_ratio

    vol_m5_ok = pair.vol_m5 >= req_vol_m5 and tx_5m >= 8
    vol_h1_ok = (age_minutes < 15.0) or (
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

    effective_min_tx = compute_effective_min_tx_24h(age_minutes)
    tx_count_ok = tx_24h >= effective_min_tx
    ticket_1h_ok = (tx_1h == 0) or (avg_ticket_1h >= 20.0)

    flow_ok = (
        vol_m5_ok
        and vol_h1_ok
        and (not vol_dying)
        and (not wash_bot_5m)
        and (not sideways_churn)
        and tx_count_ok
        and ticket_1h_ok
    )
    if wash_bot_5m:
        flow_detail = (
            f"DITOLAK: Terdeteksi wash-bot/micro-bot 5m ({tx_5m} tx hanya ${pair.vol_m5:,.0f} = ${avg_ticket_5m:.1f}/tx)."
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

    # 7. Tekanan Beli, Whale Absorption & Akumulasi 6h (Anti-Dead-Cat Bounce pada koin distribusi!)
    whale_siphon = pair.buys_m5 >= 12 and buy_ratio_5m >= 62.0 and p_m5 < -1.0
    whale_absorption = (
        p_m5 >= 2.0 and 42.0 <= buy_ratio_5m < 48.0 and buy_ratio_1h >= 48.0
    )
    h6_sell_bleed = age_minutes >= 360.0 and tx_6h >= 20 and buy_ratio_6h < 47.0

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
            f"DITOLAK: Distribusi multi-jam (6h buy hanya {buy_ratio_6h:.0f}%) — rawan dead-cat bounce lalu turun."
        )
    elif p_m5 >= 2.0:
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
        siphon_ok = buy_ratio_5m >= 50.0 and buy_ratio_1h >= 49.0
        siphon_sev = "PASS" if siphon_ok else "WAIT_DIP"
        siphon_detail = (
            "Pullback ringan masih diserap buyer."
            if siphon_ok
            else "Harga 5m minus disertai dominasi jual — tunggu buyer kembali memegang kendali."
        )
    else:
        siphon_ok = buy_ratio_5m >= 48.0 and buy_ratio_1h >= 49.0
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
            label="Tekanan Beli, Whale Absorption & Akumulasi 6h",
            passed=siphon_ok,
            severity=siphon_sev,
            actual=f"5m {buy_ratio_5m:.0f}% · 1h {buy_ratio_1h:.0f}% · 6h {buy_ratio_6h:.0f}% buy",
            threshold=">=48% buy (atau >=42% Whale Absorption), 6h>=47% & anti-siphon",
            detail=siphon_detail,
        )
    )

    # 8. Pre-ATH Extension Guard (Khusus koin berusia >= 6 jam: jangan masuk dekat ATH lokal)
    is_near_local_ath = age_minutes >= 360.0 and (p_h6 > 160.0 or p_h24 > 260.0)
    pre_ath_ok = not is_near_local_ath
    checks.append(
        GateCheck(
            id="pre_ath_extension",
            layer="ANTI_TOP",
            label="Pre-ATH Extension Guard (Koin Berusia >= 6 Jam)",
            passed=pre_ath_ok,
            severity="PASS" if pre_ath_ok else "WAIT_DIP",
            actual=f"6h {p_h6:+.1f}% · 24h {p_h24:+.1f}% (usia {age_minutes / 60.0:.1f}j)",
            threshold="6h <= +160% & 24h <= +260% (sebelum zona ATH lokal)",
            detail=(
                "Masih di fase awal sebelum zona ATH — ruang ambil profit puluhan persen tetap terbuka."
                if pre_ath_ok
                else "Koin tua sudah naik terlalu tinggi dalam 6–24 jam terakhir (dekat ATH lokal) — tunggu base konsolidasi baru."
            ),
        )
    )

    # 9. Deteksi Sleeper Revival / Daily Consolidation Candidate (usia >= 12 jam yg baru bangun)
    is_early_pre_ath_wave = (
        0.5 <= p_m5 <= 22.0
        and -2.0 <= p_h1 <= 85.0
        and p_h6 <= 120.0
        and p_h24 <= 220.0
    )
    has_revival_buyers = (
        buy_ratio_5m >= 50.0 and buy_ratio_1h >= 50.0 and buy_ratio_6h >= 49.0 and pair.buys_m5 >= 5
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
    )
