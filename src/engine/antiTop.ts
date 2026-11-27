import { ENTRY_CORRIDORS, PRIORITY_TIP_BUFFER_PCT } from './constants.ts';
import type { DexPairLike } from './security.ts';
import type { AntiTopResult, CorridorDefinition, GateCheck, ScanConfig } from './types.ts';

function asNum(v: unknown, fallback = 0): number {
  const n = Number(v);
  return Number.isFinite(n) ? n : fallback;
}

export function classifyCleanCorridor(
  mc: number,
  includeMidCapCorridor: boolean
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
  if (mc > 850_000) {
    return {
      corridor: null,
      redZoneReason: `LATE-STAGE TOP ZONE ($${Math.round(mc).toLocaleString()} > $850K): Risiko beli di puncak tinggi, tidak masuk radar early-entry.`,
    };
  }

  const matched = ENTRY_CORRIDORS.find((c) => mc >= c.minMc && mc <= c.maxMc) ?? null;
  return { corridor: matched, redZoneReason: matched ? null : 'Di luar koridor entry aman.' };
}

/**
 * LAYER 2: ANTI-TOP EXTENSION, DELAY PROJECTION & ORGANIC ORDERFLOW GUARD.
 * Zero 11-flag OR-chain bypasses. Distinguishes clearly between:
 *  - HARD_VETO (manipulative botnet churn, whale siphon, insufficient liquidity, dead volume)
 *  - WAIT_DIP (token is healthy, but currently overextended or above corridor)
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

  const isViralVelocity =
    (volM5 >= 15_000 && volH1 >= 120_000 && turnoverH1Ratio >= 0.7) ||
    (volM5 >= 25_000 && volH1 >= 180_000 && turnoverH1Ratio >= 0.5);
  const isHyperViralFastTrack =
    ageMinutes >= 6 &&
    ageMinutes < 10 &&
    volM5 >= 25_000 &&
    volH1 >= 90_000 &&
    turnoverH1Ratio >= 1.0 &&
    liqUsd >= 16_000 &&
    buyRatio1hPctEarly >= 54.0 &&
    buyRatio5mPctEarly >= 52.0 &&
    totalTxH1 >= 350;
  const isPullbackContractionOk = volM5 >= 12_000;

  const isBoosted = asNum(pair.boosts?.active) > 0;
  const hasCommunityPresence =
    (Array.isArray(pair.info?.socials) && pair.info.socials.length > 0) ||
    (Array.isArray(pair.info?.websites) && pair.info.websites.length > 0);
  const { corridor: rawCorridor, redZoneReason } = classifyCleanCorridor(spotMc, cfg.includeMidCapCorridor);
  const isCorridorD = rawCorridor?.id === 'D_VIRAL_BASE';
  const isCorridorDViralBase =
    isCorridorD && volH1 >= 250_000 && volM5 >= 15_000 && liqUsd >= 40_000 && pM5 >= 0.0 && pM5 <= 18.0;
  const corridor = isCorridorD && !isCorridorDViralBase ? null : rawCorridor;

  // 1. Corridor Check (A–D Viral Base)
  const inSafeCorridor = Boolean(corridor);
  const effectiveRedZoneReason =
    isCorridorD && !isCorridorDViralBase
      ? `Late-Stage Top ($${Math.round(spotMc).toLocaleString()} > $390K tanpa syarat Viral Base: Vol1h >= $250K, Vol5m >= $15K, Liq >= $40K).`
      : redZoneReason;

  checks.push({
    id: 'corridor_zone',
    layer: 'ANTI_TOP',
    label: 'Entry Corridor (A–D Viral Base) & Zona Atas',
    passed: inSafeCorridor,
    severity: 'HARD_VETO',
    actual: `$${Math.round(spotMc).toLocaleString()}`,
    threshold: cfg.includeMidCapCorridor
      ? '$25K–$390K · D:$390K–$850K (Vol1h>=$250K, Liq>=$40K)'
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
  const nextWallMc = corridor ? corridor.nextWallMc : spotMc * 1.35;
  const grossHeadroomPct =
    projectedEntryMc > 0 ? ((nextWallMc - projectedEntryMc) / projectedEntryMc) * 100 : 0;
  const estExecFrictionPct = 3.2 + PRIORITY_TIP_BUFFER_PCT;
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
    actual: `${Math.round(ageMinutes)} menit${isHyperViralFastTrack ? ' (EARLY FAST-TRACK)' : ''}`,
    threshold: '>= 10 menit (atau >=6m Early Fast-Track)',
    detail: isHyperViralFastTrack
      ? `Jalur Cepat Early Fast-Track aktif (${Math.round(ageMinutes)}m, Vol5m $${Math.round(volM5).toLocaleString()}).`
      : survivalAgeOk
      ? `Sudah bertahan ${Math.round(ageMinutes)} menit pasca-migrasi (lolos dump sniper awal).`
      : `Terlalu baru (${Math.round(ageMinutes)}m < 10m) — tunggu lolos dump sniper 0–9m pertama.`,
  });
  if (!survivalAgeOk) {
    waitReasons.push(`Terlalu baru (${Math.round(ageMinutes)}m < 10m) — tunggu lolos dump sniper awal`);
  }

  // 3. Anti-Parabolic Cap & Anti-Freefall Floor (v5: Stop buying falling knives!)
  const isProvenRunner = ageMinutes >= 120 && totalTx24h >= 1_200;
  const effectiveMaxH1 = isProvenRunner ? cfg.maxH1ChangePct : Math.min(cfg.maxH1ChangePct, 115);
  const effectiveMaxM5 = isCorridorDViralBase ? 18.0 : cfg.maxM5ChangePct;
  const viralMildPullbackAllowed =
    isViralVelocity &&
    volM5 >= 25_000 &&
    volH1 >= 250_000 &&
    buyRatio5mPctEarly >= 52.0 &&
    buyRatio1hPctEarly >= 53.0;
  const m5Floor = isDailyCoin ? 1.0 : viralMildPullbackAllowed ? -1.5 : 0.0;
  const h1Floor = isDailyCoin ? -4.0 : viralMildPullbackAllowed ? 0.0 : -2.0;
  const m5TooHot = pM5 > effectiveMaxM5;
  const m5FallingKnife = pM5 < m5Floor || pH1 < h1Floor;
  const h1TooExtended = pH1 > effectiveMaxH1 && !isViralVelocity;
  const momentumClean = !m5TooHot && !m5FallingKnife && !h1TooExtended;

  checks.push({
    id: 'anti_parabolic',
    layer: 'ANTI_TOP',
    label: 'Anti-Freefall Floor & Kontrol Kecepatan 5m/1h',
    passed: momentumClean,
    severity: m5FallingKnife ? 'HARD_VETO' : 'WAIT_DIP',
    actual: `5m ${pM5 >= 0 ? '+' : ''}${pM5.toFixed(1)}% · 1h ${pH1 >= 0 ? '+' : ''}${pH1.toFixed(1)}%${isViralVelocity ? ' (VIRAL)' : ''}`,
    threshold: `5m ${m5Floor.toFixed(1)}%..+${effectiveMaxM5}% · 1h ${h1Floor.toFixed(1)}%..+${effectiveMaxH1}% (atau Viral)`,
    detail: m5TooHot
      ? `Candle 5m sudah melonjak vertikal +${pM5.toFixed(1)}% — dilarang kejar pucuk, tunggu koreksi sehat.`
      : h1TooExtended
      ? `Kenaikan 1 jam +${pH1.toFixed(1)}% tanpa arus modal viral (> +${effectiveMaxH1}%) — rawan distribusi puncak.`
      : m5FallingKnife
      ? `Anti-Freefall: Tren sedang melemah/merah (5m ${pM5.toFixed(1)}%, 1h ${pH1.toFixed(1)}%) — dilarang beli pisau jatuh.`
      : isViralVelocity && pH1 > effectiveMaxH1
      ? `Ledakan viral aktif (Vol5m $${Math.round(volM5).toLocaleString()}, 5m terkendali ${pM5.toFixed(1)}%).`
      : 'Laju kenaikan hijau terkendali (belum pucuk vertikal dan tidak sedang jatuh).',
  });
  if (m5FallingKnife) vetoReasons.push(`Anti-Freefall: harga melemah (5m ${pM5.toFixed(1)}%, 1h ${pH1.toFixed(1)}%)`);
  if (m5TooHot) waitReasons.push(`Overextended 5m (+${pM5.toFixed(1)}% > +${effectiveMaxM5}%) — tunggu pullback`);
  if (h1TooExtended) waitReasons.push(`Parabolik 1h (+${pH1.toFixed(1)}% > +${effectiveMaxH1}%) — rawan puncak`);

  // 4. Liquidity Floor & Depth Ratio
  const reqLiq = Math.max(cfg.minLiquidityUsd, corridor?.minLiqUsd ?? cfg.minLiquidityUsd);
  const reqLiqRatio = corridor?.minLiqMcRatioPct ?? 8.0;
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

  // 5. v5 Viral Demand Velocity, Net Buyer Dominance (>=52%), Paid-Boost Guard & Extreme Wash-Churn Guard
  const minTicketM5 = corridor?.minAvgTicketM5Usd ?? 22.0;
  const isMicroBotnetH1 = totalTxH1 >= 250 && avgTicketH1Usd < 20.0;
  const isMicroTicketM5 = totalTxM5 >= 12 && avgTicketM5Usd < minTicketM5;
  const isWhaleSiphonM5 = buysM5 >= 12 && buyRatio5mPctEarly >= 62 && pM5 < -0.5;
  const isH6SellBleed = ageMinutes >= 360 && totalTxH6 >= 20 && buyRatio6hPctEarly < 49.0;
  const isVolDying = ageMinutes >= 25 && volH1 > 0 && volAccelRatio < 0.55 && !isPullbackContractionOk;
  const isSidewaysChurnH1 = !isDailyCoin && turnoverH1Ratio >= 1.8 && pH1 < 8.0;
  const isExtremeWashChurn = !isDailyCoin && turnoverH1Ratio >= 8.0 && (pH1 < 60.0 || pM5 < 1.0);
  const whaleAbsorption =
    pM5 >= 3.0 && volM5 >= 15_000 && buyRatio5mPctEarly >= 44.0 && buyRatio5mPctEarly < 52.0 && buyRatio1hPctEarly >= 51.0;
  const buyerPressureOk = whaleAbsorption || (buyRatio5mPctEarly >= 52.0 && buyRatio1hPctEarly >= 52.0);
  const paidBoostTrap = isBoosted && !(volM5 >= 25_000 && buyRatio5mPctEarly >= 55.0 && pM5 >= 2.0);
  const isAgedCoin = ageMinutes >= 720;
  const isSteadyOrganicClimber =
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
  const isEarlySub30m = !isAgedCoin && !isSteadyOrganicClimber && ageMinutes < 30 && spotMc <= 150_000;
  const earlyAgeScale = Math.max(0.4, Math.min(1.0, ageMinutes / 30));
  const reqVolM5 = isAgedCoin ? 2_000 : isSteadyOrganicClimber ? 4_500 : 8_000;
  const reqVolH1 = isAgedCoin
    ? 12_000
    : isSteadyOrganicClimber
    ? 45_000
    : isEarlySub30m
    ? Math.max(22_000, Math.round(55_000 * earlyAgeScale))
    : 55_000;
  const reqTurnH1 = isAgedCoin
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
    label: 'Demand Organik, Anti-Paid-Boost & Dominasi Buyer (>=52%)',
    passed: flowOk,
    severity: 'HARD_VETO',
    actual: `Vol5m $${Math.round(volM5).toLocaleString()} (${volAccelRatio.toFixed(2)}x · Buy ${buyRatio5mPctEarly.toFixed(0)}%) · ${isBoosted ? 'PAID BOOST' : 'Organik'}`,
    threshold: `Vol5m >= $${reqVolM5.toLocaleString()} · Buy >= 52% · Non-Boosted · Turn >= ${reqTurnH1.toFixed(2)}x`,
    detail: paidBoostTrap
      ? 'Jebakan Paid Boost Exit-Liquidity: token membayar boost DexScreener tanpa demand organik masif — rawan dump bebas.'
      : isExtremeWashChurn
      ? `Extreme Wash-Bot Churn: turnover 1h ${turnoverH1Ratio.toFixed(1)}x MC tapi harga flat (1h ${pH1.toFixed(1)}%, 5m ${pM5.toFixed(1)}%).`
      : isMicroBotnetH1
      ? `Botnet spam H1 terdeteksi (${totalTxH1} tx dengan tiket rata-rata receh $${avgTicketH1Usd.toFixed(1)}).`
      : isWhaleSiphonM5
      ? `Divergensi Whale Siphon: klik beli ramai (${buysM5}B/${sellsM5}S) tapi harga turun (${pM5.toFixed(1)}%).`
      : isH6SellBleed
      ? `Distribusi multi-jam terdeteksi (6h buy hanya ${buyRatio6hPctEarly.toFixed(0)}% < 49%) — rawan dead-cat bounce lalu turun bebas.`
      : isVolDying
      ? `Volume 5m sekarat ($${Math.round(volM5).toLocaleString()}, accel ${volAccelRatio.toFixed(2)}x) — bensin momentum habis.`
      : isSidewaysChurnH1
      ? `Wash-trading/distribusi sideways: turnover 1h ${turnoverH1Ratio.toFixed(2)}x MC tapi harga hanya ${pH1.toFixed(1)}%.`
      : isMicroTicketM5
      ? `Wash-bot 5m terdeteksi: tiket rata-rata terlalu kecil ($${avgTicketM5Usd.toFixed(1)} < $${minTicketM5}).`
      : !volVelocityOk
      ? `Arus modal terlalu lemah (Vol5m $${Math.round(volM5).toLocaleString()}, Vol1h $${Math.round(volH1).toLocaleString()}, Turn1h ${turnoverH1Ratio.toFixed(2)}x) — rawan mati.`
      : !buyerPressureOk
      ? `Buyer belum dominan (5m ${buyRatio5mPctEarly.toFixed(0)}%, 1h ${buyRatio1hPctEarly.toFixed(0)}% < 52%).`
      : !tx24hOk
      ? `Riwayat transaksi pair masih di bawah ambang (${totalTx24h} < ${effectiveMinTx24h}).`
      : isPullbackContractionOk && volAccelRatio < 0.55
      ? `Kontraksi volume pullback sehat: Vol5m tetap tebal $${Math.round(volM5).toLocaleString()}.`
      : whaleAbsorption
      ? 'Demand viral kuat + Whale Absorption terdeteksi (harga naik meski retail TP).'
      : 'Kecepatan arus modal 100% organik aktif dengan dominasi buyer >= 52%.',
  });

  if (paidBoostTrap) vetoReasons.push('Jebakan Paid Boost DexScreener (rawan exit liquidity dump)');
  if (isExtremeWashChurn) vetoReasons.push(`Extreme Wash-Bot Churn (Turn1h ${turnoverH1Ratio.toFixed(1)}x tapi harga flat)`);
  if (isMicroBotnetH1) vetoReasons.push(`Botnet micro-ticket H1 ($${avgTicketH1Usd.toFixed(1)}/tx)`);
  if (isWhaleSiphonM5) vetoReasons.push('Whale Siphon: klik beli tinggi tapi harga minus');
  if (isH6SellBleed) vetoReasons.push(`Distribusi 6h (${buyRatio6hPctEarly.toFixed(0)}% buy < 49%) — konsolidasi turun`);
  if (isVolDying) vetoReasons.push(`Volume 5m sekarat ($${Math.round(volM5).toLocaleString()}, accel ${volAccelRatio.toFixed(2)}x < 0.55x)`);
  if (isSidewaysChurnH1) vetoReasons.push('Wash-trading / distribusi sideways H1');
  if (isMicroTicketM5) vetoReasons.push(`Wash-bot 5m ($${avgTicketM5Usd.toFixed(1)}/tx < $${minTicketM5})`);
  if (!volVelocityOk) vetoReasons.push(`Arus modal lesu (Vol5m $${Math.round(volM5).toLocaleString()}, Vol1h $${Math.round(volH1).toLocaleString()})`);
  if (!buyerPressureOk) vetoReasons.push(`Buyer belum dominan (5m ${buyRatio5mPctEarly.toFixed(0)}%, 1h ${buyRatio1hPctEarly.toFixed(0)}% < 52%)`);
  if (!tx24hOk) vetoReasons.push(`Transaksi pair kurang (${totalTx24h} < ${effectiveMinTx24h})`);

  // 6. Anti-Late-ATH & Pre-ATH Extension Guard (v5.1: Blokir entry telat setelah naik +1000% s/d +5000%!)
  const isExtremeParabolicLate = !isDailyCoin && (pH24 >= 2_000 || pH6 >= 1_200 || pH1 >= 900);
  const isIntradayLateNonBase =
    !isDailyCoin && ageMinutes >= 60 && !isCorridorDViralBase && (pH24 >= 850 || pH6 >= 600);
  const isNearLocalAth = ageMinutes >= 360 && (pH6 > 160 || pH24 > 260);
  const lateAthHardVeto = isExtremeParabolicLate || isIntradayLateNonBase;
  const preAthOk = !isNearLocalAth && !lateAthHardVeto;
  checks.push({
    id: 'pre_ath_extension',
    layer: 'ANTI_TOP',
    label: 'Anti-Late-ATH & Pre-ATH Extension Guard (Bukan Setelah +1000%–5000%)',
    passed: preAthOk,
    severity: lateAthHardVeto ? 'HARD_VETO' : 'WAIT_DIP',
    actual: `1h ${pH1 >= 0 ? '+' : ''}${pH1.toFixed(0)}% · 6h ${pH6 >= 0 ? '+' : ''}${pH6.toFixed(0)}% · 24h ${pH24 >= 0 ? '+' : ''}${pH24.toFixed(0)}%`,
    threshold: 'Intraday < +850% 24h (ekstrem < +2000%) · Usia >=6j: 6h <= +160% & 24h <= +260%',
    detail: lateAthHardVeto
      ? `Terlalu telat / Post-Parabolic ATH (24h ${pH24 >= 0 ? '+' : ''}${pH24.toFixed(0)}%, 6h ${pH6 >= 0 ? '+' : ''}${pH6.toFixed(0)}%) — bukan lagi entry awal!`
      : preAthOk
      ? 'Masih di fase awal sebelum zona puncak ekstrem — ruang kenaikan masih lebar.'
      : 'Koin berusia >= 6 jam sudah naik terlalu tinggi (mendekati ATH lokal) — tunggu pullback/base baru.',
  });
  if (lateAthHardVeto) {
    vetoReasons.push(
      `Terlalu telat di ATH pasca-parabolik (24h ${pH24 >= 0 ? '+' : ''}${pH24.toFixed(0)}%, 6h ${pH6 >= 0 ? '+' : ''}${pH6.toFixed(0)}%)`
    );
  } else if (!preAthOk) {
    waitReasons.push(
      `Mendekati ATH lokal (6h ${pH6 >= 0 ? '+' : ''}${pH6.toFixed(0)}%, 24h ${pH24 >= 0 ? '+' : ''}${pH24.toFixed(0)}%) — tunggu pullback`
    );
  }

  // 7. Sleeper Revival / Daily Consolidation Breakout Detector (usia >= 12 jam / harian yg baru bangun akumulasi)
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
    },
  };
}
