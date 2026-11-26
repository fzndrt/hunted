"""Layer 1 — Binary Security Veto (Zero Bypass, 100% Fail-Closed)."""

from __future__ import annotations

from typing import Any, Optional
import httpx

from .config import (
    KNOWN_PROTOCOL_AUTHORITIES,
    METEORA_NFT_LP_SENTINEL,
    VERIFIED_HARD_QUOTE_MINTS,
    is_valid_solana_mint,
)
from .models import GateCheck, PairSnapshot, SecurityResult

DANGEROUS_TOKEN2022_EXTENSIONS: tuple[str, ...] = (
    "permanentdelegate",
    "transferhook",
    "transferfee",
    "defaultaccountstate",
    "nontransferable",
)


async def fetch_rugcheck_report(
    client: httpx.AsyncClient, mint: str
) -> Optional[dict[str, Any]]:
    if not is_valid_solana_mint(mint):
        return None
    url = f"https://api.rugcheck.xyz/v1/tokens/{mint}/report"
    try:
        resp = await client.get(url, timeout=4.5)
        if resp.status_code != 200:
            return None
        data = resp.json()
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def evaluate_security(
    pair: PairSnapshot,
    rugcheck_report: Optional[dict[str, Any]],
    now_ms: Optional[int] = None,
    require_hyper_viral_holders: bool = False,
    require_steady_climber_holders: bool = False,
) -> SecurityResult:
    checks: list[GateCheck] = []

    # 1. Strict Hard-Quote Mint Allowlist (ZERO ticker string / liq >= $6K bypass)
    quote_symbol = VERIFIED_HARD_QUOTE_MINTS.get(pair.quote_mint)
    hard_quote_ok = quote_symbol is not None
    checks.append(
        GateCheck(
            id="hard_quote_mint",
            layer="SECURITY",
            label="Hard-Quote Mint Allowlist",
            passed=hard_quote_ok,
            severity="PASS" if hard_quote_ok else "VETO",
            actual=f"{quote_symbol} ({pair.quote_mint[:6]}…)" if quote_symbol else f"UNVERIFIED ({pair.quote_symbol})",
            threshold="Mint resmi: SOL/USDC/USDT/WBTC/cbBTC/USD1/PUMP",
            detail=(
                f"Kolam dipasangkan dengan kontrak {quote_symbol} terverifikasi."
                if hard_quote_ok
                else "DITOLAK: Quote mint tidak terdaftar di allowlist resmi (rawan fake quote / spoof ticker)."
            ),
        )
    )

    # 2. RugCheck Fail-Closed Availability
    if not rugcheck_report:
        checks.append(
            GateCheck(
                id="rugcheck_available",
                layer="SECURITY",
                label="RugCheck On-Chain Audit",
                passed=False,
                severity="VETO",
                actual="UNAVAILABLE",
                threshold="Data RugCheck wajib tersedia (fail-closed)",
                detail="DITOLAK (Fail-Closed): Laporan RugCheck tidak tersedia / timeout — uang riil tidak boleh masuk buta.",
            )
        )
        return SecurityResult(
            passed=False,
            checks=tuple(checks),
            hard_quote_symbol=quote_symbol or pair.quote_symbol or "UNKNOWN",
            rugcheck_score=None,
            dev_pct=None,
            top1_pct=None,
            top10_pct=None,
            lp_locked_pct=None,
            lp_locked_usd=None,
            is_meteora_nft_lp=False,
            pareto_status="UNAVAILABLE",
        )

    # 3. Mint & Freeze Authority + Rugged Flag
    token_info = rugcheck_report.get("token") or {}
    mint_auth = token_info.get("mintAuthority")
    freeze_auth = token_info.get("freezeAuthority")
    is_rugged = bool(rugcheck_report.get("rugged"))
    auth_ok = (not mint_auth) and (not freeze_auth) and (not is_rugged)
    checks.append(
        GateCheck(
            id="authorities_revoked",
            layer="SECURITY",
            label="Mint / Freeze Authority & Rugged Flag",
            passed=auth_ok,
            severity="PASS" if auth_ok else "VETO",
            actual=(
                "Revoked & Clean"
                if auth_ok
                else f"mintAuth={bool(mint_auth)} freezeAuth={bool(freeze_auth)} rugged={is_rugged}"
            ),
            threshold="Mint=Null, Freeze=Null, Rugged=False",
            detail=(
                "Otoritas cetak & beku token sudah hangus."
                if auth_ok
                else "DITOLAK: Token masih bisa dicetak ulang, dibekukan, atau ditandai rugged."
            ),
        )
    )

    # 4. Token-2022 Dangerous Extensions & Mutable Metadata / Critical Risks
    token_ext = rugcheck_report.get("token_extensions") or {}
    risks = rugcheck_report.get("risks") or []
    found_dangerous_ext: list[str] = []
    if isinstance(token_ext, dict):
        for k, v in token_ext.items():
            if v and any(d in str(k).lower() for d in DANGEROUS_TOKEN2022_EXTENSIONS):
                found_dangerous_ext.append(str(k))
    for r in risks:
        r_name = str(r.get("name", "")).lower()
        r_level = str(r.get("level", "")).lower()
        if any(
            kw in r_name
            for kw in ("transfer hook", "permanent delegate", "transfer fee", "freeze", "honeypot")
        ) and r_level in {"danger", "error", "critical"}:
            found_dangerous_ext.append(str(r.get("name", "danger")))

    ext_ok = len(found_dangerous_ext) == 0
    checks.append(
        GateCheck(
            id="token2022_extensions",
            layer="SECURITY",
            label="Token-2022 & Kontrak Berbahaya",
            passed=ext_ok,
            severity="PASS" if ext_ok else "VETO",
            actual="Bersih" if ext_ok else ", ".join(sorted(set(found_dangerous_ext)))[:60],
            threshold="0 ekstensi berbahaya (TransferHook/PermanentDelegate/Fee)",
            detail=(
                "Tidak ditemukan ekstensi Token-2022 berbahaya."
                if ext_ok
                else f"DITOLAK: Terdeteksi risiko kontrak berbahaya ({', '.join(sorted(set(found_dangerous_ext)))})."
            ),
        )
    )

    # 5. Bangun daftar dinamis Vault/Pool AMM (KRITIS untuk PumpSwap & Meteora DLMM!)
    # Di PumpSwap, setiap koin punya pubkey pool unik (memegang 8%–22% suplai) — WAJIB dikecualikan agar tidak dianggap Whale!
    excluded_vaults: set[str] = set(KNOWN_PROTOCOL_AUTHORITIES)
    if pair.pair_address:
        excluded_vaults.add(pair.pair_address.strip())

    known_accounts = rugcheck_report.get("knownAccounts") or {}
    if isinstance(known_accounts, dict):
        for acc_addr, meta in known_accounts.items():
            if not isinstance(meta, dict):
                continue
            acc_type = str(meta.get("type") or "").strip().lower()
            acc_name = str(meta.get("name") or "").strip().lower()
            if acc_type in {"amm", "locker", "clob", "burn", "vault"} or any(
                kw in acc_name for kw in ("amm", "pool", "locker", "burn", "stake")
            ):
                excluded_vaults.add(str(acc_addr).strip())

    markets = rugcheck_report.get("markets") or []
    best_lp_lock_pct = 0.0
    best_lp_lock_usd = 0.0
    is_meteora_nft_lp = False
    rc_active_markets = 0

    for m in markets:
        for key in ("pubkey", "liquidityA", "liquidityB", "mintLP"):
            val = str(m.get(key) or "").strip()
            if val:
                excluded_vaults.add(val)
        lp = m.get("lp") or {}
        lp_mint = str(lp.get("lpMint") or "")
        market_type = str(m.get("marketType") or "").lower()
        if lp_mint == METEORA_NFT_LP_SENTINEL or "meteora" in market_type:
            is_meteora_nft_lp = True
        lock_pct = float(lp.get("lpLockedPct") or 0.0)
        lock_usd = float(lp.get("lpLockedUSD") or 0.0)
        pool_usd = float(lp.get("baseUSD") or 0.0) + float(lp.get("quoteUSD") or 0.0)
        if max(pool_usd, lock_usd) >= 2_000.0:
            rc_active_markets += 1
        if lock_pct > best_lp_lock_pct:
            best_lp_lock_pct = lock_pct
        if lock_usd > best_lp_lock_usd:
            best_lp_lock_usd = lock_usd

    active_markets_count = max(1, int(pair.pool_count or 1), rc_active_markets)

    # 6. Creator / Dev Supply Cap (<= 2.5%) & Holder Non-Protokol
    creator = str(rugcheck_report.get("creator") or "").strip()
    top_holders = rugcheck_report.get("topHolders") or []
    dev_pct = 0.0
    insider_count = 0
    non_protocol_holders: list[float] = []
    for h in top_holders:
        owner = str(h.get("owner") or "").strip()
        addr = str(h.get("address") or "").strip()
        pct = float(h.get("pct") or 0.0)
        is_insider = bool(h.get("insider"))
        if creator and (owner == creator or addr == creator):
            dev_pct = max(dev_pct, pct)
        if (owner and owner in excluded_vaults) or (addr and addr in excluded_vaults):
            continue
        if is_insider:
            insider_count += 1
            if not dev_pct:
                dev_pct = max(dev_pct, pct)
        non_protocol_holders.append(pct)

    dev_ok = dev_pct <= 2.5
    checks.append(
        GateCheck(
            id="dev_supply",
            layer="SECURITY",
            label="Kepemilikan Dev / Creator",
            passed=dev_ok,
            severity="PASS" if dev_ok else "VETO",
            actual=f"{dev_pct:.2f}%",
            threshold="<= 2.50%",
            detail=(
                f"Supply dev terkendali ({dev_pct:.2f}%)."
                if dev_ok
                else f"DITOLAK: Dev/insider memegang {dev_pct:.2f}% (> 2.50%, rawan dump)."
            ),
        )
    )

    # 7. LP Lock >= 80% OR >= $18K locked pool
    lp_ok = best_lp_lock_pct >= 80.0 or best_lp_lock_usd >= 18_000.0
    if lp_ok:
        lp_detail = (
            f"LP terverifikasi terkunci ({best_lp_lock_pct:.1f}% / ${best_lp_lock_usd:,.0f})."
            if best_lp_lock_pct >= 80.0
            else f"Pool AMM terkunci >= $18K (${best_lp_lock_usd:,.0f}) meski ada posisi NFT/CLMM paralel."
        )
    elif is_meteora_nft_lp:
        lp_detail = (
            "DITOLAK: Pool Meteora DLMM/DAMM v2 memakai NFT position tanpa bukti lock >= $18K di AMM terverifikasi."
        )
    else:
        lp_detail = f"DITOLAK: LP terkunci hanya {best_lp_lock_pct:.1f}% (${best_lp_lock_usd:,.0f}) — rawan rugpull tarik LP."

    checks.append(
        GateCheck(
            id="lp_lock",
            layer="SECURITY",
            label="Kunci Likuiditas (LP Lock)",
            passed=lp_ok,
            severity="PASS" if lp_ok else "VETO",
            actual=f"{best_lp_lock_pct:.1f}% (${best_lp_lock_usd:,.0f})",
            threshold=">= 80% terkunci ATAU >= $18K locked pool",
            detail=lp_detail,
        )
    )

    rc_score_raw = rugcheck_report.get("score_normalised")
    if rc_score_raw is None:
        rc_score_raw = rugcheck_report.get("score")
    rc_score = int(rc_score_raw) if isinstance(rc_score_raw, (int, float)) else None

    total_holders_raw = rugcheck_report.get("totalHolders")
    total_holders = int(total_holders_raw) if isinstance(total_holders_raw, (int, float)) and total_holders_raw > 0 else 0

    import time as _time
    resolved_now_ms = now_ms if now_ms is not None else int(_time.time() * 1000)
    if pair.pair_created_at_ms > 0:
        age_min = max(1.0, (resolved_now_ms - pair.pair_created_at_ms) / 60_000.0)
    else:
        age_min = 60.0
    holders_per_min = round(total_holders / age_min, 2) if total_holders > 0 else 0.0

    # 8. Minimum Organic Holder Depth & Holder Density Ratio (v5: Anti-Hollow-Shell MC/Holder!)
    if pair.market_cap >= 390_000.0:
        min_holders_req = 1_800
        max_mc_per_holder = 280.0
    elif pair.market_cap >= 210_000.0:
        min_holders_req = 600
        max_mc_per_holder = 280.0
    elif require_hyper_viral_holders:
        min_holders_req = 750
        max_mc_per_holder = 300.0
    elif require_steady_climber_holders:
        min_holders_req = 450
        max_mc_per_holder = 300.0
    elif pair.market_cap >= 92_000.0:
        min_holders_req = 350
        max_mc_per_holder = 320.0
    else:
        min_holders_req = 250
        max_mc_per_holder = 320.0

    mc_per_holder_usd = (
        round(pair.market_cap / total_holders, 1)
        if total_holders > 0 and pair.market_cap > 0
        else 0.0
    )
    holder_density_ok = (total_holders == 0) or (mc_per_holder_usd <= max_mc_per_holder)

    warn_or_danger_risks_early = sum(
        1
        for r in risks
        if str(r.get("level", "")).lower() in {"danger", "error", "critical", "warn"}
    )
    # v5: Deteksi risiko kritis RugCheck seperti High Holder Correlation (bundled swarm ATX), Copycat, atau skor buruk
    toxic_risk_names: list[str] = []
    for r in risks:
        r_name = str(r.get("name", "")).lower()
        r_level = str(r.get("level", "")).lower()
        if r_level in {"danger", "error", "critical", "warn"} and any(
            kw in r_name
            for kw in (
                "high holder correlation",
                "high market cap per holder",
                "copycat",
                "single holder ownership",
                "low liquidity",
            )
        ):
            toxic_risk_names.append(str(r.get("name", "risk")))
    bad_rc_score = rc_score is not None and rc_score > 35
    rugcheck_integrity_ok = (len(toxic_risk_names) == 0) and (not bad_rc_score)

    checks.append(
        GateCheck(
            id="rugcheck_integrity",
            layer="SECURITY",
            label="Integritas Risiko RugCheck (Anti-Bundler & Korelasi)",
            passed=rugcheck_integrity_ok,
            severity="PASS" if rugcheck_integrity_ok else "VETO",
            actual=(
                f"Score {rc_score if rc_score is not None else 'N/A'} · Bersih"
                if rugcheck_integrity_ok
                else f"Score {rc_score} · {', '.join(sorted(set(toxic_risk_names)))[:55] or 'Skor tinggi'}"
            ),
            threshold="RC Score <= 35 & bebas High Holder Correlation / Copycat / Hollow MC",
            detail=(
                "Skor RugCheck sehat tanpa sinyal korelasi dompet bundler/cabal."
                if rugcheck_integrity_ok
                else f"DITOLAK: Risiko on-chain tinggi ({', '.join(sorted(set(toxic_risk_names))) or f'RC score {rc_score} > 35'})."
            ),
        )
    )

    fasttrack_clean_ok = (not require_hyper_viral_holders) or (
        total_holders >= 750 and warn_or_danger_risks_early == 0 and dev_pct <= 0.05
    )
    holders_ok = (
        ((total_holders == 0) and not require_hyper_viral_holders)
        or (total_holders >= min_holders_req and fasttrack_clean_ok and holder_density_ok)
    )
    checks.append(
        GateCheck(
            id="organic_holders",
            layer="SECURITY",
            label="Basis Holder Organik & Densitas MC/Holder (Anti-Hollow-Shell)",
            passed=holders_ok,
            severity="PASS" if holders_ok else "VETO",
            actual=(
                f"{total_holders:,}H ({holders_per_min:.1f}/m · ${mc_per_holder_usd:,.0f}/H)"
                if total_holders > 0
                else "N/A"
            ),
            threshold=(
                f">= {min_holders_req:,}H, <= ${max_mc_per_holder:.0f}/H & 0 risks (Fast-Track)"
                if require_hyper_viral_holders
                else f">= {min_holders_req:,}H & <= ${max_mc_per_holder:.0f} MC/holder"
            ),
            detail=(
                f"Basis komunitas holder padat ({total_holders:,} dompet, ${mc_per_holder_usd:,.0f}/holder, {holders_per_min:.1f}/m)."
                if holders_ok
                else (
                    f"DITOLAK (Fast-Track < 20m): Wajib >= 750H, <= $300/H & 0 risks (aktual {total_holders:,}H, ${mc_per_holder_usd:,.0f}/H, {warn_or_danger_risks_early} risk)."
                    if require_hyper_viral_holders
                    else (
                        f"DITOLAK (Hollow Shell / Gelembung Cabal): MC/Holder terlalu mahal (${mc_per_holder_usd:,.0f}/holder > ${max_mc_per_holder:.0f}) — rawan terjun bebas saat cabal jual."
                        if total_holders >= min_holders_req and not holder_density_ok
                        else f"DITOLAK: Holder terlalu sedikit ({total_holders:,} < {min_holders_req:,}) — rawan sepi & langsung mati."
                    )
                )
            ),
        )
    )

    # 9. Smart Viral Pareto Law (Anti-Whale Concentration & Anti-Nano-Swarm/Cabal)
    non_protocol_holders.sort(reverse=True)
    top1_pct = non_protocol_holders[0] if non_protocol_holders else 0.0
    top10_pct = sum(non_protocol_holders[:10]) if non_protocol_holders else 0.0
    top5 = non_protocol_holders[:5]

    warn_or_danger_risks = sum(
        1
        for r in risks
        if str(r.get("level", "")).lower() in {"danger", "error", "critical", "warn"}
    )

    # Deteksi Nano-Swarm buatan:
    # (a) 5 wallet teratas hampir seragam persis (spread < 0.35%) dengan total >= 9%
    # (b) Fragmentasi ekstrem botnet (seperti ATX: ribuan dompet 0.04%, Top10 < 3.5% pada >= 500 holders!)
    is_nano_swarm = (
        (len(top5) >= 5 and (top5[0] - top5[4]) < 0.35 and sum(top5) >= 9.0)
        or (len(non_protocol_holders) >= 5 and total_holders >= 500 and top10_pct < 3.5)
    )

    # Smart Viral Pareto (v4/v5):
    # - Ultra-Clean Viral Runner (seperti $Zero: RC score<=5, 0 risk, 0 insider, Dev 0%, Top10<=28%, >=1.000 holder): Top1 <= 11.0%
    # - Proven Community (>=2.000 holder, 0 insider, RC score<=15): Top1 <= 8.0%
    # - Default token kecil/baru: ketat <= 6.5%
    clean_viral_pareto = (
        rc_score is not None
        and rc_score <= 5
        and warn_or_danger_risks == 0
        and insider_count == 0
        and dev_pct <= 0.05
        and top10_pct <= 28.0
        and total_holders >= 1_000
    )
    if clean_viral_pareto:
        max_top1_allowed = 11.0
    elif total_holders >= 2_000 and insider_count == 0 and (rc_score is not None and rc_score <= 15):
        max_top1_allowed = 8.0
    else:
        max_top1_allowed = 6.5

    pareto_ok = (
        (top1_pct <= max_top1_allowed)
        and (top10_pct <= 30.0)
        and (insider_count < 3)
        and (not is_nano_swarm)
    )
    if is_nano_swarm:
        pareto_status = "NANO_SWARM_DETECTED"
        pareto_detail = (
            f"DITOLAK: Pola Nano-Swarm/Botnet terdeteksi (Top1={top1_pct:.2f}%, Top10={top10_pct:.2f}% — distribusi artifisial/seragam)."
        )
    elif insider_count >= 3:
        pareto_status = "INSIDER_CLUSTER"
        pareto_detail = f"DITOLAK: Terdeteksi {insider_count} dompet insider di Top Holders."
    elif top1_pct > max_top1_allowed or top10_pct > 30.0:
        pareto_status = "WHALE_CONCENTRATED"
        pareto_detail = (
            f"DITOLAK: Konsentrasi holder terlalu tinggi (Top1={top1_pct:.2f}% > {max_top1_allowed:.1f}%, Top10={top10_pct:.2f}%)."
        )
    elif clean_viral_pareto and top1_pct > 6.5:
        pareto_status = "VIRAL_PARETO_CLEAN"
        pareto_detail = (
            f"Lolos Smart Viral Pareto (Score={rc_score}, 0 risk, 0% dev, {total_holders:,}H, Top1={top1_pct:.2f}%, Top10={top10_pct:.2f}%)."
        )
    else:
        pareto_status = "HEALTHY_PARETO"
        pareto_detail = f"Distribusi organik lolos Hukum Pareto (Top1={top1_pct:.2f}%, Top10={top10_pct:.2f}%)."

    checks.append(
        GateCheck(
            id="pareto_distribution",
            layer="SECURITY",
            label="Smart Viral Pareto & Anti-Nano-Swarm",
            passed=pareto_ok,
            severity="PASS" if pareto_ok else "VETO",
            actual=f"Top1 {top1_pct:.2f}% · Top10 {top10_pct:.2f}%",
            threshold=f"Top1 <= {max_top1_allowed:.1f}%, Top10 3.5%–30.0%, non-seragam",
            detail=pareto_detail,
        )
    )

    all_passed = all(c.passed for c in checks)
    return SecurityResult(
        passed=all_passed,
        checks=tuple(checks),
        hard_quote_symbol=quote_symbol or pair.quote_symbol or "UNKNOWN",
        rugcheck_score=rc_score,
        dev_pct=round(dev_pct, 2),
        top1_pct=round(top1_pct, 2),
        top10_pct=round(top10_pct, 2),
        lp_locked_pct=round(best_lp_lock_pct, 1),
        lp_locked_usd=round(best_lp_lock_usd, 0),
        is_meteora_nft_lp=is_meteora_nft_lp,
        pareto_status=pareto_status,
        total_holders=total_holders,
        holders_per_min=holders_per_min,
        active_markets_count=active_markets_count,
        clean_viral_pareto=clean_viral_pareto,
        mc_per_holder_usd=mc_per_holder_usd,
    )
