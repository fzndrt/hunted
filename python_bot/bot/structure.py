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
        # v5 Fallback ketat: JANGAN pernah lolosan koin tanpa OHLCV jika 5m tidak hijau kuat (>= +2%) & buyer dominan (>= 54%)
        safe_snapshot = (
            anti_top.p_m5 >= 2.0
            and anti_top.buy_ratio_5m_pct >= 54.0
            and anti_top.buy_ratio_1h_pct >= 53.0
            and (
                (anti_top.is_hyper_viral_fasttrack and pair.vol_m5 >= 35_000.0)
                or (
                    anti_top.age_minutes >= max(cfg.min_pair_age_minutes, 25.0)
                    and anti_top.is_viral_velocity
                    and pair.vol_m5 >= 20_000.0
                )
            )
        )
        checks.append(
            GateCheck(
                id="ohlcv_intraday_fallback",
                layer="STRUCTURE",
                label="Verifikasi Struktur Candle 5m (Fallback Ketat v5)",
                passed=safe_snapshot,
                severity="PASS" if safe_snapshot else "WAIT_DIP",
                actual=f"OHLCV N/A · 5m {anti_top.p_m5:+.1f}% · Buy5m {anti_top.buy_ratio_5m_pct:.0f}% · Vol5m ${pair.vol_m5:,.0f}",
                threshold="OHLCV 5m >= 4 candle (atau Viral/Fast-Track 5m>=+2% & Buy>=54%)",
                detail=(
                    "Data candle 5m belum penuh (pool sangat baru), namun momentum hijau & buyer dominance terverifikasi."
                    if safe_snapshot
                    else "Data candle 5m belum cukup/kosong — tunda entry sampai struktur chart OHLCV terbentuk."
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
            summary="OHLCV 5m tidak tersedia (mode fallback ketat v5).",
        )

    closes = [c[4] for c in candles_5m]
    highs = [c[2] for c in candles_5m]
    lows = [c[3] for c in candles_5m]
    vols = [c[5] for c in candles_5m]

    # Kompensasi partial candle terakhir yang baru jalan 1–2 menit
    effective_last_vol = max(vols[-1], pair.vol_m5) if vols[-1] < pair.vol_m5 * 0.65 else vols[-1]
    effective_vols = vols[:-1] + [effective_last_vol]

    current_close = closes[-1]
    peak_high = max(highs)
    drawdown_from_peak_pct = (
        max(0.0, (peak_high - current_close) / peak_high * 100.0)
        if peak_high > 0
        else 0.0
    )

    # Hitung 5m Intraday VWAP (12 candle terakhir = cost basis rata-rata pembeli aktif!)
    vwap_slice = candles_5m[-12:]
    vwap_vols = effective_vols[-12:]
    vwap_num = sum(((c[2] + c[3] + c[4]) / 3.0) * v for c, v in zip(vwap_slice, vwap_vols))
    vwap_den = sum(vwap_vols)
    vwap_price = (vwap_num / vwap_den) if vwap_den > 0 else current_close
    vwap_dev_pct = ((current_close - vwap_price) / vwap_price * 100.0) if vwap_price > 0 else 0.0

    # Pre-hitung Support, Higher-Low & GreenVol 4-bar / 6-bar
    prior_slice = candles_5m[-7:-1] if len(candles_5m) >= 7 else candles_5m[:-1]
    prior_support_low = min(c[3] for c in prior_slice) if prior_slice else 0.0
    support_broken = prior_support_low > 0 and current_close < prior_support_low * 0.99

    mid = len(candles_5m) // 2
    first_half_low = min(lows[:mid]) if mid > 0 else lows[0]
    second_half_low = min(lows[mid:]) if mid > 0 else lows[-1]
    higher_low_intact = first_half_low > 0 and second_half_low >= first_half_low * 0.95

    last_6 = candles_5m[-6:]
    last_6_vols = effective_vols[-6:]
    total_v6 = sum(last_6_vols)
    green_v6 = sum(v for c, v in zip(last_6, last_6_vols) if c[4] >= c[1])
    green_vol_share_pct = (green_v6 / total_v6 * 100.0) if total_v6 > 0 else 50.0

    last_4 = candles_5m[-4:]
    last_4_vols = effective_vols[-4:]
    total_v4 = sum(last_4_vols)
    green_v4 = sum(v for c, v in zip(last_4, last_4_vols) if c[4] >= c[1])
    green_vol_last4_pct = (green_v4 / total_v4 * 100.0) if total_v4 > 0 else 50.0

    _, latest_o, latest_h, latest_l, latest_c, _ = candles_5m[-1]
    _, prev_o, _, prev_l, prev_c, _ = candles_5m[-2]
    latest_body_pct = ((latest_c - latest_o) / latest_o * 100.0) if latest_o > 0 else 0.0
    prev_body_pct = ((prev_c - prev_o) / prev_o * 100.0) if prev_o > 0 else 0.0
    latest_range = max(0.0, latest_h - latest_l)
    latest_upper_wick_abs = max(0.0, latest_h - max(latest_o, latest_c))
    latest_body_abs = abs(latest_c - latest_o)
    latest_upper_wick_pct = (latest_upper_wick_abs / latest_range * 100.0) if latest_range > 0 else 0.0
    latest_wick_to_body = (
        (latest_upper_wick_abs / latest_body_abs)
        if latest_body_abs > 1e-18
        else (99.0 if latest_upper_wick_pct >= 35.0 else 0.0)
    )

    # 1. Deteksi Blow-Off Upper Wick, Shooting-Star & Post-Dump Rejection (v5.2: blokir pola CaveBaby tanpa bunuh Kibo/PAIR!)
    recent_2 = candles_5m[-2:]
    meaningful_wicks = [
        _upper_wick_ratio(c)
        for c in recent_2
        if c[3] > 0 and ((c[2] - c[3]) / c[3]) >= 0.035
    ]
    meaningful_red_wicks = [
        _upper_wick_ratio(c)
        for c in recent_2
        if c[4] < c[1] and c[3] > 0 and ((c[2] - c[3]) / c[3]) >= 0.035
    ]
    max_recent_wick_pct = (max(meaningful_wicks) * 100.0) if meaningful_wicks else 0.0
    max_red_wick_pct = (max(meaningful_red_wicks) * 100.0) if meaningful_red_wicks else 0.0

    # Viral Green Absorption (v5.2): candle terbaru & sebelumnya WAJIB HIJAU beruntun (bukan pantulan pasca dump merah!),
    # low candle terbaru tidak jebol low sebelumnya, bukan Shooting Star ekstrem (wick < 60% & W/B < 4.5x),
    # Higher-Low utuh, GreenVol >= 62%, Vol5m >= $25K, buyer dominan
    is_viral_green_absorption = (
        higher_low_intact
        and (not support_broken)
        and (latest_c >= latest_o)
        and (prev_c >= prev_o)
        and (latest_l >= prev_l * 0.99)
        and (latest_upper_wick_pct < 60.0)
        and (latest_wick_to_body < 4.5)
        and (green_vol_share_pct >= 62.0)
        and (pair.vol_m5 >= 25_000.0)
        and (anti_top.buy_ratio_5m_pct >= 52.0)
        and (anti_top.buy_ratio_1h_pct >= 52.0)
    )
    # v5.3 Bullish Engulfing Wick Invalidation: jika candle terbaru HIJAU kuat (>= +8%), menelan penuh body candle sebelumnya,
    # dan candle terbaru sendiri bersih (wick < 30%, W/B < 1.5x) usai shakeout ringan (prev > -10%),
    # maka wick lama dari candle sebelumnya (-2) sudah terserap (kasus VAULT #08) — tetap memblokir CaveBaby #07!
    prev_wick_engulfed = (
        latest_c > max(prev_o, prev_c)
        and latest_body_pct >= 8.0
        and latest_upper_wick_pct < 30.0
        and latest_wick_to_body < 1.5
        and prev_body_pct > -10.0
        and drawdown_from_peak_pct <= 15.0
    )
    effective_recent_wick_pct = latest_upper_wick_pct if prev_wick_engulfed else max_recent_wick_pct
    effective_red_wick_pct = 0.0 if prev_wick_engulfed else max_red_wick_pct

    wick_veto_hit = (
        (effective_red_wick_pct >= 48.0 and drawdown_from_peak_pct >= 10.0)
        or (effective_recent_wick_pct >= 68.0 and drawdown_from_peak_pct >= 12.0)
        if is_viral_green_absorption
        else (effective_recent_wick_pct >= 48.0 and drawdown_from_peak_pct >= 10.0)
    )
    # v5.2 Shooting-Star Rejection: jarum penolakan atas raksasa (>=55%) dengan rasio wick:body >=4.5x di area penurunan >=12%
    shooting_star_rejection = (
        latest_upper_wick_pct >= 55.0
        and latest_wick_to_body >= 4.5
        and drawdown_from_peak_pct >= 12.0
    )
    # v5.2 Post-Dump Dead-Cat Rejection: jika candle sebelumnya merah besar (<=-10%), pantulan berikutnya WAJIB bersih
    # (dilarang punya jarum penolakan atas >=40% atau sapu Lower-Low dengan body tipis < +8%)
    post_dump_rejection = (
        prev_body_pct <= -10.0
        and (
            latest_upper_wick_pct >= 40.0
            or (latest_l < prev_l * 0.99 and latest_body_pct < 8.0)
        )
    )

    # Pullback sehat viral boleh hingga -45% HANYA jika reclaim hijau kuat; non-viral dibatasi ketat <= -28%!
    max_allowed_dd = 45.0 if anti_top.is_viral_velocity else 28.0
    deep_crash_dump = drawdown_from_peak_pct >= max_allowed_dd
    blowoff_wick_veto = (
        wick_veto_hit
        or shooting_star_rejection
        or post_dump_rejection
        or (drawdown_from_peak_pct >= 20.0 and latest_body_pct <= -2.5)
        or (latest_body_pct <= -7.0)
        or deep_crash_dump
    )
    if (not blowoff_wick_veto) and is_viral_green_absorption and max_recent_wick_pct >= 48.0:
        wick_detail = (
            f"Penyerapan volatilitas viral sehat (2 candle HIJAU beruntun, GreenVol {green_vol_share_pct:.0f}%, "
            f"wick {latest_upper_wick_pct:.0f}%, W/B {latest_wick_to_body:.1f}x)."
        )
    elif not blowoff_wick_veto:
        wick_detail = "Struktur candle 5m bersih tanpa jarum penolakan pucuk (blow-off / shooting-star)."
    elif post_dump_rejection:
        wick_detail = (
            f"DITOLAK (Post-Dump Rejection): Pantulan gagal pasca candle dump {prev_body_pct:+.1f}% "
            f"(wick atas {latest_upper_wick_pct:.0f}%, W/B {latest_wick_to_body:.1f}x, DD -{drawdown_from_peak_pct:.1f}%) — rawan bantingan lanjutan."
        )
    elif shooting_star_rejection:
        wick_detail = (
            f"DITOLAK (Shooting-Star Rejection): Jarum penolakan atas ekstrem {latest_upper_wick_pct:.0f}% "
            f"(rasio wick:body {latest_wick_to_body:.1f}x, DD -{drawdown_from_peak_pct:.1f}%) — seller memukul jatuh di pucuk."
        )
    else:
        wick_detail = (
            f"DITOLAK: Terdeteksi pucuk blow-off / crash dalam "
            f"(wick atas {max_recent_wick_pct:.0f}%, turun -{drawdown_from_peak_pct:.1f}% dari puncak)."
        )

    checks.append(
        GateCheck(
            id="blowoff_wick_guard",
            layer="STRUCTURE",
            label="Anti-Blow-Off Top, Shooting-Star & Post-Dump Guard (5m OHLCV)",
            passed=not blowoff_wick_veto,
            severity="PASS" if not blowoff_wick_veto else "VETO",
            actual=f"Wick {latest_upper_wick_pct:.0f}% (W/B {latest_wick_to_body:.1f}x) · Prev {prev_body_pct:+.0f}% · DD -{drawdown_from_peak_pct:.1f}%",
            threshold=(
                f"Wick < 60% & W/B < 4.5x (2x Green Viral), DD <= -{max_allowed_dd:.0f}%"
                if is_viral_green_absorption
                else f"Wick < 48% (post-dump < 40%), bebas Shooting Star & dump >= -7%"
            ),
            detail=wick_detail,
        )
    )

    # 2. Deteksi Volume Exhaustion, Weak Double-Top di ATH & Anti-Late-ATH Multi-X Run (v5.1)
    window_vols = effective_vols[-12:]
    peak_candle_vol = max(window_vols) if window_vols else 0.0
    peak_vol_idx = window_vols.index(peak_candle_vol) if window_vols else 0
    recent_2_avg_vol = sum(effective_vols[-2:]) / 2.0
    recent_to_peak_ratio = (
        (recent_2_avg_vol / peak_candle_vol) if peak_candle_vol > 0 else 1.0
    )
    prior_vols = effective_vols[-6:-1] if len(effective_vols) >= 6 else effective_vols[:-1]
    med_prior_vol = median(prior_vols) if prior_vols else recent_2_avg_vol
    vol_expansion = (effective_last_vol / med_prior_vol) if med_prior_vol > 0 else 1.0

    # v5.1 Weak Double-Top Volume Divergence di ATH (kasus DEXDATA 07:48 WITA: gagal close di atas puncak lama $207.6K & vol anjlok jadi 22%!)
    # Kecualikan breakout baru yang bersih di atas puncak lama dengan ekspansi volume kuat (seperti duk: Close > prior peak & vol_expansion >= 1.5x).
    prior_peak_high = max(highs[:-1]) if len(highs) >= 2 else peak_high
    clean_expanding_breakout = (latest_c > prior_peak_high) and (vol_expansion >= 1.20) and (latest_c >= latest_o)
    weak_double_top_ath = (
        len(window_vols) >= 4
        and drawdown_from_peak_pct <= 10.0
        and peak_vol_idx < len(window_vols) - 1
        and recent_to_peak_ratio < 0.40
        and (not clean_expanding_breakout)
    )
    # v5.1 Anti-Late-ATH Multi-X Run: sudah melesat >= +500% (6x) dari dasar 5m window (atau >= +900% kapan saja) & sedang tepat di ATH (DD <= 12%)!
    window_low_5m = min(lows) if lows else 0.0
    run_from_low_5m_pct = (
        ((latest_c - window_low_5m) / window_low_5m * 100.0) if window_low_5m > 0 else 0.0
    )
    late_ath_run_veto = (
        drawdown_from_peak_pct <= 12.0
        and (
            (anti_top.age_minutes >= 40.0 and run_from_low_5m_pct >= 500.0)
            or (run_from_low_5m_pct >= 900.0)
        )
    )

    vol_exhaustion_top = (
        weak_double_top_ath
        or late_ath_run_veto
        or (
            anti_top.p_h1 >= 65.0
            and drawdown_from_peak_pct <= 15.0
            and recent_to_peak_ratio < 0.32
            and vol_expansion < 1.15
            and pair.vol_m5 < 25_000.0
        )
    )
    if weak_double_top_ath:
        exhaust_detail = (
            f"DITOLAK (Weak Double-Top di ATH): Harga menyentuh ulang puncak (DD -{drawdown_from_peak_pct:.1f}%), "
            f"tetapi volume retest anjlok ke {recent_to_peak_ratio * 100:.0f}% dari puncak sebelumnya (rawan bantingan keras)."
        )
    elif late_ath_run_veto:
        exhaust_detail = (
            f"DITOLAK (Late-ATH Run): Harga sudah melesat +{run_from_low_5m_pct:.0f}% dari dasar 5m dan sedang menempel ATH (DD -{drawdown_from_peak_pct:.1f}%) — terlalu telat, bukan entry awal."
        )
    elif vol_exhaustion_top:
        exhaust_detail = (
            f"DITOLAK: Volume Exhaustion di pucuk — harga masih tinggi (+{anti_top.p_h1:.0f}% 1h) tapi volume anjlok ke {recent_to_peak_ratio * 100:.0f}% dari puncaknya (siap dump)."
        )
    else:
        exhaust_detail = (
            f"Volume 5m masih terjaga/ekspansi ({recent_to_peak_ratio * 100:.0f}% dari puncak, ekspansi {vol_expansion:.2f}x · Run +{run_from_low_5m_pct:.0f}%)."
        )

    checks.append(
        GateCheck(
            id="volume_exhaustion_guard",
            layer="STRUCTURE",
            label="Anti-Weak-Double-Top, Late-ATH Run & Volume Exhaustion",
            passed=not vol_exhaustion_top,
            severity="PASS" if not vol_exhaustion_top else "VETO",
            actual=f"Vol/Peak {recent_to_peak_ratio * 100:.0f}% · Run +{run_from_low_5m_pct:.0f}% · DD -{drawdown_from_peak_pct:.1f}%",
            threshold="Retest ATH wajib Vol >= 40% peak · Bukan ATH setelah +500% run",
            detail=exhaust_detail,
        )
    )

    # 3. v5 ANTI-FREEFALL VWAP & POST-PEAK CASCADE GUARD (Memutus jebakan dead-cat bounce & grafik turun bebas!)
    # Pullback Reclaim viral (seperti $Zero di -40%): WAJIB candle terakhir hijau kuat (>= +2.5% body),
    # mencetak Higher-Low lokal terhadap candle sebelumnya (latest_l >= prev_l * 0.99), GreenVol 4-bar >= 52%, dan VWAP >= -18%!
    is_verified_viral_reclaim = (
        anti_top.is_viral_velocity
        and higher_low_intact
        and (not support_broken)
        and 8.0 <= drawdown_from_peak_pct <= 45.0
        and latest_body_pct >= 2.5
        and latest_l >= prev_l * 0.99
        and green_vol_last4_pct >= 52.0
        and vwap_dev_pct >= -18.0
        and max_recent_wick_pct < (68.0 if is_viral_green_absorption else 42.0)
    )

    # Jebakan Freefall #1: Harga tenggelam di bawah VWAP 5m (mayoritas pembeli rugi & siap jual di setiap pantulan)
    under_vwap_freefall = (vwap_dev_pct < -6.0) and (not is_verified_viral_reclaim)
    # Jebakan Freefall #2: 2 candle 5m terakhir KEDUANYA MERAH (sedang meluncur turun!)
    two_red_cascade = (latest_c < latest_o) and (prev_c < prev_o)
    # Jebakan Freefall #3: Dead-cat micro-bounce setelah distribusi merah (DD >= 12% & GreenVol 4-bar < 48%)
    dead_cat_red_dist = (
        drawdown_from_peak_pct >= 12.0
        and green_vol_last4_pct < 48.0
        and (not is_verified_viral_reclaim)
    )
    # Jebakan Freefall #4: Post-peak Lower-Low & Lower-High cascade (candle terbaru merah jebol low candle sebelumnya saat DD >= 10%)
    post_peak_ll_dump = (
        drawdown_from_peak_pct >= 10.0
        and latest_c < latest_o
        and latest_l < prev_l * 0.995
    )

    freefall_veto = (
        under_vwap_freefall
        or two_red_cascade
        or dead_cat_red_dist
        or post_peak_ll_dump
    )
    freefall_reasons: list[str] = []
    if under_vwap_freefall:
        freefall_reasons.append(f"harga tenggelam {vwap_dev_pct:.1f}% di bawah VWAP 5m (jebakan bagholder)")
    if two_red_cascade:
        freefall_reasons.append("2 candle 5m beruntun merah (sedang meluncur turun)")
    if dead_cat_red_dist:
        freefall_reasons.append(f"dead-cat bounce volume merah (GreenVol 4-bar hanya {green_vol_last4_pct:.0f}%)")
    if post_peak_ll_dump:
        freefall_reasons.append("candle merah mencetak Lower-Low baru pasca-puncak")

    checks.append(
        GateCheck(
            id="anti_freefall_vwap",
            layer="STRUCTURE",
            label="Anti-Freefall 5m VWAP & Post-Peak Cascade Guard",
            passed=not freefall_veto,
            severity="PASS" if not freefall_veto else "VETO",
            actual=f"VWAP {vwap_dev_pct:+.1f}% · GreenVol4 {green_vol_last4_pct:.0f}% · Body {latest_body_pct:+.1f}%",
            threshold="Di atas/dekat VWAP (>= -6%), GreenVol4 >= 48%, bebas 2-red cascade",
            detail=(
                f"Reclaim Pullback Viral terverifikasi (body {latest_body_pct:+.1f}%, GreenVol4 {green_vol_last4_pct:.0f}%, VWAP {vwap_dev_pct:+.1f}%)."
                if is_verified_viral_reclaim and not freefall_veto
                else (
                    f"Tren sehat di atas/dekat VWAP 5m ({vwap_dev_pct:+.1f}%, GreenVol4 {green_vol_last4_pct:.0f}%)."
                    if not freefall_veto
                    else f"DITOLAK (Anti-Freefall): {', '.join(freefall_reasons)}."
                )
            ),
        )
    )

    # 4. Integritas Support & Higher-Low
    is_healthy_pullback_reclaim = is_verified_viral_reclaim or (
        higher_low_intact
        and (not support_broken)
        and (not freefall_veto)
        and 6.0 <= drawdown_from_peak_pct <= 28.0
        and latest_c >= latest_o
        and green_vol_last4_pct >= 52.0
        and (max_recent_wick_pct < (68.0 if is_viral_green_absorption else 42.0))
    )

    structure_broken = support_broken or (
        (not higher_low_intact) and (green_vol_share_pct < 48.0) and (not is_healthy_pullback_reclaim)
    )
    checks.append(
        GateCheck(
            id="intraday_higher_low",
            layer="STRUCTURE",
            label="Integritas Support & Higher-Low 5m",
            passed=not structure_broken,
            severity="PASS" if not structure_broken else "VETO",
            actual=f"HL={'Ya' if higher_low_intact else 'Tidak'} · GreenVol6 {green_vol_share_pct:.0f}%",
            threshold="Close di atas support 30m & tidak lower-low dengan volume merah",
            detail=(
                "Pullback Higher-Low Reclaim sehat (seller terserap di atas support utama)."
                if is_healthy_pullback_reclaim and not structure_broken
                else (
                    "Support intraday terjaga dengan struktur Higher-Low / penyerapan sehat."
                    if not structure_broken
                    else "DITOLAK: Struktur support 5m jebol (Lower-Low / distribusi volume merah) — rawan dump lanjutan."
                )
            ),
        )
    )

    passed = all(c.passed for c in checks)
    has_veto = any((not c.passed) and c.severity == "VETO" for c in checks)
    if under_vwap_freefall and not (wick_veto_hit or shooting_star_rejection or vol_exhaustion_top):
        regime: StructureRegime = "BREAKDOWN_DUMP"
        summary = (
            f"Freefall/Breakdown ({freefall_reasons[0]})"
            if freefall_reasons
            else f"Freefall di bawah VWAP 5m ({vwap_dev_pct:+.1f}%)."
        )
    elif blowoff_wick_veto or vol_exhaustion_top:
        regime = "EXHAUSTION_TOP"
        summary = f"Pucuk blow-off/exhaustion (wick {max_recent_wick_pct:.0f}%, vol/peak {recent_to_peak_ratio * 100:.0f}%)."
    elif freefall_veto or structure_broken:
        regime = "BREAKDOWN_DUMP"
        summary = (
            f"Freefall/Breakdown ({freefall_reasons[0]})"
            if freefall_reasons
            else f"Support 5m jebol (GreenVol {green_vol_share_pct:.0f}%)."
        )
    elif is_healthy_pullback_reclaim or (drawdown_from_peak_pct >= 6.0 and higher_low_intact):
        regime = "PULLBACK_RECLAIM"
        summary = f"Pullback Reclaim sehat (VWAP {vwap_dev_pct:+.1f}%, DD -{drawdown_from_peak_pct:.1f}%, GreenVol4 {green_vol_last4_pct:.0f}%)."
    else:
        regime = "CONTINUATION_BREAKOUT"
        summary = f"Continuation Breakout di atas VWAP ({vwap_dev_pct:+.1f}%, GreenVol4 {green_vol_last4_pct:.0f}%)."

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
        vwap_deviation_pct=round(vwap_dev_pct, 1),
        green_vol_last4_pct=round(green_vol_last4_pct, 1),
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


def aggregate_1m_to_3m_candles(candles_1m: list[list[float]]) -> list[list[float]]:
    """Gabungkan candle 1-menit menjadi bar 3-menit (atau 2-menit untuk koin 6–11m) agar pool muda tetap terverifikasi OHLCV."""
    if len(candles_1m) < 6:
        return []
    step = 3 if len(candles_1m) >= 12 else 2
    grouped: list[list[float]] = []
    for i in range(0, len(candles_1m) - (step - 1), step):
        chunk = candles_1m[i : i + step]
        grouped.append(
            [
                chunk[0][0],
                chunk[0][1],
                max(c[2] for c in chunk),
                min(c[3] for c in chunk),
                chunk[-1][4],
                sum(c[5] for c in chunk),
            ]
        )
    return grouped


async def fetch_and_evaluate_structure(
    client: httpx.AsyncClient,
    pair: PairSnapshot,
    anti_top: AntiTopResult,
    cfg: BotConfig,
) -> StructureResult:
    """Tarik OHLCV dari GeckoTerminal sesuai usia token lalu evaluasi struktur candle (cadangan 1m->2m/3m untuk SEMUA koin muda < 25m)."""
    if anti_top.age_minutes >= 20.0 * 60.0:
        candles_1h = await fetch_pool_ohlcv(client, pair.pair_address, "hour", 1, 24)
        await asyncio.sleep(0.35)
        candles_5m = await fetch_pool_ohlcv(client, pair.pair_address, "minute", 5, 12)
        return evaluate_candle_structure(pair, anti_top, candles_5m, candles_1h, cfg)
    candles_5m = await fetch_pool_ohlcv(client, pair.pair_address, "minute", 5, 24)
    if len(candles_5m) < 4 and (
        anti_top.age_minutes <= 25.0
        or anti_top.is_hyper_viral_fasttrack
        or anti_top.is_viral_velocity
    ):
        await asyncio.sleep(0.35)
        candles_1m = await fetch_pool_ohlcv(client, pair.pair_address, "minute", 1, 24)
        synth_early = aggregate_1m_to_3m_candles(candles_1m)
        if len(synth_early) >= 4:
            return evaluate_candle_structure(pair, anti_top, synth_early, [], cfg)
    return evaluate_candle_structure(pair, anti_top, candles_5m, [], cfg)
