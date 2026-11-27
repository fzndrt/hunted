"""6-Feed Async Solana Discovery (GeckoTerminal + DexScreener PumpSwap/Raydium/Profiles/Boosts)."""

from __future__ import annotations

import asyncio
from typing import Any, Optional
import httpx

from .config import (
    ORGANIC_FEED_SOURCES,
    SOL_MINT,
    VERIFIED_HARD_QUOTE_MINTS,
    is_valid_solana_mint,
)
from .models import PairSnapshot


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        num = float(val)
        return num if num == num else default
    except (TypeError, ValueError):
        return default


def _safe_int(val: Any, default: int = 0) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def parse_dexscreener_pair(
    raw: dict[str, Any], feed_sources: tuple[str, ...] = ()
) -> Optional[PairSnapshot]:
    if str(raw.get("chainId") or "").lower() != "solana":
        return None
    base = raw.get("baseToken") or {}
    quote = raw.get("quoteToken") or {}
    mint = str(base.get("address") or "").strip()
    quote_mint = str(quote.get("address") or "").strip()
    if not is_valid_solana_mint(mint) or mint == SOL_MINT:
        return None

    liq = raw.get("liquidity") or {}
    vol = raw.get("volume") or {}
    txns = raw.get("txns") or {}
    pc = raw.get("priceChange") or {}

    m5_tx = txns.get("m5") or {}
    h1_tx = txns.get("h1") or {}
    h6_tx = txns.get("h6") or {}
    h24_tx = txns.get("h24") or {}

    mc = _safe_float(raw.get("marketCap")) or _safe_float(raw.get("fdv"))
    boosts = raw.get("boosts") or {}
    active_boosts = _safe_int(boosts.get("active")) if isinstance(boosts, dict) else 0
    info = raw.get("info") if isinstance(raw.get("info"), dict) else {}
    socials = info.get("socials") if isinstance(info.get("socials"), list) else []
    websites = info.get("websites") if isinstance(info.get("websites"), list) else []
    social_count = len(socials)
    has_socials = social_count > 0
    has_website = len(websites) > 0
    has_image = bool(info.get("imageUrl"))
    return PairSnapshot(
        mint=mint,
        pair_address=str(raw.get("pairAddress") or ""),
        dex_id=str(raw.get("dexId") or "solana"),
        symbol=str(base.get("symbol") or "TOKEN")[:20],
        name=str(base.get("name") or base.get("symbol") or "Solana Token")[:48],
        quote_mint=quote_mint,
        quote_symbol=str(quote.get("symbol") or "UNKNOWN")[:12],
        price_usd=_safe_float(raw.get("priceUsd")),
        market_cap=mc,
        liquidity_usd=_safe_float(liq.get("usd")),
        vol_m5=_safe_float(vol.get("m5")),
        vol_h1=_safe_float(vol.get("h1")),
        vol_h24=_safe_float(vol.get("h24")),
        buys_m5=_safe_int(m5_tx.get("buys")),
        sells_m5=_safe_int(m5_tx.get("sells")),
        buys_h1=_safe_int(h1_tx.get("buys")),
        sells_h1=_safe_int(h1_tx.get("sells")),
        buys_h24=_safe_int(h24_tx.get("buys")),
        sells_h24=_safe_int(h24_tx.get("sells")),
        price_change_m5=_safe_float(pc.get("m5")),
        price_change_h1=_safe_float(pc.get("h1")),
        price_change_h24=_safe_float(pc.get("h24")),
        pair_created_at_ms=_safe_int(raw.get("pairCreatedAt")),
        feed_sources=feed_sources,
        url=str(raw.get("url") or f"https://dexscreener.com/solana/{mint}"),
        vol_h6=_safe_float(vol.get("h6")),
        price_change_h6=_safe_float(pc.get("h6")),
        buys_h6=_safe_int(h6_tx.get("buys")),
        sells_h6=_safe_int(h6_tx.get("sells")),
        pool_count=1,
        is_boosted=active_boosts > 0,
        has_socials=has_socials,
        social_count=social_count,
        has_website=has_website,
        has_image=has_image,
    )


def pick_best_solana_pair(
    pairs: list[dict[str, Any]], mint: str, feed_sources: tuple[str, ...] = ()
) -> Optional[PairSnapshot]:
    from dataclasses import replace

    candidates: list[PairSnapshot] = []
    for raw in pairs:
        snap = parse_dexscreener_pair(raw, feed_sources=feed_sources)
        if snap and snap.mint == mint:
            candidates.append(snap)
    if not candidates:
        return None
    # Hitung jumlah pool DEX aktif >= $2K likuiditas (sinyal Smart LP / multi-pool seperti mihao, STONK, $Zero)
    active_pool_count = max(
        1,
        sum(
            1
            for c in candidates
            if c.liquidity_usd >= 2_000.0 and c.quote_mint in VERIFIED_HARD_QUOTE_MINTS
        ),
    )
    any_boosted = any(c.is_boosted for c in candidates)
    any_socials = any(c.has_socials for c in candidates)
    max_social_count = max(c.social_count for c in candidates)
    any_website = any(c.has_website for c in candidates)
    any_image = any(c.has_image for c in candidates)
    # Prioritaskan pair dengan hard-quote terverifikasi lalu likuiditas terbesar
    candidates.sort(
        key=lambda p: (
            1 if p.quote_mint in VERIFIED_HARD_QUOTE_MINTS else 0,
            p.liquidity_usd,
        ),
        reverse=True,
    )
    return replace(
        candidates[0],
        pool_count=active_pool_count,
        is_boosted=any_boosted,
        has_socials=any_socials,
        social_count=max_social_count,
        has_website=any_website,
        has_image=any_image,
    )


async def _get_json(client: httpx.AsyncClient, url: str) -> Any:
    try:
        resp = await client.get(url, timeout=4.5)
        if resp.status_code != 200:
            return None
        return resp.json()
    except Exception:
        return None


async def fetch_live_sol_price_usd(
    client: httpx.AsyncClient, fallback_usd: float = 160.0
) -> float:
    """Ambil harga SOL/USD dinamis (mengganti harga hardcoded di versi lama)."""
    data = await _get_json(
        client, f"https://api.dexscreener.com/tokens/v1/solana/{SOL_MINT}"
    )
    if isinstance(data, list):
        for pair in data:
            q = (pair.get("quoteToken") or {}).get("symbol", "")
            if q in {"USDC", "USDT"}:
                px = _safe_float(pair.get("priceUsd"))
                if 10.0 <= px <= 5000.0:
                    return px
    return fallback_usd


async def fetch_pair_for_mint(
    client: httpx.AsyncClient, mint: str, feed_sources: tuple[str, ...] = ("manual",)
) -> Optional[PairSnapshot]:
    if not is_valid_solana_mint(mint):
        return None
    data = await _get_json(
        client, f"https://api.dexscreener.com/tokens/v1/solana/{mint}"
    )
    if not isinstance(data, list):
        return None
    return pick_best_solana_pair(data, mint, feed_sources=feed_sources)


async def fetch_pairs_batch(
    client: httpx.AsyncClient,
    mints: list[str],
    sources_map: dict[str, set[str]],
) -> dict[str, PairSnapshot]:
    results: dict[str, PairSnapshot] = {}
    clean_mints = [m for m in mints if is_valid_solana_mint(m) and m != SOL_MINT]

    for i in range(0, len(clean_mints), 25):
        chunk = clean_mints[i : i + 25]
        joined = ",".join(chunk)
        data = await _get_json(
            client, f"https://api.dexscreener.com/tokens/v1/solana/{joined}"
        )
        if not isinstance(data, list):
            continue
        grouped: dict[str, list[dict[str, Any]]] = {}
        for raw in data:
            addr = str((raw.get("baseToken") or {}).get("address") or "").strip()
            if addr:
                grouped.setdefault(addr, []).append(raw)
        for m in chunk:
            best = pick_best_solana_pair(
                grouped.get(m, []),
                m,
                feed_sources=tuple(sorted(sources_map.get(m, set()))),
            )
            if best:
                results[m] = best
    return results


async def _fetch_gecko_staggered(
    client: httpx.AsyncClient,
) -> tuple[Any, Any, Any, Any, Any]:
    """Tarik GeckoTerminal 5m (p1 & p2), 1h (p1 & p2) & 6h berurutan (jeda 0.45s) untuk tangkap koin organik awal."""
    gt_5m = await _get_json(
        client,
        "https://api.geckoterminal.com/api/v2/networks/solana/trending_pools?duration=5m&page=1",
    )
    await asyncio.sleep(0.45)
    gt_5m_p2 = await _get_json(
        client,
        "https://api.geckoterminal.com/api/v2/networks/solana/trending_pools?duration=5m&page=2",
    )
    await asyncio.sleep(0.45)
    gt_1h = await _get_json(
        client,
        "https://api.geckoterminal.com/api/v2/networks/solana/trending_pools?duration=1h&page=1",
    )
    await asyncio.sleep(0.45)
    gt_1h_p2 = await _get_json(
        client,
        "https://api.geckoterminal.com/api/v2/networks/solana/trending_pools?duration=1h&page=2",
    )
    await asyncio.sleep(0.45)
    gt_6h = await _get_json(
        client,
        "https://api.geckoterminal.com/api/v2/networks/solana/trending_pools?duration=6h&page=1",
    )
    return gt_5m, gt_5m_p2, gt_1h, gt_1h_p2, gt_6h


async def discover_solana_candidates(
    client: httpx.AsyncClient,
    max_candidates: int = 100,
    watchlist_mints: Optional[list[str]] = None,
) -> tuple[list[PairSnapshot], list[str]]:
    """
    Kumpulkan kandidat 100% ORGANIK dari 10 sumber utama + Watchlist Auto-Rescan (v5.3: ZERO Paid-Boost Spam!):
      - Watchlist Auto-Rescan: koin berkualitas yang sebelumnya WAIT_DIP dipantau ulang hingga 2 jam
      - Organik Intraday & Harian: GeckoTerminal 5m (p1 & p2), 1h (p1 & p2), 6h
      - Multi-Pool & Komunitas Awal: DexScreener PumpSwap, Meteora, Raydium, CTO, Token Profiles (non-boosted)
    """
    dex_urls = {
        "dex_pumpswap": "https://api.dexscreener.com/latest/dex/search?q=pumpswap",
        "dex_meteora": "https://api.dexscreener.com/latest/dex/search?q=meteora",
        "dex_raydium": "https://api.dexscreener.com/latest/dex/search?q=raydium",
        "dex_community_takeovers": "https://api.dexscreener.com/community-takeovers/latest/v1",
        "dex_token_profiles": "https://api.dexscreener.com/token-profiles/latest/v1",
    }

    dex_keys = list(dex_urls.keys())
    gecko_task = _fetch_gecko_staggered(client)
    dex_tasks = [_get_json(client, dex_urls[k]) for k in dex_keys]
    gathered = await asyncio.gather(gecko_task, *dex_tasks)
    gt_5m, gt_5m_p2, gt_1h, gt_1h_p2, gt_6h = gathered[0]
    dex_responses = gathered[1:]

    sources_map: dict[str, set[str]] = {}
    notes: list[str] = []

    def add_mint(mint: str, source: str) -> None:
        m = mint.strip()
        if is_valid_solana_mint(m) and m not in VERIFIED_HARD_QUOTE_MINTS:
            sources_map.setdefault(m, set()).add(source)

    if watchlist_mints:
        wl_added = 0
        for wm in watchlist_mints[:15]:
            before = len(sources_map)
            add_mint(wm, "watchlist_rescan")
            if len(sources_map) > before or (wm.strip() in sources_map and "watchlist_rescan" in sources_map[wm.strip()]):
                wl_added += 1
        notes.append(f"watchlist_rescan:{wl_added}")

    for key, payload in (
        ("gecko_trending_5m", gt_5m),
        ("gecko_trending_5m_p2", gt_5m_p2),
        ("gecko_trending_1h", gt_1h),
        ("gecko_trending_1h_p2", gt_1h_p2),
        ("gecko_trending_6h", gt_6h),
    ):
        before = sum(1 for s in sources_map.values() if key in s)
        if isinstance(payload, dict):
            for item in payload.get("data") or []:
                rel = (item.get("relationships") or {}).get("base_token") or {}
                tid = str((rel.get("data") or {}).get("id") or "")
                if tid.startswith("solana_"):
                    add_mint(tid.replace("solana_", "", 1), key)
        after = sum(1 for s in sources_map.values() if key in s)
        notes.append(f"{key}:{after - before}")

    for key, payload in zip(dex_keys, dex_responses):
        before = sum(1 for s in sources_map.values() if key in s)
        if key in {"dex_pumpswap", "dex_meteora", "dex_raydium"} and isinstance(payload, dict):
            for p in payload.get("pairs") or []:
                if str(p.get("chainId") or "").lower() == "solana":
                    add_mint(str((p.get("baseToken") or {}).get("address") or ""), key)
        elif key in {"dex_community_takeovers", "dex_token_profiles"} and isinstance(payload, list):
            sol_seen = 0
            for item in payload:
                if str(item.get("chainId") or "").lower() == "solana":
                    add_mint(str(item.get("tokenAddress") or ""), key)
                    sol_seen += 1
                    if sol_seen >= 20:
                        break
        after = sum(1 for s in sources_map.values() if key in s)
        notes.append(f"{key}:{after - before}")

    # Prioritaskan Watchlist Rescan + multi-feed ORGANIK + profil/CTO baru agar koin awal seperti VAULT tidak terbuang oleh kuota Gecko p2!
    def _mint_priority(m: str) -> tuple[int, int, int]:
        srcs = sources_map[m]
        wl_bonus = 1 if "watchlist_rescan" in srcs else 0
        organic_cnt = sum(1 for s in srcs if s in ORGANIC_FEED_SOURCES)
        primary_early_bonus = (
            1
            if (
                "gecko_trending_5m" in srcs
                or "gecko_trending_1h" in srcs
                or "dex_token_profiles" in srcs
                or "dex_community_takeovers" in srcs
            )
            else 0
        )
        return (wl_bonus, organic_cnt + primary_early_bonus, len(srcs))

    ranked_mints = sorted(sources_map.keys(), key=_mint_priority, reverse=True)[
        :max_candidates
    ]

    batch = await fetch_pairs_batch(client, ranked_mints, sources_map)
    pairs = list(batch.values())
    pairs.sort(
        key=lambda p: (
            1 if "watchlist_rescan" in p.feed_sources else 0,
            0 if p.is_boosted else 1,
            1 if (p.has_socials or p.has_website) else 0,
            sum(1 for s in p.feed_sources if s in ORGANIC_FEED_SOURCES),
            p.vol_m5,
            p.vol_h1,
        ),
        reverse=True,
    )
    return pairs, notes
