"""Layer 2B — Real OHLCV Candle Structure & Daily Consolidation Classifier.

Menganalisis candle OHLCV asli dari GeckoTerminal untuk 2 tujuan kritis:
  1. Koin Intraday (< 20 jam): memblokir pucuk blow-off (upper wick panjang,
     volume exhaustion / bensin habis, dan breakdown lower-low) sebelum alert dikirim.
  2. Koin Berumur Harian (>= 20 jam): membedakan secara matematis antara
     KONSOLIDASI AKUMULASI (Akan Naik: box ketat, Higher Low, posisi harga di 1/3 atas
     box, volume hijau dominan, wick atas bersih, volume bangun) vs
     KONSOLIDASI DISTRIBUSI / BLEED (Akan Turun: Lower High, posisi bawah box,
     volume merah dominan, wick atas panjang, support jebol, 6h sell bleed).
"""

from __future__ import annotations

import asyncio
from statistics import median
from typing import Any, Optional
import httpx

from .config import BotConfig
from .models import AntiTopResult, GateCheck, PairSnapshot, StructureRegime, StructureResult


async def fetch_pool_ohlcv(
    client: httpx.AsyncClient,
    pair_address: str,
    timeframe: str = "minute",
    aggregate: int = 5,
    limit: int = 24,
) -> list[list[float]]:
    """Tarik candle OHLCV [ts, open, high, low, close, volume] terurut kronologis (lama -> baru)."""
    addr = (pair_address or "").strip()
    if len(addr) < 32:
        return []
    url = (
        f"https://api.geckoterminal.com/api/v2/networks/solana/pools/{addr}"
        f"/ohlcv/{timeframe}?aggregate={aggregate}&limit={limit}"
    )
    try:
        resp = await client.get(url, timeout=3.8)
        if resp.status_code != 200:
            return []
        payload: Any = resp.json()
        raw = (((payload.get("data") or {}).get("attributes") or {}).get("ohlcv_list")) or []
        candles: list[list[float]] = []
        for row in raw:
            if isinstance(row, (list, tuple)) and len(row) >= 6:
                candles.append([float(x) for x in row[:6]])
        # GeckoTerminal mengembalikan urutan terbaru -> terlama; balikkan menjadi kronologis
        candles.sort(key=lambda c: c[0])
        return candles
    except Exception:
        return []


def _upper_wick_ratio(c: list[float]) -> float:
    _, o, h, l, cl, _ = c
    rng = h - l
    if rng <= 1e-12:
        return 0.0
    body_top = max(o, cl)
    return max(0.0, (h - body_top) / rng)


def _evaluate_intraday_ohlcv(
    pair: PairSnapshot,
    anti_top: AntiTopResult,
    candles_5m: list[list[float]],
    cfg: BotConfig,
) -> StructureResult:
    checks: list[GateCheck] = []

    if len(candles_5m) < 4:
        # Fallback ketat berbasis snapshot ketika OHLCV tidak tersedia (anti-chase pucuk)
        safe_snapshot = (
            anti_top.age_minutes >= max(cfg.min_pair_age_minutes, 25.0)
            and anti_top.vol_m5_accel_ratio >= 0.65
            and anti_top.p_h1 <= cfg.max_fresh_h1_change_pct
            and anti_top.p_m5 >= -1.0
        )
        checks.append(
            GateCheck(
                id="ohlcv_intraday_fallback",
                layer="STRUCTURE",
                label="Verifikasi Struktur Candle 5m (Fallback)",
                passed=safe_snapshot,
                severity="PASS" if safe_snapshot else "WAIT_DIP",
                actual=f"OHLCV N/A · accel {anti_top.vol_m5_accel_ratio:.2f}x · 1h {anti_top.p_h1:+.0f}%",
                threshold="OHLCV 5m >= 4 candle (atau usia>=25m, accel>=0.65x, 1h<=115%)",
                detail=(
                    "Data candle 5m sementara kosong, namun profil momentum snapshot masih terukur."
                    if safe_snapshot
                    else "Data candle 5m belum cukup/kosong pada token berisiko pucuk — tunda entry."
                ),
            )
        )
        return StructureResult(
            passed=safe_snapshot,
            wait_dip_only=not safe_snapshot,
            regime="UNVERIFIED",
            ohlcv_verified=False,
            checks=tuple(checks),
            vol_expansion_ratio=anti_top.vol_m5_accel_ratio,
            summary="OHLCV 5m tidak tersedia (mode fallback konservatif).",
        )

    closes = [c[4] for c in candles_5m]
    highs = [c[2] for c in candles_5m]
    lows = [c[3] for c in candles_5m]
    vols = [c[5] for c in candles_5m]

    current_close = closes[-1]
    peak_high = max(highs)
    drawdown_from_peak_pct = (
        max(0.0, (peak_high - current_close) / peak_high * 100.0)
        if peak_high > 0
        else 0.0
    )

    # 1. Deteksi Blow-Off Upper Wick Rejection (pucuk tertolak sniper/whale)
    recent_3 = candles_5m[-3:]
    meaningful_wicks = [
        _upper_wick_ratio(c)
        for c in recent_3
        if c[3] > 0 and ((c[2] - c[3]) / c[3]) >= 0.035
    ]
    max_recent_wick_pct = (max(meaningful_wicks) * 100.0) if meaningful_wicks else 0.0
    latest_o, _, _, _, latest_c, _ = candles_5m[-1]
    latest_red_dump_pct = ((latest_c - latest_o) / latest_o * 100.0) if latest_o > 0 else 0.0

    blowoff_wick_veto = (
        (max_recent_wick_pct >= 48.0 and drawdown_from_peak_pct >= 10.0)
        or (drawdown_from_peak_pct >= 24.0 and latest_red_dump_pct <= -3.0)
        or (latest_red_dump_pct <= -8.0)
    )
    checks.append(
        GateCheck(
            id="blowoff_wick_guard",
            layer="STRUCTURE",
            label="Anti-Blow-Off Top & Wick Rejection (5m OHLCV)",
            passed=not blowoff_wick_veto,
            severity="PASS" if not blowoff_wick_veto else "VETO",
            actual=f"Wick atas {max_recent_wick_pct:.0f}% · DD dr peak -{drawdown_from_peak_pct:.1f}%",
            threshold="Wick < 48% di pucuk & tidak ada candle dump >= -8%",
            detail=(
                "Struktur candle 5m bersih tanpa jarum penolakan pucuk (blow-off top)."
                if not blowoff_wick_veto
                else f"DITOLAK: Terdeteksi pucuk blow-off / penolakan keras (wick atas {max_recent_wick_pct:.0f}%, turun -{drawdown_from_peak_pct:.1f}% dari puncak)."
            ),
        )
    )

    # 2. Deteksi Volume Exhaustion di Pucuk (bensin habis setelah lonjakan 1h)
    window_vols = vols[-12:]
    peak_candle_vol = max(window_vols) if window_vols else 0.0
    recent_2_avg_vol = sum(vols[-2:]) / 2.0
    recent_to_peak_ratio = (
        (recent_2_avg_vol / peak_candle_vol) if peak_candle_vol > 0 else 1.0
    )
    prior_vols = vols[-6:-1] if len(vols) >= 6 else vols[:-1]
    med_prior_vol = median(prior_vols) if prior_vols else recent_2_avg_vol
    vol_expansion = (vols[-1] / med_prior_vol) if med_prior_vol > 0 else 1.0

    # Jika harga sudah melesat > +65% 1h dan masih di dekat puncak (DD <= 15%),
    # namun volume 2 candle terakhir sudah anjlok < 32% dari candle puncaknya -> Bensin Habis!
    vol_exhaustion_top = (
        anti_top.p_h1 >= 65.0
        and drawdown_from_peak_pct <= 15.0
        and recent_to_peak_ratio < 0.32
        and vol_expansion < 1.15
    )
    checks.append(
        GateCheck(
            id="volume_exhaustion_guard",
            layer="STRUCTURE",
            label="Anti-Volume Exhaustion (Bensin Habis di Pucuk)",
            passed=not vol_exhaustion_top,
            severity="PASS" if not vol_exhaustion_top else "VETO",
            actual=f"Vol/Peak {recent_to_peak_ratio * 100:.0f}% · Ekspansi {vol_expansion:.2f}x",
            threshold="Vol terbaru >= 32% peak candle saat extended",
            detail=(
                f"Volume 5m masih terjaga/ekspansi ({recent_to_peak_ratio * 100:.0f}% dari puncak)."
                if not vol_exhaustion_top
                else f"DITOLAK: Volume Exhaustion di pucuk — harga masih tinggi (+{anti_top.p_h1:.0f}% 1h) tapi volume anjlok ke {recent_to_peak_ratio * 100:.0f}% dari puncaknya (siap dump)."
            ),
        )
    )

    # 3. Integritas Support & Higher-Low (jangan beli candle yang baru jebol support 30m terakhir!)
    prior_slice = candles_5m[-7:-1] if len(candles_5m) >= 7 else candles_5m[:-1]
    prior_support_low = min(c[3] for c in prior_slice) if prior_slice else 0.0
    support_broken = prior_support_low > 0 and current_close < prior_support_low * 0.99

    mid = len(candles_5m) // 2
    first_half_low = min(lows[:mid]) if mid > 0 else lows[0]
    second_half_low = min(lows[mid:]) if mid > 0 else lows[-1]
    higher_low_intact = first_half_low > 0 and second_half_low >= first_half_low * 0.95

    # Hitung dominasi volume candle hijau pada 6 candle terakhir
    last_6 = candles_5m[-6:]
    total_v6 = sum(c[5] for c in last_6)
    green_v6 = sum(c[5] for c in last_6 if c[4] >= c[1])
    green_vol_share_pct = (green_v6 / total_v6 * 100.0) if total_v6 > 0 else 50.0

    structure_broken = support_broken or (not higher_low_intact and green_vol_share_pct < 42.0)
    checks.append(
        GateCheck(
            id="intraday_higher_low",
            layer="STRUCTURE",
            label="Integritas Support & Higher-Low 5m",
            passed=not structure_broken,
            severity="PASS" if not structure_broken else "VETO",
            actual=f"HL={'Ya' if higher_low_intact else 'Tidak'} · GreenVol {green_vol_share_pct:.0f}%",
            threshold="Close di atas support 30m & tidak lower-low dengan volume merah",
            detail=(
                "Support intraday terjaga dengan struktur Higher-Low / penyerapan sehat."
                if not structure_broken
                else "DITOLAK: Struktur support 5m jebol (Lower-Low / distribusi volume merah) — rawan dump lanjutan."
            ),
        )
    )

    passed = all(c.passed for c in checks)
    has_veto = any((not c.passed) and c.severity == "VETO" for c in checks)
    if blowoff_wick_veto or vol_exhaustion_top:
        regime: StructureRegime = "EXHAUSTION_TOP"
        summary = f"Pucuk blow-off/exhaustion (wick {max_recent_wick_pct:.0f}%, vol/peak {recent_to_peak_ratio * 100:.0f}%)."
    elif structure_broken:
        regime = "BREAKDOWN_DUMP"
        summary = f"Support 5m jebol (GreenVol {green_vol_share_pct:.0f}%)."
    elif drawdown_from_peak_pct >= 6.0 and higher_low_intact:
        regime = "PULLBACK_RECLAIM"
        summary = f"Pullback Reclaim sehat (HL terjaga, DD -{drawdown_from_peak_pct:.1f}%, GreenVol {green_vol_share_pct:.0f}%)."
    else:
        regime = "CONTINUATION_BREAKOUT"
        summary = f"Continuation Breakout sehat (Wick {max_recent_wick_pct:.0f}%, GreenVol {green_vol_share_pct:.0f}%)."

    return StructureResult(
        passed=passed,
        wait_dip_only=(not passed) and (not has_veto),
        regime=regime,
        ohlcv_verified=True,
        checks=tuple(checks),
        upper_wick_pct=round(max_recent_wick_pct, 1),
        drawdown_from_peak_pct=round(drawdown_from_peak_pct, 1),
        vol_expansion_ratio=round(vol_expansion, 2),
        base_range_pct=0.0,
        range_position_pct=round(max(0.0, 100.0 - drawdown_from_peak_pct), 1),
        green_vol_share_pct=round(green_vol_share_pct, 1),
        higher_low_intact=higher_low_intact,
        summary=summary,
    )


def _evaluate_daily_consolidation_ohlcv(
    pair: PairSnapshot,
    anti_top: AntiTopResult,
    candles_1h: list[list[float]],
    candles_5m: list[list[float]],
) -> StructureResult:
    """
    Klasifikasi koin berumur harian (>= 20 jam):
    Membedakan KONSOLIDASI AKUMULASI (Akan Naik) vs KONSOLIDASI DISTRIBUSI / BLEED (Akan Turun).
    """
    checks: list[GateCheck] = []

    if len(candles_1h) < 8:
        # Fallback jika data 1h belum cukup: cek multi-timeframe 6h/1h/5m
        bullish_snap = (
            anti_top.buy_ratio_6h_pct >= 51.0
            and anti_top.buy_ratio_1h_pct >= 51.0
            and anti_top.p_h6 >= -10.0
            and anti_top.p_h6 <= 65.0
            and anti_top.p_m5 >= 1.5
            and anti_top.vol_wake_5m_ratio >= 2.5
        )
        bearish_snap = anti_top.buy_ratio_6h_pct < 47.0 or anti_top.p_h6 < -18.0
        checks.append(
            GateCheck(
                id="daily_consolidation_fallback",
                layer="STRUCTURE",
                label="Klasifikasi Konsolidasi Harian (Fallback)",
                passed=bullish_snap,
                severity="PASS" if bullish_snap else ("VETO" if bearish_snap else "WAIT_DIP"),
                actual=f"6h {anti_top.p_h6:+.1f}% ({anti_top.buy_ratio_6h_pct:.0f}% buy) · Wake {anti_top.vol_wake_5m_ratio:.1f}x",
                threshold="6h buy>=51%, 6h [-10%,+65%], Vol Wake>=2.5x",
                detail=(
                    "Konsolidasi harian bangun (terverifikasi multi-timeframe snapshot)."
                    if bullish_snap
                    else (
                        "DITOLAK: Tekanan jual 6h dominan — pola distribusi turun."
                        if bearish_snap
                        else "Koin harian masih tidur/sideways — tunggu konfirmasi breakout."
                    )
                ),
            )
        )
        return StructureResult(
            passed=bullish_snap,
            wait_dip_only=(not bullish_snap) and (not bearish_snap),
            regime=(
                "BULLISH_CONSOLIDATION_BREAKOUT"
                if bullish_snap
                else ("BEARISH_DISTRIBUTION" if bearish_snap else "BULLISH_CONSOLIDATION_WAITING")
            ),
            ohlcv_verified=False,
            checks=tuple(checks),
            vol_expansion_ratio=anti_top.vol_wake_5m_ratio,
            summary="Klasifikasi konsolidasi harian (mode snapshot fallback).",
        )

    # Pisahkan base konsolidasi (candle 1h sebelumnya) vs candle breakout saat ini
    base = candles_1h[:-1]
    latest = candles_1h[-1]
    current_close = latest[4]

    base_highs = [c[2] for c in base]
    base_lows = [c[3] for c in base]
    base_vols = [c[5] for c in base]

    base_high = max(base_highs)
    base_low = min(base_lows)
    base_range_pct = ((base_high - base_low) / base_low * 100.0) if base_low > 0 else 999.0
    box_span = max(base_high - base_low, 1e-12)
    range_position_pct = (current_close - base_low) / box_span * 100.0

    # Belah base menjadi paruh pertama vs paruh kedua untuk mendeteksi Ascending Base vs Descending Trap
    mid = len(base) // 2
    first_half = base[:mid]
    second_half = base[mid:]
    first_low = min(c[3] for c in first_half)
    second_low = min(c[3] for c in second_half)
    first_high = max(c[2] for c in first_half)
    second_high = max(c[2] for c in second_half)

    higher_low_intact = first_low > 0 and second_low >= first_low * 0.96
    lower_high_trap = first_high > 0 and second_high < first_high * 0.90
    support_broken = base_low > 0 and current_close < base_low * 0.99

    # Dominasi volume hijau vs merah pada 8 jam terakhir (Akumulasi vs Distribusi!)
    recent_8h = candles_1h[-8:]
    total_v8 = sum(c[5] for c in recent_8h)
    green_v8 = sum(c[5] for c in recent_8h if c[4] >= c[1])
    green_vol_share_pct = (green_v8 / total_v8 * 100.0) if total_v8 > 0 else 50.0

    # Rata-rata wick atas 4 jam terakhir (Overhead Supply / Whale jual di setiap pantulan)
    last_4h = candles_1h[-4:]
    avg_upper_wick_pct = (sum(_upper_wick_ratio(c) for c in last_4h) / len(last_4h)) * 100.0

    # Ekspansi volume keluar dari base kering (1h terbaru atau proyeksi 5m x 12 vs median base 1h)
    med_base_vol = median(base_vols) if base_vols else 0.0
    run_rate_1h_from_5m = pair.vol_m5 * 12.0
    active_vol = max(latest[5], pair.vol_h1, run_rate_1h_from_5m)
    vol_expansion_ratio = (active_vol / med_base_vol) if med_base_vol > 0 else 1.0

    # Cek apakah 5m terakhir menunjukkan blow-off wick pada koin harian
    m5_blowoff = False
    if len(candles_5m) >= 3:
        m5_wicks = [
            _upper_wick_ratio(c) * 100.0
            for c in candles_5m[-2:]
            if c[3] > 0 and ((c[2] - c[3]) / c[3]) >= 0.04
        ]
        if m5_wicks and max(m5_wicks) >= 52.0 and anti_top.p_m5 < 0:
            m5_blowoff = True

    # ---- DETEKSI KONSOLIDASI AKAN TURUN (BEARISH DISTRIBUTION -> HARD VETO!) ----
    is_bearish_distribution = (
        support_broken
        or (lower_high_trap and range_position_pct < 45.0)
        or (green_vol_share_pct < 44.0)
        or (avg_upper_wick_pct >= 42.0)
        or (anti_top.buy_ratio_6h_pct < 47.0)
        or m5_blowoff
    )

    bearish_reasons: list[str] = []
    if support_broken:
        bearish_reasons.append("support base jebol")
    if lower_high_trap and range_position_pct < 45.0:
        bearish_reasons.append("Lower-High & harga di bawah box (segitiga turun)")
    if green_vol_share_pct < 44.0:
        bearish_reasons.append(f"volume merah dominan (GreenVol hanya {green_vol_share_pct:.0f}%)")
    if avg_upper_wick_pct >= 42.0:
        bearish_reasons.append(f"overhead supply berat (wick atas {avg_upper_wick_pct:.0f}%)")
    if anti_top.buy_ratio_6h_pct < 47.0:
        bearish_reasons.append(f"tekanan jual 6h dominan ({anti_top.buy_ratio_6h_pct:.0f}% buy)")
    if m5_blowoff:
        bearish_reasons.append("jarum penolakan 5m (false breakout)")

    checks.append(
        GateCheck(
            id="daily_accum_vs_dist",
            layer="STRUCTURE",
            label="Konsolidasi Harian: Akumulasi vs Distribusi",
            passed=not is_bearish_distribution,
            severity="PASS" if not is_bearish_distribution else "VETO",
            actual=f"GreenVol {green_vol_share_pct:.0f}% · Wick {avg_upper_wick_pct:.0f}% · 6hBuy {anti_top.buy_ratio_6h_pct:.0f}%",
            threshold="GreenVol >= 44%, Wick < 42%, 6hBuy >= 47%, Support utuh",
            detail=(
                f"Pola akumulasi bersih (GreenVol {green_vol_share_pct:.0f}%, Higher Low {'utuh' if higher_low_intact else 'datar'})."
                if not is_bearish_distribution
                else f"DITOLAK (Konsolidasi Akan Turun / Distribusi): {', '.join(bearish_reasons)}."
            ),
        )
    )

    # ---- SYARAT BREAKOUT KONSOLIDASI AKAN NAIK (BULLISH ACCUMULATION BREAKOUT) ----
    box_tight_ok = 6.0 <= base_range_pct <= 58.0
    upper_breakout_zone = 62.0 <= range_position_pct <= 125.0
    accum_strong_ok = higher_low_intact and green_vol_share_pct >= 52.0 and avg_upper_wick_pct < 36.0
    volume_wake_ok = vol_expansion_ratio >= 1.45 and anti_top.buy_ratio_6h_pct >= 50.0 and anti_top.buy_ratio_1h_pct >= 50.0

    bullish_breakout_ready = (
        (not is_bearish_distribution)
        and box_tight_ok
        and upper_breakout_zone
        and accum_strong_ok
        and volume_wake_ok
    )

    # Jika tidak bearish, tetapi belum memenuhi syarat breakout (masih di tengah box atau volume belum bangun)
    if is_bearish_distribution:
        breakout_sev = "VETO"
        breakout_detail = "Konsolidasi bersifat distribusi/turun — bukan setup akumulasi naik."
    elif bullish_breakout_ready:
        breakout_sev = "PASS"
        breakout_detail = (
            f"BREAKOUT AKUMULASI HARIAN: Box ketat {base_range_pct:.0f}%, Higher-Low utuh, "
            f"harga di posisi {range_position_pct:.0f}% box, volume ekspansi {vol_expansion_ratio:.2f}x."
        )
    elif range_position_pct > 125.0:
        breakout_sev = "WAIT_DIP"
        breakout_detail = f"Sudah terlanjur breakout jauh di atas box ({range_position_pct:.0f}%) — tunggu retest resistensi."
    elif not box_tight_ok:
        breakout_sev = "WAIT_DIP"
        breakout_detail = f"Rentang base terlalu liar/lebar ({base_range_pct:.0f}%) — bukan konsolidasi rapi."
    else:
        breakout_sev = "WAIT_DIP"
        breakout_detail = (
            f"Base harian sehat tapi belum konfirmasi breakout (posisi box {range_position_pct:.0f}%, "
            f"ekspansi vol {vol_expansion_ratio:.2f}x, GreenVol {green_vol_share_pct:.0f}%)."
        )

    checks.append(
        GateCheck(
            id="daily_breakout_trigger",
            layer="STRUCTURE",
            label="Trigger Breakout Konsolidasi Harian (1h OHLCV)",
            passed=bullish_breakout_ready,
            severity=breakout_sev,
            actual=f"Box {base_range_pct:.0f}% · Pos {range_position_pct:.0f}% · Vol {vol_expansion_ratio:.2f}x",
            threshold="Box 6–58%, Pos 62–125%, HL=Ya, GreenVol>=52%, Vol>=1.45x",
            detail=breakout_detail,
        )
    )

    if is_bearish_distribution:
        regime: StructureRegime = "BEARISH_DISTRIBUTION"
        summary = f"Konsolidasi Turun/Distribusi ({', '.join(bearish_reasons[:2])})."
    elif bullish_breakout_ready:
        regime = "BULLISH_CONSOLIDATION_BREAKOUT"
        summary = (
            f"Akumulasi Harian Breakout · Box {base_range_pct:.0f}% · Pos {range_position_pct:.0f}% · "
            f"GreenVol {green_vol_share_pct:.0f}% · Vol {vol_expansion_ratio:.1f}x"
        )
    else:
        regime = "BULLISH_CONSOLIDATION_WAITING"
        summary = f"Konsolidasi Harian Menunggu Breakout (Pos {range_position_pct:.0f}%, Vol {vol_expansion_ratio:.1f}x)."

    return StructureResult(
        passed=bullish_breakout_ready,
        wait_dip_only=(not bullish_breakout_ready) and (not is_bearish_distribution),
        regime=regime,
        ohlcv_verified=True,
        checks=tuple(checks),
        upper_wick_pct=round(avg_upper_wick_pct, 1),
        drawdown_from_peak_pct=round(max(0.0, (base_high - current_close) / base_high * 100.0) if base_high > 0 else 0.0, 1),
        vol_expansion_ratio=round(vol_expansion_ratio, 2),
        base_range_pct=round(base_range_pct, 1),
        range_position_pct=round(range_position_pct, 1),
        green_vol_share_pct=round(green_vol_share_pct, 1),
        higher_low_intact=higher_low_intact,
        summary=summary,
    )


def evaluate_candle_structure(
    pair: PairSnapshot,
    anti_top: AntiTopResult,
    candles_5m: list[list[float]],
    candles_1h: list[list[float]],
    cfg: BotConfig,
) -> StructureResult:
    """Router deterministik Layer 2B: Intraday (< 20 jam) vs Daily Consolidation (>= 20 jam)."""
    if anti_top.age_minutes >= 20.0 * 60.0:
        return _evaluate_daily_consolidation_ohlcv(pair, anti_top, candles_1h, candles_5m)
    return _evaluate_intraday_ohlcv(pair, anti_top, candles_5m, cfg)


async def fetch_and_evaluate_structure(
    client: httpx.AsyncClient,
    pair: PairSnapshot,
    anti_top: AntiTopResult,
    cfg: BotConfig,
) -> StructureResult:
    """Tarik OHLCV dari GeckoTerminal sesuai usia token lalu evaluasi struktur candle."""
    if anti_top.age_minutes >= 20.0 * 60.0:
        candles_1h = await fetch_pool_ohlcv(client, pair.pair_address, "hour", 1, 24)
        await asyncio.sleep(0.35)
        candles_5m = await fetch_pool_ohlcv(client, pair.pair_address, "minute", 5, 12)
        return evaluate_candle_structure(pair, anti_top, candles_5m, candles_1h, cfg)
    candles_5m = await fetch_pool_ohlcv(client, pair.pair_address, "minute", 5, 24)
    return evaluate_candle_structure(pair, anti_top, candles_5m, [], cfg)
