import { ENTRY_CORRIDORS, PRIORITY_TIP_BUFFER_PCT } from './constants.ts';
import type { DexPairLike } from './security.ts';
import type { AntiTopResult, CorridorDefinition, GateCheck, ScanConfig } from './types.ts';

function asNum(v: unknown, fallback = 0): number {
  const n = Number(v);
  return Number.isFinite(n) ? n : fallback;
}

const VETERAN_MIN_AGE_MINUTES = 7 * 24 * 60; // 10,080 menit (7 hari)

export function classifyCleanCorridor(
  mc: number,
  includeMidCapCorridor: boolean,
  ageMinutes = 60
): { corridor: CorridorDefinition | null; redZoneReason: string | null } {
  if (mc < 25_000) {
    return {
      corridor: null,
      redZoneReason: `MC terlalu dini ($${Math.round(mc).toLocaleString()} < $25K) — belum teruji di kurva/kelulusan.`,
    };
  }
  if (!includeMidCapCorridor && mc > 210_000) {
    return {
      corridor: null,
      redZoneReason: `Di luar fokus early-entry (MC $${Math.round(mc).toLocaleString()} > $210K).`,
    };
  }
  if (mc > 10_000_000) {
    return {
      corridor: null,
      redZoneReason: `MEGA-CAP TOP ZONE ($${Math.round(mc).toLocaleString()} > $10M): Di luar batas atas Jalur 2 Veteran Swing.`,
    };
  }
  if (mc > 850_000) {
    if (ageMinutes >= VETERAN_MIN_AGE_MINUTES) {
      const vet = ENTRY_CORRIDORS.find((c) => c.id === 'E_VETERAN_SWING') ?? null;
      return { corridor: vet, redZoneReason: null };
    }
    return {
      corridor: null,
      redZoneReason: `LATE-STAGE TOP ZONE ($${Math.round(mc).toLocaleString()} > $850K · Usia < 7 Hari): Risiko beli di puncak koin baru — Jalur 2 hanya untuk koin veteran >= 7 hari.`,
    };
  }

  const matched =
    ENTRY_CORRIDORS.find((c) => c.id !== 'E_VETERAN_SWING' && mc >= c.minMc && mc <= c.maxMc) ??
    null;
  return { corridor: matched, redZoneReason: matched ? null : 'Di luar koridor entry aman.' };
}

/**
 * LAYER 2: ANTI-TOP EXTENSION, DELAY PROJECTION & ORGANIC ORDERFLOW GUARD (v5.6 Dual-Track).
 *  - Jalur 1 (EARLY_MICROCAP): $25K–$850K Anti-ATH & Anti-20m-Death
 *  - Jalur 2 (VETERAN_SWING): $850K–$10M, usia >= 7 hari, keluar-masuk cepat (+18%..+35%)
 */
export function evaluateAntiTopLayer(pair: DexPairLike, cfg: ScanConfig): AntiTopResult {
  const checks: GateCheck[] = [];
  const vetoReasons: string[] = [];
  const waitReasons: string[] = [];

  const spotMc = asNum(pair.marketCap) || asNum(pair.fdv);
  const liqUsd = asNum(pair.liquidity?.usd);
  const liqMcRatioPct = spotMc > 0 ? (liqUsd / spotMc) * 100 : 0;

  const pM5 = asNum(pair.priceChange?.m5);
  const pH1 = asNum(pair.priceChange?.h1);
  const pH6 = asNum(pair.priceChange?.h6);
  const pH24 = asNum(pair.priceChange?.h24);

  const volM5 = asNum(pair.volume?.m5);
  const volH1 = asNum(pair.volume?.h1);
  const volH24 = asNum(pair.volume?.h24);

  const buysM5 = Math.max(0, Math.round(asNum(pair.txns?.m5?.buys)));
  const sellsM5 = Math.max(0, Math.round(asNum(pair.txns?.m5?.sells)));
  const totalTxM5 = buysM5 + sellsM5;
  const buysH1 = Math.max(0, Math.round(asNum(pair.txns?.h1?.buys)));
  const sellsH1 = Math.max(0, Math.round(asNum(pair.txns?.h1?.sells)));
  const totalTxH1 = buysH1 + sellsH1;
  const txnsAny = pair.txns as Record<string, { buys?: unknown; sells?: unknown } | undefined> | undefined;
  const buysH6 = Math.max(0, Math.round(asNum(txnsAny?.h6?.buys)));
  const sellsH6 = Math.max(0, Math.round(asNum(txnsAny?.h6?.sells)));
  const totalTxH6 = buysH6 + sellsH6;
  const totalTx24h =
    Math.max(0, Math.round(asNum(pair.txns?.h24?.buys))) +
    Math.max(0, Math.round(asNum(pair.txns?.h24?.sells)));

  const buySellRatioM5 = buysM5 / Math.max(1, sellsM5);
  const buyRatio5mPctEarly = totalTxM5 > 0 ? (buysM5 / totalTxM5) * 100 : 50;
  const buyRatio1hPctEarly = totalTxH1 > 0 ? (buysH1 / totalTxH1) * 100 : 50;
  const buyRatio6hPctEarly = totalTxH6 > 0 ? (buysH6 / totalTxH6) * 100 : buyRatio1hPctEarly;
  const avgTicketM5Usd = totalTxM5 > 0 ? volM5 / totalTxM5 : 0;
  const avgTicketH1Usd = totalTxH1 > 0 ? volH1 / totalTxH1 : 0;
  const turnoverH1Ratio = spotMc > 0 ? volH1 / spotMc : 0;
  const baselineM5Vol = volH1 > 0 ? volH1 / 12 : 0;
  const volAccelRatio = baselineM5Vol > 0 ? volM5 / baselineM5Vol : 1.0;

  const createdAtMs = asNum(pair.pairCreatedAt, 0);
  const ageMinutes =
    createdAtMs > 0 ? Math.max(1, (Date.now() - createdAtMs) / 60_000) : 60;
  const isDailyCoin = ageMinutes >= 20 * 60;

  const isBoosted = asNum(pair.boosts?.active) > 0;
  const isViralVelocity =
    !isBoosted &&
    avgTicketM5Usd >= 30.0 &&
    ((volM5 >= 20_000 && volH1 >= 165_000 && turnoverH1Ratio >= 0.85) ||
      (volM5 >= 32_000 && volH1 >= 220_000 && turnoverH1Ratio >= 0.55));
  const isMegaViralFlow =
    isViralVelocity && volM5 >= 45_000 && volH1 >= 350_000 && avgTicketM5Usd >= 65.0;
  const isEarlyCorridorAFastTrack =
    ageMinutes >= 6 &&
    ageMinutes < 10 &&
    spotMc <= 88_000 &&
    volM5 >= 18_000 &&
    volH1 >= 45_000 &&
    turnoverH1Ratio >= 0.8 &&
    liqUsd >= 14_000 &&
    buyRatio5mPctEarly >= 55.0 &&
    buyRatio1hPctEarly >= 55.0 &&
    avgTicketM5Usd >= 32.0 &&
    totalTxH1 >= 220 &&
    !isBoosted;
  const isHyperViralFastTrack =
    isEarlyCorridorAFastTrack ||
    (ageMinutes >= 6 &&
      ageMinutes < 10 &&
      volM5 >= 25_000 &&
      volH1 >= 90_000 &&
      turnoverH1Ratio >= 1.0 &&
      liqUsd >= 16_000 &&
      buyRatio1hPctEarly >= 54.0 &&
      buyRatio5mPctEarly >= 52.0 &&
      avgTicketM5Usd >= 32.0 &&
      totalTxH1 >= 350 &&
      !isBoosted);
  const isPullbackContractionOk = volM5 >= 12_000;

  const hasCommunityPresence =
    (Array.isArray(pair.info?.socials) && pair.info.socials.length > 0) ||
    (Array.isArray(pair.info?.websites) && pair.info.websites.length > 0);
  const { corridor: rawCorridor, redZoneReason } = classifyCleanCorridor(
    spotMc,
    cfg.includeMidCapCorridor,
    ageMinutes
  );
  const isCorridorD = rawCorridor?.id === 'D_VIRAL_BASE';
  const isCorridorDViralBase =
    isCorridorD && volH1 >= 250_000 && volM5 >= 15_000 && liqUsd >= 40_000 && pM5 >= 0.0 && pM5 <= 18.0;
  const isVeteranSwing = rawCorridor?.id === 'E_VETERAN_SWING' && ageMinutes >= VETERAN_MIN_AGE_MINUTES;
  const signalTrack = isVeteranSwing ? 'VETERAN_SWING' : 'EARLY_MICROCAP';
  const corridor = isCorridorD && !isCorridorDViralBase ? null : rawCorridor;

  // 1. Corridor Check (Jalur 1 A–D Micro-Cap | Jalur 2 E Veteran Swing)
  const inSafeCorridor = Boolean(corridor);
  const effectiveRedZoneReason =
    isCorridorD && !isCorridorDViralBase
      ? `Late-Stage Top ($${Math.round(spotMc).toLocaleString()} > $390K tanpa syarat Viral Base: Vol1h >= $250K, Vol5m >= $15K, Liq >= $40K).`
      : redZoneReason;

  checks.push({
    id: 'corridor_zone',
    layer: 'ANTI_TOP',
    label: isVeteranSwing
      ? 'Jalur 2 · Veteran Swing Corridor ($850K–$10M, >=7 Hari)'
      : 'Entry Corridor (A–D Micro-Cap) & Zona Atas',
    passed: inSafeCorridor,
    severity: 'HARD_VETO',
    actual: `$${Math.round(spotMc).toLocaleString()}`,
    threshold: cfg.includeMidCapCorridor
      ? 'Jalur 1: $25K–$850K · Jalur 2 Veteran (>=7hri): $850K–$10M'
      : '$25K–$92K · $92K–$210K',
    detail: corridor ? corridor.label : effectiveRedZoneReason ?? 'Di luar koridor entry.',
  });
  if (!inSafeCorridor) {
    vetoReasons.push(effectiveRedZoneReason ?? 'Di luar rentang MC early-entry');
  }

  // 2. Delay-Compensated Projected Entry MC & Remaining Headroom to Next Wall
  const velPctPerSec = Math.max(0, pM5 / 300);
  const accelMult = volAccelRatio >= 1.8 ? 1.35 : volAccelRatio >= 1.3 ? 1.15 : 1.0;
  const projectedDelayDriftPct = Math.min(
    18.0,
    Number((velPctPerSec * cfg.reactionDelaySec * accelMult).toFixed(2))
  );
  const projectedEntryMc = spotMc * (1 + projectedDelayDriftPct / 100);
  const nextWallMc = isVeteranSwing
    ? Math.min(corridor?.nextWallMc ?? 13_500_000, spotMc * 1.35)
    : corridor
    ? corridor.nextWallMc
    : spotMc * 1.35;
  const grossHeadroomPct =
    projectedEntryMc > 0 ? ((nextWallMc - projectedEntryMc) / projectedEntryMc) * 100 : 0;
  const estExecFrictionPct = isVeteranSwing ? 1.8 + PRIORITY_TIP_BUFFER_PCT : 3.2 + PRIORITY_TIP_BUFFER_PCT;
  const netHeadroomPct = grossHeadroomPct - estExecFrictionPct;

  const projectedHitsWall = corridor ? projectedEntryMc > corridor.maxMc * 1.03 : false;
  const headroomOk = !projectedHitsWall && netHeadroomPct >= cfg.minNetHeadroomPct;

  checks.push({
    id: 'delay_headroom',
    layer: 'ANTI_TOP',
    label: `Delay ${cfg.reactionDelaySec}s Headroom to Wall`,
    passed: headroomOk,
    severity: 'WAIT_DIP',
    actual: `Proj $${Math.round(projectedEntryMc).toLocaleString()} (+${projectedDelayDriftPct.toFixed(
      1
    )}% drift) · Net +${netHeadroomPct.toFixed(1)}%`,
    threshold: `Net Headroom >= +${cfg.minNetHeadroomPct.toFixed(0)}% below $${Math.round(
      nextWallMc
    ).toLocaleString()}`,
    detail: headroomOk
      ? 'Setelah kompensasi delay eksekusi, ruang naik menuju tembok resistansi masih sangat sehat.'
      : 'Ruang naik menuju tembok take-profit terlalu sempit setelah memperhitungkan delay masuk — tunggu pullback.',
  });
  if (!headroomOk && inSafeCorridor) {
    waitReasons.push(
      `Ruang profit pasca-delay ${cfg.reactionDelaySec}s sempit (+${netHeadroomPct.toFixed(1)}% < +${cfg.minNetHeadroomPct}%)`
    );
  }

  // 2b. Survival Age Guard (v5.1: >= 10m — atau jalur cepat Early Fast-Track 6–9m!)
  const survivalAgeOk = ageMinutes >= 10 || isHyperViralFastTrack;
  checks.push({
    id: 'survival_age',
    layer: 'ANTI_TOP',
    label: 'Usia Bertahan Pasca-Migrasi (Early-Wave >=10m)',
    passed: survivalAgeOk,
    severity: 'WAIT_DIP',
    actual: `${Math.round(ageMinutes)} menit${isVeteranSwing ? ' (VETERAN >=7HRI)' : isHyperViralFastTrack ? ' (EARLY FAST-TRACK)' : ''}`,
    threshold: isVeteranSwing ? '>= 7 hari (10.080m)' : '>= 10 menit (atau >=6m Early Fast-Track)',
    detail: isVeteranSwing
      ? `Koin veteran mapan berusia ${(ageMinutes / 1440).toFixed(0)} hari — lolos syarat usia Jalur 2.`
      : isHyperViralFastTrack
      ? `Jalur Cepat Early Fast-Track aktif (${Math.round(ageMinutes)}m, Vol5m $${Math.round(volM5).toLocaleString()}).`
      : survivalAgeOk
      ? `Sudah bertahan ${Math.round(ageMinutes)} menit pasca-migrasi (lolos dump sniper awal).`
      : `Terlalu baru (${Math.round(ageMinutes)}m < 10m) — tunggu lolos dump sniper 0–9m pertama.`,
  });
  if (!survivalAgeOk) {
    waitReasons.push(`Terlalu baru (${Math.round(ageMinutes)}m < 10m) — tunggu lolos dump sniper awal`);
  }

  // 3. Anti-Parabolic Cap & Anti-Freefall Floor (v5.6: Track 1 vs Track 2 Veteran Swing!)
  const isProvenRunner = ageMinutes >= 120 && totalTx24h >= 1_200;
  const isEarlyCorridorAWave =
    !isVeteranSwing &&
    ageMinutes < 30 &&
    spotMc <= 92_000 &&
    volM5 >= 12_000 &&
    turnoverH1Ratio >= 0.9 &&
    buyRatio5mPctEarly >= 54.0 &&
    buyRatio1hPctEarly >= 54.0 &&
    !isBoosted;
  const effectiveMaxH1 = isVeteranSwing
    ? 28.0
    : isProvenRunner
    ? cfg.maxH1ChangePct
    : isEarlyCorridorAWave
    ? 185
    : Math.min(cfg.maxH1ChangePct, 115);
  const effectiveMaxM5 = isVeteranSwing ? 12.0 : isCorridorDViralBase ? 18.0 : cfg.maxM5ChangePct;
  const viralMildPullbackAllowed =
    !isVeteranSwing &&
    isViralVelocity &&
    volM5 >= 25_000 &&
    volH1 >= 250_000 &&
    buyRatio5mPctEarly >= 52.0 &&
    buyRatio1hPctEarly >= 53.0;
  const m5Floor = isVeteranSwing ? 0.8 : isDailyCoin ? 1.0 : viralMildPullbackAllowed ? -1.5 : 0.0;
  const h1Floor = isVeteranSwing ? 2.0 : isDailyCoin ? -4.0 : viralMildPullbackAllowed ? 0.0 : -2.0;
  const isCorridorARevival =
    !isVeteranSwing &&
    ageMinutes >= 90 &&
    spotMc <= 92_000 &&
    volM5 >= 10_000 &&
    volH1 >= 55_000 &&
    turnoverH1Ratio >= 0.85 &&
    buyRatio5mPctEarly >= 54.0 &&
    buyRatio1hPctEarly >= 54.0 &&
    buyRatio6hPctEarly >= 50.0 &&
    pH6 <= 160.0 &&
    pH24 <= 220.0 &&
    !isBoosted;
  const m5TooHot = pM5 > effectiveMaxM5;
  const m5FallingKnife = pM5 < m5Floor || pH1 < h1Floor;
  const viralH1CapOk = !isVeteranSwing && (isMegaViralFlow || (isViralVelocity && pH1 <= 420.0));
  const h1TooExtended = pH1 > effectiveMaxH1 && !viralH1CapOk && !isCorridorARevival;
  const momentumClean = !m5TooHot && !m5FallingKnife && !h1TooExtended;

  checks.push({
    id: 'anti_parabolic',
    layer: 'ANTI_TOP',
    label: isVeteranSwing
      ? 'Momentum Swing Veteran (5m +0.8..+12%, 1h +2..+28%)'
      : 'Anti-Freefall Floor & Kontrol Kecepatan 5m/1h',
    passed: momentumClean,
    severity: m5FallingKnife ? (isVeteranSwing ? 'WAIT_DIP' : 'HARD_VETO') : 'WAIT_DIP',
    actual: `5m ${pM5 >= 0 ? '+' : ''}${pM5.toFixed(1)}% · 1h ${pH1 >= 0 ? '+' : ''}${pH1.toFixed(1)}%${isVeteranSwing ? ' (VETERAN)' : isViralVelocity ? ' (VIRAL)' : ''}`,
    threshold: `5m ${m5Floor.toFixed(1)}%..+${effectiveMaxM5}% · 1h ${h1Floor.toFixed(1)}%..+${effectiveMaxH1}%`,
    detail: m5TooHot
      ? `Candle 5m sudah melonjak vertikal +${pM5.toFixed(1)}% — dilarang kejar pucuk, tunggu koreksi sehat.`
      : h1TooExtended
      ? `Kenaikan 1 jam +${pH1.toFixed(1)}% melewati batas (+${effectiveMaxH1}%) — rawan distribusi puncak.`
      : m5FallingKnife
      ? isVeteranSwing
        ? `Swing veteran belum bangun/sedang merah (5m ${pM5.toFixed(1)}%, 1h ${pH1.toFixed(1)}%) — tunggu pantulan hijau.`
        : `Anti-Freefall: Tren sedang melemah/merah (5m ${pM5.toFixed(1)}%, 1h ${pH1.toFixed(1)}%) — dilarang beli pisau jatuh.`
      : isViralVelocity && pH1 > effectiveMaxH1
      ? `Ledakan viral aktif (Vol5m $${Math.round(volM5).toLocaleString()}, 5m terkendali ${pM5.toFixed(1)}%).`
      : 'Laju kenaikan hijau terkendali (belum pucuk vertikal dan tidak sedang jatuh).',
  });
  if (m5FallingKnife) {
    if (isVeteranSwing) waitReasons.push(`Swing veteran belum aktif (5m ${pM5.toFixed(1)}%, 1h ${pH1.toFixed(1)}%)`);
    else vetoReasons.push(`Anti-Freefall: harga melemah (5m ${pM5.toFixed(1)}%, 1h ${pH1.toFixed(1)}%)`);
  }
  if (m5TooHot) waitReasons.push(`Overextended 5m (+${pM5.toFixed(1)}% > +${effectiveMaxM5}%) — tunggu pullback`);
  if (h1TooExtended) waitReasons.push(`Parabolik 1h (+${pH1.toFixed(1)}% > +${effectiveMaxH1}%) — rawan puncak`);

  // 4. Liquidity Floor & Depth Ratio
  const reqLiq = isVeteranSwing
    ? 80_000
    : Math.max(cfg.minLiquidityUsd, corridor?.minLiqUsd ?? cfg.minLiquidityUsd);
  const reqLiqRatio = isVeteranSwing ? 4.5 : corridor?.minLiqMcRatioPct ?? 8.0;
  const liqOk = liqUsd >= reqLiq && liqMcRatioPct >= reqLiqRatio;

  checks.push({
    id: 'liquidity_depth',
    layer: 'ORDERFLOW',
    label: 'Liquidity & LP/MC Ratio',
    passed: liqOk,
    severity: 'HARD_VETO',
    actual: `$${Math.round(liqUsd).toLocaleString()} (${liqMcRatioPct.toFixed(1)}% of MC)`,
    threshold: `>= $${Math.round(reqLiq).toLocaleString()} & >= ${reqLiqRatio.toFixed(1)}% LP/MC`,
    detail: liqOk
      ? 'Kedalaman likuiditas proporsional terhadap Market Cap.'
      : 'Likuiditas terlalu tipis terhadap MC — rawan slippage berat saat keluar.',
  });
  if (!liqOk) {
    vetoReasons.push(`Likuiditas tipis ($${Math.round(liqUsd).toLocaleString()}, ${liqMcRatioPct.toFixed(1)}% MC)`);
  }

  // 5. v5.6 Demand Velocity, Net Buyer Dominance, Paid-Boost Guard & Ticket Floor
  const minTicketM5 = isVeteranSwing ? 35.0 : isDailyCoin ? 22.0 : Math.max(28.0, corridor?.minAvgTicketM5Usd ?? 28.0);
  const minTicketH1 = isVeteranSwing ? 28.0 : isDailyCoin ? 20.0 : 24.0;
  const isMicroBotnetH1 = totalTxH1 >= 250 && avgTicketH1Usd < minTicketH1;
  const isMicroTicketM5 = totalTxM5 >= 12 && avgTicketM5Usd < minTicketM5;
  const isWhaleSiphonM5 = buysM5 >= 12 && buyRatio5mPctEarly >= 62 && pM5 < -0.5;
  const h6MinBuy = isVeteranSwing ? 50.0 : 49.0;
  const h1MinBuy = isVeteranSwing ? 53.0 : 52.0;
  const isH6SellBleed = ageMinutes >= 360 && totalTxH6 >= 20 && buyRatio6hPctEarly < h6MinBuy;
  const minAccelReq = isVeteranSwing ? 0.45 : 0.55;
  const veteranVolOk = isVeteranSwing && volM5 >= 5_000;
  const isVolDying =
    ageMinutes >= 25 &&
    volH1 > 0 &&
    volAccelRatio < minAccelReq &&
    !isPullbackContractionOk &&
    !veteranVolOk;
  const isSidewaysChurnH1 = !isDailyCoin && turnoverH1Ratio >= 1.8 && pH1 < 8.0;
  const isExtremeWashChurn = !isDailyCoin && turnoverH1Ratio >= 8.0 && (pH1 < 60.0 || pM5 < 1.0);
  const whaleAbsorption =
    !isVeteranSwing &&
    pM5 >= 3.0 &&
    volM5 >= 15_000 &&
    buyRatio5mPctEarly >= 44.0 &&
    buyRatio5mPctEarly < 52.0 &&
    buyRatio1hPctEarly >= 51.0;
  const buyerPressureOk =
    whaleAbsorption ||
    (buyRatio5mPctEarly >= 52.0 &&
      buyRatio1hPctEarly >= h1MinBuy &&
      (!isVeteranSwing || buyRatio6hPctEarly >= 50.0));
  const veteranCommunityBoostOk =
    isVeteranSwing && liqUsd >= 80_000 && volH1 >= 18_000 && buyRatio1hPctEarly >= 53.0 && buyRatio6hPctEarly >= 50.0;
  const paidBoostTrap =
    isBoosted &&
    !veteranCommunityBoostOk &&
    !(volM5 >= 25_000 && buyRatio5mPctEarly >= 55.0 && pM5 >= 2.0);
  const isAgedCoin = !isVeteranSwing && ageMinutes >= 720;
  const isSteadyOrganicClimber =
    !isVeteranSwing &&
    !isAgedCoin &&
    ageMinutes >= 45 &&
    spotMc <= 145_000 &&
    volM5 >= 4_500 &&
    volH1 >= 45_000 &&
    turnoverH1Ratio >= 0.7 &&
    buyRatio1hPctEarly >= 54.0 &&
    buyRatio5mPctEarly >= 52.0 &&
    avgTicketM5Usd >= 28.0 &&
    totalTxH1 >= 300;
  const isEarlySub30m = !isVeteranSwing && !isAgedCoin && !isSteadyOrganicClimber && ageMinutes < 30 && spotMc <= 150_000;
  const earlyAgeScale = Math.max(0.4, Math.min(1.0, ageMinutes / 30));
  const reqVolM5 = isVeteranSwing ? 2_500 : isAgedCoin ? 2_000 : isSteadyOrganicClimber ? 4_500 : 8_000;
  const reqVolH1 = isVeteranSwing
    ? 18_000
    : isAgedCoin
    ? 12_000
    : isSteadyOrganicClimber
    ? 45_000
    : isEarlySub30m
    ? Math.max(22_000, Math.round(55_000 * earlyAgeScale))
    : 55_000;
  const reqTurnH1 = isVeteranSwing
    ? 0.004
    : isAgedCoin
    ? 0.15
    : isSteadyOrganicClimber
    ? 0.7
    : isEarlySub30m
    ? Math.max(0.35, Number((0.6 * earlyAgeScale).toFixed(2)))
    : 0.6;
  const volVelocityOk =
    totalTxM5 >= 8 &&
    volM5 >= reqVolM5 &&
    (ageMinutes < 10 || (volH1 >= reqVolH1 && turnoverH1Ratio >= reqTurnH1));
  const hasMinActivity = volVelocityOk && buyerPressureOk;

  const baseMinTx24h = corridor?.minTx24h ?? 60;
  const effectiveMinTx24h =
    ageMinutes < 15 ? Math.min(baseMinTx24h, Math.max(18, Math.round(ageMinutes * 6))) : baseMinTx24h;
  const tx24hOk = totalTx24h >= effectiveMinTx24h;

  const flowOk =
    !isMicroBotnetH1 &&
    !isMicroTicketM5 &&
    !isWhaleSiphonM5 &&
    !isH6SellBleed &&
    !isVolDying &&
    !isSidewaysChurnH1 &&
    !isExtremeWashChurn &&
    !paidBoostTrap &&
    hasMinActivity &&
    tx24hOk;

  checks.push({
    id: 'organic_orderflow',
    layer: 'ORDERFLOW',
    label: isVeteranSwing
      ? 'Arus Swing Veteran & Dominasi Buyer (5m>=52%, 1h>=53%, 6h>=50%)'
      : 'Demand Organik, Anti-Paid-Boost & Dominasi Buyer (>=52%)',
    passed: flowOk,
    severity: 'HARD_VETO',
    actual: `Vol5m $${Math.round(volM5).toLocaleString()} (${volAccelRatio.toFixed(2)}x · Buy ${buyRatio5mPctEarly.toFixed(0)}%) · ${isBoosted ? (veteranCommunityBoostOk ? 'Community Boost' : 'PAID BOOST') : 'Organik'}`,
    threshold: `Vol5m >= $${reqVolM5.toLocaleString()} · Vol1h >= $${reqVolH1.toLocaleString()} · Buy >= ${h1MinBuy}%`,
    detail: paidBoostTrap
      ? 'Jebakan Paid Boost Exit-Liquidity: token membayar boost DexScreener tanpa demand organik masif — rawan dump bebas.'
      : isExtremeWashChurn
      ? `Extreme Wash-Bot Churn: turnover 1h ${turnoverH1Ratio.toFixed(1)}x MC tapi harga flat (1h ${pH1.toFixed(1)}%, 5m ${pM5.toFixed(1)}%).`
      : isMicroBotnetH1
      ? `Botnet spam H1 terdeteksi (${totalTxH1} tx dengan tiket rata-rata receh $${avgTicketH1Usd.toFixed(1)}).`
      : isWhaleSiphonM5
      ? `Divergensi Whale Siphon: klik beli ramai (${buysM5}B/${sellsM5}S) tapi harga turun (${pM5.toFixed(1)}%).`
      : isH6SellBleed
      ? `Distribusi multi-jam terdeteksi (6h buy hanya ${buyRatio6hPctEarly.toFixed(0)}% < ${h6MinBuy}%) — rawan dead-cat bounce lalu turun bebas.`
      : isVolDying
      ? `Volume 5m sekarat ($${Math.round(volM5).toLocaleString()}, accel ${volAccelRatio.toFixed(2)}x) — bensin momentum habis.`
      : isSidewaysChurnH1
      ? `Wash-trading/distribusi sideways: turnover 1h ${turnoverH1Ratio.toFixed(2)}x MC tapi harga hanya ${pH1.toFixed(1)}%.`
      : isMicroTicketM5
      ? `Wash-bot 5m terdeteksi: tiket rata-rata terlalu kecil ($${avgTicketM5Usd.toFixed(1)} < $${minTicketM5}).`
      : !volVelocityOk
      ? `Arus modal terlalu lemah (Vol5m $${Math.round(volM5).toLocaleString()}, Vol1h $${Math.round(volH1).toLocaleString()}, Turn1h ${turnoverH1Ratio.toFixed(3)}x) — rawan mati.`
      : !buyerPressureOk
      ? `Buyer belum dominan (5m ${buyRatio5mPctEarly.toFixed(0)}%, 1h ${buyRatio1hPctEarly.toFixed(0)}%, 6h ${buyRatio6hPctEarly.toFixed(0)}%).`
      : !tx24hOk
      ? `Riwayat transaksi pair masih di bawah ambang (${totalTx24h} < ${effectiveMinTx24h}).`
      : isPullbackContractionOk && volAccelRatio < minAccelReq
      ? `Kontraksi volume pullback sehat: Vol5m tetap tebal $${Math.round(volM5).toLocaleString()}.`
      : whaleAbsorption
      ? 'Demand viral kuat + Whale Absorption terdeteksi (harga naik meski retail TP).'
      : 'Kecepatan arus modal sehat dengan dominasi buyer net-positive.',
  });

  if (paidBoostTrap) vetoReasons.push('Jebakan Paid Boost DexScreener (rawan exit liquidity dump)');
  if (isExtremeWashChurn) vetoReasons.push(`Extreme Wash-Bot Churn (Turn1h ${turnoverH1Ratio.toFixed(1)}x tapi harga flat)`);
  if (isMicroBotnetH1) vetoReasons.push(`Botnet micro-ticket H1 ($${avgTicketH1Usd.toFixed(1)}/tx)`);
  if (isWhaleSiphonM5) vetoReasons.push('Whale Siphon: klik beli tinggi tapi harga minus');
  if (isH6SellBleed) vetoReasons.push(`Distribusi 6h (${buyRatio6hPctEarly.toFixed(0)}% buy < ${h6MinBuy}%) — konsolidasi turun`);
  if (isVolDying) vetoReasons.push(`Volume 5m sekarat ($${Math.round(volM5).toLocaleString()}, accel ${volAccelRatio.toFixed(2)}x < ${minAccelReq}x)`);
  if (isSidewaysChurnH1) vetoReasons.push('Wash-trading / distribusi sideways H1');
  if (isMicroTicketM5) vetoReasons.push(`Wash-bot 5m ($${avgTicketM5Usd.toFixed(1)}/tx < $${minTicketM5})`);
  if (!volVelocityOk) vetoReasons.push(`Arus modal lesu (Vol5m $${Math.round(volM5).toLocaleString()}, Vol1h $${Math.round(volH1).toLocaleString()})`);
  if (!buyerPressureOk) vetoReasons.push(`Buyer belum dominan (5m ${buyRatio5mPctEarly.toFixed(0)}%, 1h ${buyRatio1hPctEarly.toFixed(0)}%)`);
  if (!tx24hOk) vetoReasons.push(`Transaksi pair kurang (${totalTx24h} < ${effectiveMinTx24h})`);

  // 6. Anti-Late-ATH & Pre-ATH Extension Guard (v5.6: Track 1 Anti +1000% | Track 2 Anti Kejar Swing >+65% 24h)
  const isExtremeParabolicLate =
    !isDailyCoin && (pH24 >= 2_000 || pH6 >= 1_200 || (pH1 >= 900 && !isCorridorARevival));
  const isIntradayLateNonBase =
    !isDailyCoin &&
    !isCorridorDViralBase &&
    !isMegaViralFlow &&
    !isCorridorARevival &&
    ((spotMc >= 135_000 && (pH1 >= 420 || pH6 >= 420 || pH24 >= 420)) ||
      (ageMinutes >= 45 && (pH24 >= 550 || pH6 >= 480)) ||
      pH24 >= 850 ||
      pH6 >= 600);
  const isVeteranOverextended = isVeteranSwing && (pH6 > 35 || pH24 > 65);
  const isNearLocalAth =
    isVeteranOverextended || (!isVeteranSwing && ageMinutes >= 360 && (pH6 > 160 || pH24 > 260));
  const lateAthHardVeto = isExtremeParabolicLate || isIntradayLateNonBase;
  const preAthOk = !isNearLocalAth && !lateAthHardVeto;
  checks.push({
    id: 'pre_ath_extension',
    layer: 'ANTI_TOP',
    label: isVeteranSwing
      ? 'Anti-Kejar-Pucuk Veteran Swing (6h <= +35%, 24h <= +65%)'
      : 'Anti-Late-ATH & Pre-ATH Extension Guard (Anti +1000% dari Lahir)',
    passed: preAthOk,
    severity: lateAthHardVeto ? 'HARD_VETO' : 'WAIT_DIP',
    actual: `1h ${pH1 >= 0 ? '+' : ''}${pH1.toFixed(0)}% · 6h ${pH6 >= 0 ? '+' : ''}${pH6.toFixed(0)}% · 24h ${pH24 >= 0 ? '+' : ''}${pH24.toFixed(0)}%`,
    threshold: isVeteranSwing
      ? 'Veteran Swing: 6h <= +35% & 24h <= +65%'
      : 'MC>=$135K: < +420% pasca-migrasi (kecuali Mega-Viral) · Usia >=6j: 6h<=+160% & 24h<=+260%',
    detail: lateAthHardVeto
      ? `Terlalu telat / ATH Pasca-Lahir (MC $${Math.round(spotMc).toLocaleString()}, 1h ${pH1 >= 0 ? '+' : ''}${pH1.toFixed(0)}%, 24h ${pH24 >= 0 ? '+' : ''}${pH24.toFixed(0)}%) — rawan langsung dump!`
      : preAthOk
      ? 'Masih di fase awal sebelum zona puncak — ruang swing masih sehat.'
      : isVeteranOverextended
      ? `Veteran Swing sudah naik terlalu jauh hari ini (6h ${pH6 >= 0 ? '+' : ''}${pH6.toFixed(0)}%, 24h ${pH24 >= 0 ? '+' : ''}${pH24.toFixed(0)}%) — tunggu pullback -10% s/d -20%.`
      : 'Koin berusia >= 6 jam sudah naik terlalu tinggi (mendekati ATH lokal) — tunggu pullback/base baru.',
  });
  if (lateAthHardVeto) {
    vetoReasons.push(
      `Terlalu telat di ATH pasca-lahir (MC $${Math.round(spotMc).toLocaleString()}, 1h ${pH1 >= 0 ? '+' : ''}${pH1.toFixed(0)}%, 24h ${pH24 >= 0 ? '+' : ''}${pH24.toFixed(0)}%)`
    );
  } else if (!preAthOk) {
    waitReasons.push(
      isVeteranOverextended
        ? `Veteran Swing overextended (6h ${pH6 >= 0 ? '+' : ''}${pH6.toFixed(0)}%, 24h ${pH24 >= 0 ? '+' : ''}${pH24.toFixed(0)}%) — tunggu pullback`
        : `Mendekati ATH lokal (6h ${pH6 >= 0 ? '+' : ''}${pH6.toFixed(0)}%, 24h ${pH24 >= 0 ? '+' : ''}${pH24.toFixed(0)}%) — tunggu pullback`
    );
  }

  // 7. Sleeper Revival / Daily Consolidation Breakout Detector (khusus Track 1 usia >= 12 jam)
  const avg5mVol24h = volH24 > 0 ? volH24 / 288 : 0;
  const volWake5mRatio = avg5mVol24h > 0 ? volM5 / avg5mVol24h : 0;
  const volH1Share24hPct = volH24 > 0 ? (volH1 / volH24) * 100 : 0;
  const buyRatio5mPct = totalTxM5 > 0 ? (buysM5 / totalTxM5) * 100 : 50;
  const buyRatio1hPct = totalTxH1 > 0 ? (buysH1 / totalTxH1) * 100 : 50;

  const hasVolumeWakeup =
    (volM5 >= 1_200 && volWake5mRatio >= 2.5) ||
    (volH1 >= 6_000 && volH1Share24hPct >= 25);
  const isEarlyPreAthWave =
    pM5 >= 0.5 && pM5 <= 22.0 && pH1 >= -2.0 && pH1 <= 85.0 && pH6 <= 120.0 && pH24 <= 220.0;
  const hasRevivalBuyers =
    buyRatio5mPct >= 50.0 && buyRatio1hPct >= 50.0 && buyRatio6hPctEarly >= 49.0 && buysM5 >= 5;

  const isSleeperRevival =
    !isVeteranSwing &&
    ageMinutes >= 720 &&
    inSafeCorridor &&
    headroomOk &&
    preAthOk &&
    hasVolumeWakeup &&
    isEarlyPreAthWave &&
    hasRevivalBuyers;

  const passed = vetoReasons.length === 0 && waitReasons.length === 0;
  const isPullbackWatch = vetoReasons.length === 0 && waitReasons.length > 0;

  return {
    passed,
    isPullbackWatch,
    corridor,
    redZoneReason,
    vetoReasons,
    waitReasons,
    checks,
    metrics: {
      spotMc: Math.round(spotMc),
      projectedEntryMc: Math.round(projectedEntryMc),
      projectedDelayDriftPct,
      nextWallMc: Math.round(nextWallMc),
      grossHeadroomPct: Number(grossHeadroomPct.toFixed(1)),
      netHeadroomPct: Number(netHeadroomPct.toFixed(1)),
      ageMinutes: Number(ageMinutes.toFixed(1)),
      liqUsd: Math.round(liqUsd),
      liqMcRatioPct: Number(liqMcRatioPct.toFixed(1)),
      pM5: Number(pM5.toFixed(2)),
      pH1: Number(pH1.toFixed(2)),
      pH6: Number(pH6.toFixed(2)),
      pH24: Number(pH24.toFixed(2)),
      volM5: Math.round(volM5),
      volH1: Math.round(volH1),
      buysM5,
      sellsM5,
      buySellRatioM5: Number(buySellRatioM5.toFixed(2)),
      avgTicketM5Usd: Number(avgTicketM5Usd.toFixed(1)),
      avgTicketH1Usd: Number(avgTicketH1Usd.toFixed(1)),
      turnoverH1Ratio: Number(turnoverH1Ratio.toFixed(2)),
      volAccelRatio: Number(volAccelRatio.toFixed(2)),
      isSleeperRevival,
      volWake5mRatio: Number(volWake5mRatio.toFixed(2)),
      volH1Share24hPct: Number(volH1Share24hPct.toFixed(1)),
      isViralVelocity,
      isPullbackContractionOk,
      isCorridorDViralBase,
      isHyperViralFastTrack,
      isSteadyOrganicClimber,
      isBoosted,
      hasCommunityPresence,
      isVeteranSwing,
      signalTrack,
    },
  };
}
