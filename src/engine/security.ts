import {
  KNOWN_PROTOCOL_AUTHORITIES,
  VERIFIED_HARD_QUOTE_MINTS,
} from './constants.ts';
import type { GateCheck, SecurityAuditResult } from './types.ts';

export interface DexPairLike {
  chainId?: string;
  dexId?: string;
  pairAddress?: string;
  pairCreatedAt?: number;
  priceUsd?: string | number;
  marketCap?: number;
  fdv?: number;
  baseToken?: { address?: string; name?: string; symbol?: string };
  quoteToken?: { address?: string; name?: string; symbol?: string };
  liquidity?: { usd?: number; base?: number; quote?: number };
  volume?: { m5?: number; h1?: number; h6?: number; h24?: number };
  priceChange?: { m5?: number; h1?: number; h6?: number; h24?: number };
  txns?: {
    m5?: { buys?: number; sells?: number };
    h1?: { buys?: number; sells?: number };
    h6?: { buys?: number; sells?: number };
    h24?: { buys?: number; sells?: number };
  };
  boosts?: { active?: number };
  info?: {
    imageUrl?: string;
    websites?: Array<{ url?: string }>;
    socials?: Array<{ type?: string; url?: string }>;
  };
}

export interface RugCheckReportLike {
  rugged?: boolean;
  score?: number;
  score_normalised?: number;
  totalHolders?: number;
  creatorBalance?: number;
  mintAuthority?: string | null;
  freezeAuthority?: string | null;
  token?: {
    decimals?: number;
    supply?: number | string;
    mintAuthority?: string | null;
    freezeAuthority?: string | null;
  };
  token_extensions?: Record<string, unknown> | Array<Record<string, unknown> | string> | null;
  transferFee?: { pct?: number; maxAmount?: number } | null;
  risks?: Array<{ name?: string; level?: string }>;
  topHolders?: Array<{
    address?: string;
    owner?: string;
    pct?: number;
    insider?: boolean;
    uiAmount?: number;
  }>;
  knownAccounts?: Record<string, { name?: string; type?: string }>;
  markets?: Array<{
    pubkey?: string;
    marketType?: string;
    mintA?: string;
    mintB?: string;
    liquidityA?: string;
    liquidityB?: string;
    mintLP?: string;
    lp?: {
      baseMint?: string;
      quoteMint?: string;
      baseUSD?: number;
      quoteUSD?: number;
      lpLockedPct?: number;
    };
  }>;
}

function asNum(v: unknown, fallback = 0): number {
  const n = Number(v);
  return Number.isFinite(n) ? n : fallback;
}

/**
 * LAYER 1: HARD BINARY SECURITY VETO (Zero Bypasses).
 * Fixes all critical security loopholes found in the old monolith:
 *  - Strict mint-address hard-quote allowlist (no ticker spoofing, no liq >= $6k bypass)
 *  - Token-2022 dangerous extension check (null/false safe, active hooks/delegates blocked)
 *  - Pareto Conservation Law (blocks both concentrated cabals AND Nano-Swarm botnets)
 *  - Fail-closed when RugCheck data is missing
 */
export function evaluateSecurityLayer(
  pair: DexPairLike,
  rug: RugCheckReportLike | null,
  minHoldersRequired: number,
  isVeteranSwing = false
): SecurityAuditResult {
  const checks: GateCheck[] = [];
  const vetoReasons: string[] = [];

  const quoteMint = String(pair.quoteToken?.address ?? '').trim();
  const hardQuoteSymbol = VERIFIED_HARD_QUOTE_MINTS[quoteMint] ?? '';
  const hardQuoteVerified = Boolean(hardQuoteSymbol);

  checks.push({
    id: 'hard_quote_mint',
    layer: 'SECURITY',
    label: 'Verified Hard-Quote Mint',
    passed: hardQuoteVerified,
    severity: 'HARD_VETO',
    actual: hardQuoteVerified
      ? `${hardQuoteSymbol} (${quoteMint.slice(0, 6)}...)`
      : `Unverified (${pair.quoteToken?.symbol ?? 'UNKNOWN'})`,
    threshold: 'Strict SOL/USDC/USDT/WBTC/cbBTC/USD1/PUMP mint',
    detail: hardQuoteVerified
      ? 'Pool berpasangan dengan aset likuid terverifikasi (berdasarkan alamat mint, bukan nama ticker).'
      : 'Quote token tidak termasuk daftar mint resmi. Rawan kolam likuiditas palsu.',
  });
  if (!hardQuoteVerified) {
    vetoReasons.push(`Quote asset tidak terverifikasi (${pair.quoteToken?.symbol ?? 'UNKNOWN'})`);
  }

  if (!rug || typeof rug !== 'object') {
    checks.push({
      id: 'rugcheck_available',
      layer: 'SECURITY',
      label: 'On-Chain Security Report',
      passed: false,
      severity: 'HARD_VETO',
      actual: 'Unavailable / Timeout',
      threshold: 'Verified RugCheck JSON required (Fail-Closed)',
      detail: 'Audit kontrak on-chain gagal diambil. Sistem fail-closed menolak entry tanpa bukti keamanan.',
    });
    vetoReasons.push('Audit kontrak RugCheck gagal/timeout (Fail-Closed)');
    return {
      passed: false,
      source: 'UNAVAILABLE',
      vetoReasons,
      checks,
      metrics: {
        mintAuthorityRevoked: false,
        freezeAuthorityRevoked: false,
        hasDangerousToken2022Ext: false,
        transferFeePct: 0,
        creatorBalancePct: 0,
        totalHolders: 0,
        top10PurePct: 0,
        maxSinglePurePct: 0,
        rank5PurePct: 0,
        outsidePoolPct: 0,
        lpLockedPct: null,
        hardQuoteVerified,
        hardQuoteSymbol: hardQuoteSymbol || String(pair.quoteToken?.symbol ?? 'UNKNOWN'),
        hardQuoteReserveRatioPct: null,
        rugScore: null,
      },
    };
  }

  // 1. Rugged flag
  const isRugged = rug.rugged === true;
  if (isRugged) {
    vetoReasons.push('Token ditandai RUGGED oleh RugCheck');
  }

  // 2. Mint & Freeze Authority
  const mintAuthority = rug.token?.mintAuthority ?? rug.mintAuthority ?? null;
  const freezeAuthority = rug.token?.freezeAuthority ?? rug.freezeAuthority ?? null;
  const mintAuthorityRevoked = !mintAuthority;
  const freezeAuthorityRevoked = !freezeAuthority;

  checks.push({
    id: 'mint_freeze_auth',
    layer: 'SECURITY',
    label: 'Mint & Freeze Authority',
    passed: mintAuthorityRevoked && freezeAuthorityRevoked && !isRugged,
    severity: 'HARD_VETO',
    actual: isRugged
      ? 'RUGGED'
      : `Mint: ${mintAuthorityRevoked ? 'Revoked' : 'ACTIVE'} · Freeze: ${freezeAuthorityRevoked ? 'Revoked' : 'ACTIVE'}`,
    threshold: 'Both Revoked & Not Rugged',
    detail:
      mintAuthorityRevoked && freezeAuthorityRevoked && !isRugged
        ? 'Suplai terkunci permanen dan dompet pembeli tidak dapat dibekukan.'
        : 'Otoritas Mint atau Freeze masih aktif — dev dapat mencetak suplai atau membekukan saldo.',
  });
  if (!mintAuthorityRevoked) vetoReasons.push('Mint Authority masih aktif');
  if (!freezeAuthorityRevoked) vetoReasons.push('Freeze Authority masih aktif');

  // 3. Token-2022 Dangerous Extensions (handles null/false cleanly for PumpSwap Token-2022)
  let hasDangerousToken2022Ext = false;
  let extReason = 'Clean SPL / Token-2022';
  const tokExt = rug.token_extensions;
  if (tokExt && !Array.isArray(tokExt) && typeof tokExt === 'object') {
    if (tokExt.nonTransferable === true) {
      hasDangerousToken2022Ext = true;
      extReason = 'NonTransferable aktif';
    }
    const pd = tokExt.permanentDelegate as Record<string, unknown> | null | undefined;
    if (pd && (typeof pd !== 'object' || pd.delegate || pd.authority)) {
      hasDangerousToken2022Ext = true;
      extReason = 'PermanentDelegate aktif';
    }
    const th = tokExt.transferHook as Record<string, unknown> | null | undefined;
    if (th && (typeof th !== 'object' || th.programId || th.authority)) {
      hasDangerousToken2022Ext = true;
      extReason = 'TransferHook aktif';
    }
    const pc = tokExt.pausableConfig as Record<string, unknown> | null | undefined;
    if (pc && (typeof pc !== 'object' || pc.authority || pc.paused)) {
      hasDangerousToken2022Ext = true;
      extReason = 'PausableConfig aktif';
    }
  } else if (Array.isArray(tokExt)) {
    for (const item of tokExt) {
      const name = String(
        typeof item === 'string' ? item : item?.extension ?? item?.name ?? ''
      ).toLowerCase();
      if (['permanentdelegate', 'transferhook', 'nontransferable', 'pausableconfig'].includes(name)) {
        hasDangerousToken2022Ext = true;
        extReason = `Ekstensi ${name} terdeteksi`;
      }
    }
  }

  const transferFeePct = asNum(rug.transferFee?.pct, 0);
  const feeTooHigh = transferFeePct > 2.0;

  checks.push({
    id: 'token2022_ext',
    layer: 'SECURITY',
    label: 'Token-2022 Extensions & Tax',
    passed: !hasDangerousToken2022Ext && !feeTooHigh,
    severity: 'HARD_VETO',
    actual: `${extReason} · Fee ${transferFeePct.toFixed(2)}%`,
    threshold: 'No PermanentDelegate/TransferHook · Fee <= 2.0%',
    detail:
      !hasDangerousToken2022Ext && !feeTooHigh
        ? 'Tidak ada ekstensi penyedot saldo atau pajak transfer tinggi.'
        : `Ditolak: ${hasDangerousToken2022Ext ? extReason : `Pajak transfer ${transferFeePct.toFixed(2)}% > 2%`}.`,
  });
  if (hasDangerousToken2022Ext) vetoReasons.push(`Token-2022 berbahaya: ${extReason}`);
  if (feeTooHigh) vetoReasons.push(`Transfer fee terlalu tinggi (${transferFeePct.toFixed(2)}%)`);

  // 4. Creator Balance Check
  const supplyRaw = asNum(rug.token?.supply, 0);
  const creatorBalRaw = asNum(rug.creatorBalance, 0);
  const creatorBalancePct = supplyRaw > 0 ? (creatorBalRaw / supplyRaw) * 100 : 0;
  const maxCreatorPct = isVeteranSwing ? 1.0 : 2.5;
  const creatorOk = creatorBalancePct <= maxCreatorPct;

  checks.push({
    id: 'dev_balance',
    layer: 'SECURITY',
    label: 'Creator / Dev Holding',
    passed: creatorOk,
    severity: 'HARD_VETO',
    actual: `${creatorBalancePct.toFixed(2)}%`,
    threshold: `<= ${maxCreatorPct.toFixed(2)}%`,
    detail: creatorOk
      ? 'Dompet pembuat kontrak memegang porsi kecil atau sudah nol.'
      : `Dev masih memegang ${creatorBalancePct.toFixed(2)}% suplai (> ${maxCreatorPct}%) — rawan dev dump.`,
  });
  if (!creatorOk) vetoReasons.push(`Dev masih memegang ${creatorBalancePct.toFixed(2)}% (> ${maxCreatorPct}%)`);

  // 5. Vault Separation, LP Lock & True Hard-Quote Backing Ratio
  const marketVaults = new Set<string>(KNOWN_PROTOCOL_AUTHORITIES);
  if (pair.pairAddress) marketVaults.add(pair.pairAddress.toLowerCase());

  if (rug.knownAccounts && typeof rug.knownAccounts === 'object') {
    for (const [addr, meta] of Object.entries(rug.knownAccounts)) {
      const t = String(meta?.type ?? '').toLowerCase();
      if (['amm', 'locker', 'clob', 'burn', 'vault'].includes(t)) {
        marketVaults.add(addr.toLowerCase());
      }
    }
  }

  let lpLockedPct: number | null = null;
  let hardQuoteReserveRatioPct: number | null = null;
  const markets = Array.isArray(rug.markets) ? rug.markets : [];

  const sortedMarkets = [...markets].sort((a, b) => {
    const aUsd = asNum(a.lp?.baseUSD) + asNum(a.lp?.quoteUSD);
    const bUsd = asNum(b.lp?.baseUSD) + asNum(b.lp?.quoteUSD);
    return bUsd - aUsd;
  });

  for (const m of sortedMarkets) {
    if (m.liquidityA) marketVaults.add(m.liquidityA.toLowerCase());
    if (m.liquidityB) marketVaults.add(m.liquidityB.toLowerCase());
    if (m.pubkey) marketVaults.add(m.pubkey.toLowerCase());
    if (m.mintLP) marketVaults.add(m.mintLP.toLowerCase());
  }

  const targetMarket =
    sortedMarkets.find(
      (m) => pair.pairAddress && String(m.pubkey ?? '').toLowerCase() === pair.pairAddress.toLowerCase()
    ) ?? sortedMarkets[0];

  let totalLockedUsd = 0;
  for (const m of sortedMarkets) {
    const mBase = asNum(m.lp?.baseUSD);
    const mQuote = asNum(m.lp?.quoteUSD);
    const mLock = asNum(m.lp?.lpLockedPct, 0);
    if (mLock >= 80 && mBase + mQuote > 0) {
      totalLockedUsd += (mBase + mQuote) * (Math.min(100, mLock) / 100);
    }
  }

  let isUnverifiableNftLp = false;
  if (targetMarket?.lp) {
    const baseUsd = asNum(targetMarket.lp.baseUSD);
    const quoteUsd = asNum(targetMarket.lp.quoteUSD);
    const totalPoolUsd = baseUsd + quoteUsd;
    const baseMint = String(targetMarket.lp.baseMint ?? targetMarket.mintA ?? '').trim();
    const quoteM = String(targetMarket.lp.quoteMint ?? targetMarket.mintB ?? '').trim();
    const lpMintAddr = String(
      (targetMarket.lp as Record<string, unknown>).lpMint ?? targetMarket.mintLP ?? ''
    ).trim();

    if (typeof targetMarket.lp.lpLockedPct === 'number') {
      lpLockedPct = targetMarket.lp.lpLockedPct;
    }

    if (lpMintAddr === '11111111111111111111111111111111' && (lpLockedPct ?? 0) < 80) {
      isUnverifiableNftLp = true;
    }

    if (totalPoolUsd > 0) {
      let hardSideUsd = 0;
      if (VERIFIED_HARD_QUOTE_MINTS[baseMint]) hardSideUsd = baseUsd;
      else if (VERIFIED_HARD_QUOTE_MINTS[quoteM]) hardSideUsd = quoteUsd;
      hardQuoteReserveRatioPct = (hardSideUsd / totalPoolUsd) * 100;
    }
  }

  const minLockedUsdReq = isVeteranSwing ? 80_000 : 18_000;
  const lpLockOk =
    lpLockedPct === null ? true : lpLockedPct >= 80 || totalLockedUsd >= minLockedUsdReq;
  const hardReserveOk = hardQuoteReserveRatioPct === null ? hardQuoteVerified : hardQuoteReserveRatioPct >= 22;

  checks.push({
    id: 'lp_security',
    layer: 'SECURITY',
    label: 'LP Lock & Hard-Quote Backing',
    passed: lpLockOk && hardReserveOk,
    severity: 'HARD_VETO',
    actual: `Locked: ${
      lpLockedPct !== null
        ? isUnverifiableNftLp
          ? '0% (NFT/DLMM)'
          : `${lpLockedPct.toFixed(1)}%`
        : 'N/A'
    } · Hard Side: ${
      hardQuoteReserveRatioPct !== null ? `${hardQuoteReserveRatioPct.toFixed(1)}%` : 'N/A'
    }`,
    threshold: 'LP Lock >= 80% (or >= $18K locked pool) · Hard-Quote Reserve >= 22%',
    detail:
      lpLockOk && hardReserveOk
        ? 'Cadangan aset keras di kolam seimbang dan likuiditas utama terkunci.'
        : isUnverifiableNftLp
        ? 'Pool berbasis posisi NFT (Meteora DLMM/DAMM v2) tanpa kunci LP terverifikasi — rawan penarikan likuiditas sepihak.'
        : 'Kolam LP tidak terkunci memadai atau dominan single-sided token tanpa cadangan SOL/USDC cukup.',
  });
  if (!lpLockOk) {
    vetoReasons.push(
      isUnverifiableNftLp
        ? 'Pool Meteora NFT/DLMM tanpa kunci LP terverifikasi (rawan LP pull)'
        : `LP Lock rendah (${lpLockedPct?.toFixed(1)}% < 80%)`
    );
  }
  if (!hardReserveOk) {
    vetoReasons.push(`Cadangan Hard-Quote tipis (${hardQuoteReserveRatioPct?.toFixed(1)}% < 22%)`);
  }

  // 6. Pure Holder Distribution & Pareto Conservation Law (Anti-Cabal & Anti-Nano-Swarm)
  const rawHolders = Array.isArray(rug.topHolders) ? rug.topHolders : [];
  let poolHoldingsPct = 0;
  const pureHolders: number[] = [];
  let insiderHoldersCount = 0;

  for (const h of rawHolders) {
    const pct = asNum(h.pct, 0);
    const addr = String(h.address ?? '').toLowerCase();
    const owner = String(h.owner ?? '').toLowerCase();
    if (marketVaults.has(addr) || marketVaults.has(owner)) {
      poolHoldingsPct += pct;
      continue;
    }
    if (h.insider) insiderHoldersCount += 1;
    pureHolders.push(pct);
  }

  pureHolders.sort((a, b) => b - a);
  const top10PurePct = pureHolders.slice(0, 10).reduce((s, v) => s + v, 0);
  const maxSinglePurePct = pureHolders[0] ?? 0;
  const rank5PurePct = pureHolders[4] ?? 0;
  const outsidePoolPct = Math.max(0, 100 - poolHoldingsPct);
  const totalHolders = asNum(rug.totalHolders, 0);

  const rugScoreVal = typeof rug.score_normalised === 'number' ? rug.score_normalised : rug.score ?? null;
  const warnOrDangerRisks = Array.isArray(rug.risks)
    ? rug.risks.filter((r) => ['danger', 'error', 'critical', 'warn'].includes(String(r?.level ?? '').toLowerCase())).length
    : 0;
  const createdAtMs = asNum(pair.pairCreatedAt, 0);
  const ageMin = createdAtMs > 0 ? Math.max(1, (Date.now() - createdAtMs) / 60_000) : 60;
  const holdersPerMin = totalHolders > 0 ? Number((totalHolders / ageMin).toFixed(2)) : 0;

  const cleanViralPareto =
    rugScoreVal !== null &&
    rugScoreVal <= 5 &&
    warnOrDangerRisks === 0 &&
    insiderHoldersCount === 0 &&
    creatorBalancePct <= 0.05 &&
    top10PurePct <= 28.0 &&
    totalHolders >= 1000;

  const maxSingleAllowed = isVeteranSwing
    ? 3.5
    : cleanViralPareto
    ? 11.0
    : totalHolders >= 2000 && insiderHoldersCount === 0 && (rugScoreVal === null || rugScoreVal <= 15)
    ? 8.0
    : 6.5;
  const top10Ceiling = isVeteranSwing
    ? 18.0
    : outsidePoolPct >= 80 && totalHolders >= 1000 && insiderHoldersCount === 0 && maxSinglePurePct <= maxSingleAllowed
    ? Math.min(30.0, outsidePoolPct * 0.32)
    : 28.0;

  const spotMcVal = asNum(pair.marketCap) || asNum(pair.fdv);
  const mcPerHolderUsd = totalHolders > 0 && spotMcVal > 0 ? Number((spotMcVal / totalHolders).toFixed(1)) : 0;
  const isDailyToken = ageMin >= 20 * 60;
  const maxMcPerHolder = isVeteranSwing ? 650 : isDailyToken ? 280 : spotMcVal >= 110_000 ? 240 : 260;
  const effectiveMinHolders = isVeteranSwing ? Math.max(2_500, minHoldersRequired) : minHoldersRequired;
  const holderDensityOk = totalHolders === 0 || mcPerHolderUsd <= maxMcPerHolder;

  const toxicRisks = Array.isArray(rug.risks)
    ? rug.risks
        .filter((r) => {
          const lvl = String(r?.level ?? '').toLowerCase();
          const name = String(r?.name ?? '').toLowerCase();
          return (
            ['danger', 'error', 'critical', 'warn'].includes(lvl) &&
            (name.includes('high holder correlation') ||
              name.includes('high market cap per holder') ||
              name.includes('copycat') ||
              name.includes('single holder ownership') ||
              name.includes('low liquidity'))
          );
        })
        .map((r) => String(r?.name ?? 'risk'))
    : [];
  const maxRcScore = isVeteranSwing ? 5 : 35;
  const badRcScore = rugScoreVal !== null && rugScoreVal > maxRcScore;
  const veteranRisksFail = isVeteranSwing && warnOrDangerRisks > 0;
  const rugIntegrityOk = toxicRisks.length === 0 && !badRcScore && !veteranRisksFail;

  checks.push({
    id: 'rugcheck_integrity',
    layer: 'SECURITY',
    label: 'Integritas Risiko RugCheck (Anti-Bundler)',
    passed: rugIntegrityOk,
    severity: 'HARD_VETO',
    actual: rugIntegrityOk
      ? `Score ${rugScoreVal ?? 'N/A'} · Bersih`
      : `Score ${rugScoreVal ?? 'N/A'} · ${toxicRisks.join(', ') || (veteranRisksFail ? `${warnOrDangerRisks} risk` : `Skor > ${maxRcScore}`)}`,
    threshold: isVeteranSwing
      ? 'Veteran Swing: RC Score <= 5 & 0 warning/danger risks'
      : 'RC Score <= 35 & bebas High Holder Correlation / Copycat / Hollow MC',
    detail: rugIntegrityOk
      ? 'Skor RugCheck sehat tanpa sinyal korelasi dompet bundler/cabal.'
      : `Ditolak: Risiko on-chain tinggi (${toxicRisks.join(', ') || `RC score ${rugScoreVal} > ${maxRcScore} / ${warnOrDangerRisks} risk`}).`,
  });
  if (!rugIntegrityOk) {
    vetoReasons.push(`Risiko RugCheck tinggi (${toxicRisks[0] ?? `score ${rugScoreVal} > ${maxRcScore}`})`);
  }

  const isNanoSwarm =
    !isVeteranSwing &&
    ((outsidePoolPct >= 65 &&
      (top10PurePct < 5.2 || maxSinglePurePct < 0.65 || (outsidePoolPct >= 70 && rank5PurePct < 0.45))) ||
      (totalHolders >= 500 && pureHolders.length >= 5 && top10PurePct < 3.5));
  const isCabalConcentrated =
    top10PurePct > top10Ceiling || maxSinglePurePct > maxSingleAllowed || insiderHoldersCount >= 3;
  const holdersEnough = totalHolders === 0 || (totalHolders >= effectiveMinHolders && holderDensityOk);

  // Detect identical twin-wallet split clusters (>= 4 wallets within 0.015% of each other)
  let maxTwinCluster = 1;
  for (let i = 0; i < Math.min(pureHolders.length, 15); i++) {
    let cluster = 1;
    for (let j = i + 1; j < Math.min(pureHolders.length, 15); j++) {
      if (Math.abs(pureHolders[i] - pureHolders[j]) <= 0.015 && pureHolders[i] >= 0.35) {
        cluster += 1;
      }
    }
    if (cluster > maxTwinCluster) maxTwinCluster = cluster;
  }
  const hasTwinCabal = maxTwinCluster >= 4 && totalHolders < 600;

  const paretoOk = !isNanoSwarm && !isCabalConcentrated && !hasTwinCabal && holdersEnough;

  checks.push({
    id: 'pareto_holders',
    layer: 'SECURITY',
    label: 'Smart Viral Pareto & Densitas MC/Holder (Anti-Hollow-Shell)',
    passed: paretoOk,
    severity: 'HARD_VETO',
    actual: `Top10: ${top10PurePct.toFixed(1)}% · Max: ${maxSinglePurePct.toFixed(2)}% · ${totalHolders.toLocaleString()}H ($${Math.round(mcPerHolderUsd)}/H)`,
    threshold: `Top10 3.5%–${top10Ceiling.toFixed(1)}% · >= ${effectiveMinHolders}H · <= $${maxMcPerHolder}/H`,
    detail: isNanoSwarm
      ? 'Pola Nano-Swarm terdeteksi: suplai dipecah ke ratusan/ribuan dompet bot mikro terkoordinasi.'
      : isCabalConcentrated
      ? `Konsentrasi whale/insider terlalu tinggi (Top10 ${top10PurePct.toFixed(1)}%, Max ${maxSinglePurePct.toFixed(2)}%).`
      : hasTwinCabal
      ? `Kluster ${maxTwinCluster} dompet kembar identik terdeteksi (indikasi split-wallet bundler).`
      : !holderDensityOk
      ? `Hollow Shell / Gelembung Cabal: MC/Holder terlalu mahal ($${Math.round(mcPerHolderUsd)}/H > $${maxMcPerHolder}/H) — rawan terjun bebas.`
      : !holdersEnough
      ? `Jumlah holder terlalu sedikit (${totalHolders} < ${effectiveMinHolders}).`
      : cleanViralPareto && maxSinglePurePct > 6.5
      ? `Lolos Smart Viral Pareto (Score ${rugScoreVal}, 0 risk, 0% dev, ${totalHolders.toLocaleString()}H).`
      : 'Kurva distribusi holder & densitas MC/holder sehat mengikuti hukum Pareto organik.',
  });

  if (isNanoSwarm) vetoReasons.push('Pola Nano-Swarm / Decoy Botnet terdeteksi (Hukum Pareto gagal)');
  if (isCabalConcentrated) {
    vetoReasons.push(`Konsentrasi Top-10/Whale tinggi (Top10 ${top10PurePct.toFixed(1)}%, Max ${maxSinglePurePct.toFixed(2)}%)`);
  }
  if (hasTwinCabal) vetoReasons.push(`Kluster ${maxTwinCluster} dompet kembar bundler terdeteksi`);
  if (!holderDensityOk) vetoReasons.push(`Hollow Shell: MC/Holder $${Math.round(mcPerHolderUsd)}/H > $${maxMcPerHolder}/H`);
  if (totalHolders > 0 && totalHolders < effectiveMinHolders) {
    vetoReasons.push(`Holders terlalu minim (${totalHolders} < ${effectiveMinHolders})`);
  }

  return {
    passed: vetoReasons.length === 0,
    source: 'RUGCHECK_LIVE',
    vetoReasons,
    checks,
    metrics: {
      mintAuthorityRevoked,
      freezeAuthorityRevoked,
      hasDangerousToken2022Ext,
      transferFeePct,
      creatorBalancePct: Number(creatorBalancePct.toFixed(2)),
      totalHolders,
      top10PurePct: Number(top10PurePct.toFixed(2)),
      maxSinglePurePct: Number(maxSinglePurePct.toFixed(2)),
      rank5PurePct: Number(rank5PurePct.toFixed(2)),
      outsidePoolPct: Number(outsidePoolPct.toFixed(2)),
      lpLockedPct: lpLockedPct !== null ? Number(lpLockedPct.toFixed(1)) : null,
      hardQuoteVerified,
      hardQuoteSymbol: hardQuoteSymbol || String(pair.quoteToken?.symbol ?? 'UNKNOWN'),
      hardQuoteReserveRatioPct:
        hardQuoteReserveRatioPct !== null ? Number(hardQuoteReserveRatioPct.toFixed(1)) : null,
      rugScore: rugScoreVal,
      holdersPerMin,
      cleanViralPareto,
      mcPerHolderUsd,
    },
  };
}
