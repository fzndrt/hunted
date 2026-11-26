import {
  DEFAULT_SCAN_CONFIG,
  isValidSolanaMint,
  VERIFIED_HARD_QUOTE_MINTS,
} from './constants.ts';
import { evaluateAntiTopLayer } from './antiTop.ts';
import { evaluateExecutionLayer } from './execution.ts';
import { computeScoreAndVerdict } from './scoring.ts';
import {
  evaluateSecurityLayer,
  type DexPairLike,
  type RugCheckReportLike,
} from './security.ts';
import type { AuditedToken, ScanConfig, ScanSummary } from './types.ts';

const HEADERS = {
  Accept: 'application/json',
};

// Short-lived server cache to prevent public API 429 storms on rapid clicks
const rugCache = new Map<string, { ts: number; data: RugCheckReportLike | null }>();
const RUG_CACHE_TTL_MS = 45_000;

async function fetchJsonWithTimeout<T>(url: string, timeoutMs = 3500): Promise<T | null> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const resp = await fetch(url, { signal: controller.signal, headers: HEADERS });
    clearTimeout(timer);
    if (!resp.ok) return null;
    return (await resp.json()) as T;
  } catch {
    clearTimeout(timer);
    return null;
  }
}

async function fetchRugCheck(mint: string): Promise<RugCheckReportLike | null> {
  const cached = rugCache.get(mint);
  if (cached && Date.now() - cached.ts < RUG_CACHE_TTL_MS) {
    return cached.data;
  }
  const url = `https://api.rugcheck.xyz/v1/tokens/${encodeURIComponent(mint)}/report`;
  const data = await fetchJsonWithTimeout<RugCheckReportLike>(url, 3200);
  rugCache.set(mint, { ts: Date.now(), data });
  if (rugCache.size > 200) {
    const oldestKey = rugCache.keys().next().value;
    if (oldestKey) rugCache.delete(oldestKey);
  }
  return data;
}

function selectBestSolanaPair(pairs: DexPairLike[], mint: string): DexPairLike | null {
  const solPairs = pairs.filter(
    (p) =>
      String(p.chainId ?? '').toLowerCase() === 'solana' &&
      String(p.baseToken?.address ?? '').trim() === mint
  );
  if (solPairs.length === 0) return null;
  solPairs.sort((a, b) => {
    const aHard = VERIFIED_HARD_QUOTE_MINTS[String(a.quoteToken?.address ?? '')] ? 1 : 0;
    const bHard = VERIFIED_HARD_QUOTE_MINTS[String(b.quoteToken?.address ?? '')] ? 1 : 0;
    if (aHard !== bHard) return bHard - aHard;
    return (Number(b.liquidity?.usd) || 0) - (Number(a.liquidity?.usd) || 0);
  });
  return solPairs[0];
}

function buildTokenLinks(mint: string, pairAddress: string) {
  return {
    dexScreener: `https://dexscreener.com/solana/${pairAddress || mint}`,
    jupiter: `https://jup.ag/swap/SOL-${mint}`,
    solscan: `https://solscan.io/token/${mint}`,
    rugcheck: `https://rugcheck.xyz/tokens/${mint}`,
  };
}

/**
 * Full 4-Layer Deep Audit of a single Solana pair (Security + AntiTop + Jupiter Execution + Score).
 */
export async function auditSinglePair(
  pair: DexPairLike,
  cfg: ScanConfig
): Promise<AuditedToken> {
  const mint = String(pair.baseToken?.address ?? '').trim();
  const pairAddress = String(pair.pairAddress ?? '').trim();

  // Layer 2 runs in-memory first (~0.1ms)
  const antiTop = evaluateAntiTopLayer(pair, cfg);
  const minHolders = antiTop.corridor?.minHolders ?? 75;

  // Run Layer 1 (RugCheck) + Layer 3 (Jupiter dual-size round-trip) in parallel
  const [rugData, execution] = await Promise.all([
    fetchRugCheck(mint),
    evaluateExecutionLayer(mint, cfg),
  ]);

  const security = evaluateSecurityLayer(pair, rugData, minHolders);
  const { verdict, verdictHeadline, totalScore, pillars, tradePlan } = computeScoreAndVerdict(
    security,
    antiTop,
    execution,
    cfg
  );

  return {
    mint,
    pairAddress,
    dexId: String(pair.dexId ?? 'solana'),
    name: String(pair.baseToken?.name ?? 'Unknown'),
    symbol: String(pair.baseToken?.symbol ?? 'SOL'),
    priceUsd: Number(pair.priceUsd) || 0,
    verdict,
    verdictHeadline,
    totalScore,
    pillars,
    security,
    antiTop,
    execution,
    tradePlan,
    links: buildTokenLinks(mint, pairAddress),
    auditedAt: Date.now(),
  };
}

/**
 * Single-Mint Deep Forensic Audit (for checking ANY contract address before buying with real money).
 */
export async function inspectSingleMint(
  rawMint: string,
  partialCfg?: Partial<ScanConfig>
): Promise<AuditedToken | null> {
  const mint = rawMint.trim();
  if (!isValidSolanaMint(mint)) {
    throw new Error('Alamat kontrak Solana tidak valid (harus Base58 32–44 karakter).');
  }
  const cfg: ScanConfig = { ...DEFAULT_SCAN_CONFIG, ...(partialCfg ?? {}) };
  const url = `https://api.dexscreener.com/tokens/v1/solana/${encodeURIComponent(mint)}`;
  const pairs = await fetchJsonWithTimeout<DexPairLike[]>(url, 4000);
  if (!Array.isArray(pairs) || pairs.length === 0) {
    return null;
  }
  const bestPair = selectBestSolanaPair(pairs, mint);
  if (!bestPair) return null;
  return auditSinglePair(bestPair, cfg);
}

/**
 * Bounded concurrency pool to respect public API rate limits cleanly (max 4 concurrent Stage-2 audits).
 */
async function mapBounded<T, R>(
  items: T[],
  concurrency: number,
  worker: (item: T) => Promise<R>
): Promise<R[]> {
  const results: R[] = new Array(items.length);
  let cursor = 0;

  async function runWorker() {
    while (cursor < items.length) {
      const idx = cursor++;
      results[idx] = await worker(items[idx]);
    }
  }

  const workers = Array.from({ length: Math.min(concurrency, items.length) }, () => runWorker());
  await Promise.all(workers);
  return results;
}

/**
 * Live Multi-Feed Discovery + Clean 4-Layer Pipeline Scan.
 * Pulls real organic & trending Solana pools from DexScreener + GeckoTerminal without rate-limit storms.
 */
export async function runLiveCleanScan(partialCfg?: Partial<ScanConfig>): Promise<ScanSummary> {
  const t0 = Date.now();
  const cfg: ScanConfig = { ...DEFAULT_SCAN_CONFIG, ...(partialCfg ?? {}) };
  const feedNotes: string[] = [];

  // 1. Staggered GeckoTerminal 5m/1h/6h (anti-429) + Parallel Organic DexScreener Feeds
  const fetchGeckoStaggered = async () => {
    const gt5m = await fetchJsonWithTimeout<{ data?: Array<Record<string, unknown>> }>(
      'https://api.geckoterminal.com/api/v2/networks/solana/trending_pools?duration=5m&page=1',
      4000
    );
    await new Promise((r) => setTimeout(r, 700));
    const gt1h = await fetchJsonWithTimeout<{ data?: Array<Record<string, unknown>> }>(
      'https://api.geckoterminal.com/api/v2/networks/solana/trending_pools?duration=1h&page=1',
      4000
    );
    await new Promise((r) => setTimeout(r, 700));
    const gt6h = await fetchJsonWithTimeout<{ data?: Array<Record<string, unknown>> }>(
      'https://api.geckoterminal.com/api/v2/networks/solana/trending_pools?duration=6h&page=1',
      4000
    );
    return [gt5m, gt1h, gt6h] as const;
  };

  const [[gt5m, gt1h, gt6h], dsPump, dsRay, dsCto, dsBoostsTop] = await Promise.all([
    fetchGeckoStaggered(),
    fetchJsonWithTimeout<{ pairs?: DexPairLike[] }>(
      'https://api.dexscreener.com/latest/dex/search?q=pumpswap',
      4000
    ),
    fetchJsonWithTimeout<{ pairs?: DexPairLike[] }>(
      'https://api.dexscreener.com/latest/dex/search?q=raydium',
      4000
    ),
    fetchJsonWithTimeout<Array<{ chainId?: string; tokenAddress?: string }>>(
      'https://api.dexscreener.com/community-takeovers/latest/v1',
      4000
    ),
    fetchJsonWithTimeout<Array<{ chainId?: string; tokenAddress?: string }>>(
      'https://api.dexscreener.com/token-boosts/top/v1',
      4000
    ),
  ]);

  const organicScoreByMint = new Map<string, number>();
  const recordMint = (mint: string, weight: number) => {
    if (isValidSolanaMint(mint) && !VERIFIED_HARD_QUOTE_MINTS[mint]) {
      organicScoreByMint.set(mint, (organicScoreByMint.get(mint) ?? 0) + weight);
    }
  };

  const extractGtMints = (payload: { data?: Array<Record<string, unknown>> } | null, label: string) => {
    if (!payload?.data) {
      feedNotes.push(`${label}: unavailable`);
      return;
    }
    let added = 0;
    for (const pool of payload.data) {
      const rel = (pool.relationships as Record<string, { data?: { id?: string } }> | undefined)
        ?.base_token?.data?.id;
      if (typeof rel === 'string' && rel.startsWith('solana_')) {
        const mint = rel.slice('solana_'.length);
        if (isValidSolanaMint(mint) && !VERIFIED_HARD_QUOTE_MINTS[mint]) {
          recordMint(mint, 3);
          added++;
        }
      }
    }
    feedNotes.push(`${label}: ${added} mints`);
  };

  extractGtMints(gt5m, 'GeckoTerminal 5m Trending');
  extractGtMints(gt1h, 'GeckoTerminal 1h Revival');
  extractGtMints(gt6h, 'GeckoTerminal 6h Daily Consolidation');

  for (const [searchPayload, label] of [
    [dsPump, 'DexScreener PumpSwap'],
    [dsRay, 'DexScreener Raydium'],
  ] as const) {
    if (!Array.isArray(searchPayload?.pairs)) continue;
    let added = 0;
    for (const p of searchPayload.pairs) {
      if (String(p.chainId ?? '').toLowerCase() !== 'solana') continue;
      const mint = String(p.baseToken?.address ?? '').trim();
      if (isValidSolanaMint(mint) && !VERIFIED_HARD_QUOTE_MINTS[mint]) {
        recordMint(mint, 2);
        added++;
      }
    }
    feedNotes.push(`${label}: ${added} mints`);
  }

  for (const [arr, label, weight] of [
    [dsCto, 'DexScreener CTO', 2],
    [dsBoostsTop, 'DexScreener Top Boosts', 1],
  ] as const) {
    if (!Array.isArray(arr)) continue;
    let added = 0;
    for (const item of arr.slice(0, 25)) {
      if (String(item.chainId ?? '').toLowerCase() !== 'solana') continue;
      const mint = String(item.tokenAddress ?? '').trim();
      if (isValidSolanaMint(mint) && !VERIFIED_HARD_QUOTE_MINTS[mint]) {
        recordMint(mint, weight);
        added++;
      }
    }
    feedNotes.push(`${label}: ${added} mints`);
  }

  // 2. Batch-fetch live market data prioritizing organic trending mints first (max 60 candidates)
  const mintList = Array.from(organicScoreByMint.entries())
    .sort((a, b) => b[1] - a[1])
    .map(([m]) => m)
    .slice(0, 60);
  const chunks: string[][] = [];
  for (let i = 0; i < mintList.length; i += 30) {
    chunks.push(mintList.slice(i, i + 30));
  }

  const pairBatches = await Promise.all(
    chunks.map((chunk) =>
      fetchJsonWithTimeout<DexPairLike[]>(
        `https://api.dexscreener.com/tokens/v1/solana/${chunk.join(',')}`,
        4000
      )
    )
  );

  const pairsByMint = new Map<string, DexPairLike[]>();
  for (const batch of pairBatches) {
    if (!Array.isArray(batch)) continue;
    for (const p of batch) {
      if (String(p.chainId ?? '').toLowerCase() !== 'solana') continue;
      const mint = String(p.baseToken?.address ?? '').trim();
      if (!mint) continue;
      const existing = pairsByMint.get(mint);
      if (existing) existing.push(p);
      else pairsByMint.set(mint, [p]);
    }
  }

  // 3. Stage 1 In-Memory Pre-Filter (O(1) math, zero external calls)
  const preFiltered: Array<{ pair: DexPairLike; priority: number }> = [];
  for (const [mint, pairs] of pairsByMint.entries()) {
    const best = selectBestSolanaPair(pairs, mint);
    if (!best) continue;
    const mc = Number(best.marketCap) || Number(best.fdv) || 0;
    const liq = Number(best.liquidity?.usd) || 0;
    const quoteMint = String(best.quoteToken?.address ?? '').trim();

    // Must have verified hard quote, basic liquidity, and sit in early-to-viral-base range ($18K–$900K)
    if (!VERIFIED_HARD_QUOTE_MINTS[quoteMint]) continue;
    const volH1 = Number(best.volume?.h1) || 0;
    const inBasicRange = mc >= 18_000 && mc <= 450_000 && liq >= 7_500;
    const inViralBaseD = mc > 450_000 && mc <= 900_000 && volH1 >= 220_000 && liq >= 38_000;
    if (!inBasicRange && !inViralBaseD) continue;

    const s1 = evaluateAntiTopLayer(best, cfg);
    // Prioritize Sleeper Revival + tokens that pass AntiTop or are clean pullback-watch candidates
    const priority = s1.passed
      ? s1.metrics.isSleeperRevival
        ? 4
        : 3
      : s1.isPullbackWatch
      ? 2
      : 1;
    preFiltered.push({ pair: best, priority });
  }

  preFiltered.sort((a, b) => {
    if (b.priority !== a.priority) return b.priority - a.priority;
    return (Number(b.pair.volume?.m5) || 0) - (Number(a.pair.volume?.m5) || 0);
  });

  // Cap deep Stage-2 audits at 10 with bounded concurrency (4 at a time) -> fast & rate-limit safe!
  const stage2Queue = preFiltered.slice(0, 10).map((x) => x.pair);
  const stage1PassedCount = preFiltered.filter((x) => x.priority >= 2).length;

  const audited = await mapBounded(stage2Queue, 4, (pair) => auditSinglePair(pair, cfg));

  // Rank: ENTRY_READY first, then WATCH_PULLBACK, then REJECTED; within each by totalScore desc
  const verdictOrder = { ENTRY_READY: 0, WATCH_PULLBACK: 1, REJECTED: 2 };
  audited.sort((a, b) => {
    if (verdictOrder[a.verdict] !== verdictOrder[b.verdict]) {
      return verdictOrder[a.verdict] - verdictOrder[b.verdict];
    }
    return b.totalScore - a.totalScore;
  });

  return {
    scannedAt: Date.now(),
    durationMs: Date.now() - t0,
    discoveredCount: mintList.length,
    stage1PassedCount,
    stage2AuditedCount: audited.length,
    entryReadyCount: audited.filter((t) => t.verdict === 'ENTRY_READY').length,
    watchPullbackCount: audited.filter((t) => t.verdict === 'WATCH_PULLBACK').length,
    rejectedCount: audited.filter((t) => t.verdict === 'REJECTED').length,
    config: cfg,
    tokens: audited,
    feedNotes,
  };
}
