import {
  DEPTH_STRESS_MULTIPLIER,
  SOL_MINT,
  TEST_BUY_LAMPORTS,
} from './constants.ts';
import type { ExecutionAuditResult, GateCheck, ScanConfig } from './types.ts';

interface JupQuoteResponse {
  outAmount?: string;
  priceImpactPct?: string | number;
  swapUsdValue?: string | number;
  routePlan?: Array<Record<string, unknown>>;
}

async function fetchJupQuote(
  inputMint: string,
  outputMint: string,
  amountRaw: string,
  timeoutMs = 2800
): Promise<{ ok: true; data: JupQuoteResponse } | { ok: false; rateLimited: boolean; error: string }> {
  const url =
    `https://lite-api.jup.ag/swap/v1/quote` +
    `?inputMint=${encodeURIComponent(inputMint)}` +
    `&outputMint=${encodeURIComponent(outputMint)}` +
    `&amount=${encodeURIComponent(amountRaw)}` +
    `&slippageBps=250`;

  for (let attempt = 0; attempt < 2; attempt++) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const resp = await fetch(url, {
        signal: controller.signal,
        headers: {
          Accept: 'application/json',
        },
      });
      clearTimeout(timer);

      if (resp.status === 429) {
        if (attempt === 0) {
          await new Promise((r) => setTimeout(r, 320));
          continue;
        }
        return { ok: false, rateLimited: true, error: 'Jupiter rate limited (HTTP 429)' };
      }
      if (!resp.ok) {
        return { ok: false, rateLimited: false, error: `HTTP ${resp.status}` };
      }
      const json = (await resp.json()) as JupQuoteResponse;
      if (!json || !json.outAmount || BigInt(json.outAmount) <= 0n) {
        return { ok: false, rateLimited: false, error: 'Zero output quote' };
      }
      return { ok: true, data: json };
    } catch (err) {
      clearTimeout(timer);
      if (attempt === 0) {
        await new Promise((r) => setTimeout(r, 200));
        continue;
      }
      return {
        ok: false,
        rateLimited: false,
        error: err instanceof Error ? err.message : 'Quote timeout',
      };
    }
  }
  return { ok: false, rateLimited: false, error: 'Quote failed' };
}

/**
 * LAYER 3: REAL JUPITER DUAL-SIZE ROUND-TRIP EXECUTION GUARD.
 * Fixes two bugs from the legacy engine:
 *  1. Never clamps a failed 1.0 SOL depth quote to 12.5% (fail-closed!).
 *  2. Always scales Jupiter fractional priceImpactPct * 100 accurately.
 */
export async function evaluateExecutionLayer(
  mint: string,
  cfg: ScanConfig
): Promise<ExecutionAuditResult> {
  const checks: GateCheck[] = [];
  const vetoReasons: string[] = [];

  // Step 1: Simulate 0.1 SOL Buy
  const buyQuote = await fetchJupQuote(SOL_MINT, mint, String(TEST_BUY_LAMPORTS));
  if (!buyQuote.ok) {
    const status = buyQuote.rateLimited ? 'RATE_LIMITED' : 'FAILED';
    checks.push({
      id: 'jup_buy_quote',
      layer: 'EXECUTION',
      label: 'Jupiter 0.1 SOL Buy Route',
      passed: false,
      severity: 'HARD_VETO',
      actual: buyQuote.error,
      threshold: 'Liquid route required',
      detail: buyQuote.rateLimited
        ? 'Jupiter API sedang rate-limit — fail-closed, tidak meloloskan tanpa bukti likuiditas.'
        : 'Tidak ada rute beli likuid 0.1 SOL di Jupiter DEX.',
    });
    vetoReasons.push(
      buyQuote.rateLimited
        ? 'Jupiter API rate-limited (Fail-Closed)'
        : 'Tidak memiliki rute beli 0.1 SOL di Jupiter'
    );
    return {
      passed: false,
      status,
      vetoReasons,
      checks,
      metrics: {
        buyPriceImpactPct: null,
        roundTripLoss01SolPct: null,
        depthLoss1SolPct: null,
        buyRouteHops: null,
        sellRouteHops: null,
        swapUsdValue: null,
      },
    };
  }

  const outTokensRaw = BigInt(buyQuote.data.outAmount!);
  const buyRouteHops = Array.isArray(buyQuote.data.routePlan) ? buyQuote.data.routePlan.length : 1;
  // Jupiter priceImpactPct is fractional (e.g. "0.014" = 1.4%, "1.05" = 105%)
  const rawBuyImpact = Math.abs(Number(buyQuote.data.priceImpactPct ?? 0));
  const buyPriceImpactPct = Number((rawBuyImpact * 100).toFixed(2));
  const swapUsdValue = Number(buyQuote.data.swapUsdValue ?? 0) || null;

  // Step 2: Parallel 0.1 SOL Sell + 1.0 SOL Exit Depth Stress Test
  const depthTokensRaw = outTokensRaw * BigInt(DEPTH_STRESS_MULTIPLIER);
  const [sell01, sellDepth] = await Promise.all([
    fetchJupQuote(mint, SOL_MINT, outTokensRaw.toString()),
    fetchJupQuote(mint, SOL_MINT, depthTokensRaw.toString()),
  ]);

  if (!sell01.ok) {
    const status = sell01.rateLimited ? 'RATE_LIMITED' : 'FAILED';
    checks.push({
      id: 'jup_sell_honeypot',
      layer: 'EXECUTION',
      label: 'Round-Trip 0.1 SOL Sell Test',
      passed: false,
      severity: 'HARD_VETO',
      actual: sell01.error,
      threshold: `Round-trip loss <= ${cfg.maxRoundTripLossPct}%`,
      detail: sell01.rateLimited
        ? 'Kuotasi jual terkena rate-limit — tidak meloloskan tanpa konfirmasi bisa dijual.'
        : 'BAHAYA HONEYPOT: Bisa dibeli 0.1 SOL namun GAGAL dijual kembali ke SOL!',
    });
    vetoReasons.push(
      sell01.rateLimited
        ? 'Jupiter sell quote rate-limited (Fail-Closed)'
        : 'JEBAKAN HONEYPOT: Tidak bisa dijual kembali ke SOL'
    );
    return {
      passed: false,
      status,
      vetoReasons,
      checks,
      metrics: {
        buyPriceImpactPct,
        roundTripLoss01SolPct: null,
        depthLoss1SolPct: null,
        buyRouteHops,
        sellRouteHops: null,
        swapUsdValue,
      },
    };
  }

  const solBack01 = Number(sell01.data.outAmount!);
  const roundTripLoss01SolPct = Number(
    Math.max(0, (1 - solBack01 / TEST_BUY_LAMPORTS) * 100).toFixed(2)
  );
  const sellRouteHops = Array.isArray(sell01.data.routePlan) ? sell01.data.routePlan.length : 1;

  const hopsOk = buyRouteHops <= 3 && sellRouteHops <= 3;
  const impactOk = buyPriceImpactPct <= 4.5;
  const rtOk = roundTripLoss01SolPct <= cfg.maxRoundTripLossPct;

  checks.push({
    id: 'jup_roundtrip_01',
    layer: 'EXECUTION',
    label: 'Round-Trip 0.1 SOL Execution',
    passed: rtOk && impactOk && hopsOk,
    severity: 'HARD_VETO',
    actual: `RT Loss -${roundTripLoss01SolPct}% · Impact ${buyPriceImpactPct}% · Hops ${buyRouteHops}/${sellRouteHops}`,
    threshold: `RT <= ${cfg.maxRoundTripLossPct}% · Impact <= 4.5% · Hops <= 3`,
    detail:
      rtOk && impactOk && hopsOk
        ? 'Beli & jual 0.1 SOL mulus tanpa pajak tersembunyi atau split-route aneh.'
        : 'Biaya pulang-pergi atau slippage 0.1 SOL terlalu tinggi — rawan sandwich MEV atau hidden tax.',
  });
  if (!rtOk) vetoReasons.push(`Round-trip loss 0.1 SOL tinggi (-${roundTripLoss01SolPct}% > ${cfg.maxRoundTripLossPct}%)`);
  if (!impactOk) vetoReasons.push(`Price impact beli 0.1 SOL terlalu besar (${buyPriceImpactPct}% > 4.5%)`);
  if (!hopsOk) vetoReasons.push(`Rute swap terlalu berputar (${buyRouteHops}/${sellRouteHops} hops > 3)`);

  // Step 3: 1.0 SOL Exit Depth Stress Test (NO FAKE FALLBACK CLAMPING!)
  if (!sellDepth.ok) {
    const status = sellDepth.rateLimited ? 'RATE_LIMITED' : 'FAILED';
    checks.push({
      id: 'jup_depth_1sol',
      layer: 'EXECUTION',
      label: 'Exit Depth Stress-Test (1.0 SOL)',
      passed: false,
      severity: 'HARD_VETO',
      actual: sellDepth.error,
      threshold: `1.0 SOL Exit Loss <= ${cfg.maxDepthCliffLossPct}%`,
      detail: sellDepth.rateLimited
        ? 'Stress-test 1.0 SOL terkena rate-limit — fail-closed (tidak ditebak/di-clamp).'
        : 'HOLLOW-BIN / LIKUIDITAS SEMU: 0.1 SOL bisa keluar tapi pencairan 1.0 SOL gagal!',
    });
    vetoReasons.push(
      sellDepth.rateLimited
        ? 'Stress-test 1.0 SOL rate-limited (Fail-Closed)'
        : 'Hollow-Bin Trap: pencairan 1.0 SOL gagal'
    );
    return {
      passed: false,
      status,
      vetoReasons,
      checks,
      metrics: {
        buyPriceImpactPct,
        roundTripLoss01SolPct,
        depthLoss1SolPct: null,
        buyRouteHops,
        sellRouteHops,
        swapUsdValue,
      },
    };
  }

  const solBack1 = Number(sellDepth.data.outAmount!);
  const expected1SolLamports = TEST_BUY_LAMPORTS * DEPTH_STRESS_MULTIPLIER;
  const depthLoss1SolPct = Number(
    Math.max(0, (1 - solBack1 / expected1SolLamports) * 100).toFixed(2)
  );
  const depthOk = depthLoss1SolPct <= cfg.maxDepthCliffLossPct;

  checks.push({
    id: 'jup_depth_1sol',
    layer: 'EXECUTION',
    label: 'Exit Depth Stress-Test (1.0 SOL)',
    passed: depthOk,
    severity: 'HARD_VETO',
    actual: `Exit Loss -${depthLoss1SolPct}%`,
    threshold: `<= ${cfg.maxDepthCliffLossPct}%`,
    detail: depthOk
      ? 'Kedalaman kolam teruji mampu menyerap penjualan 1.0 SOL tanpa jurang slippage.'
      : `Jurang likuiditas DLMM/CLMM: jual 1.0 SOL langsung amblas -${depthLoss1SolPct}%.`,
  });
  if (!depthOk) {
    vetoReasons.push(`Jurang likuiditas 1.0 SOL (-${depthLoss1SolPct}% > ${cfg.maxDepthCliffLossPct}%)`);
  }

  return {
    passed: vetoReasons.length === 0,
    status: 'VERIFIED',
    vetoReasons,
    checks,
    metrics: {
      buyPriceImpactPct,
      roundTripLoss01SolPct,
      depthLoss1SolPct,
      buyRouteHops,
      sellRouteHops,
      swapUsdValue,
    },
  };
}
