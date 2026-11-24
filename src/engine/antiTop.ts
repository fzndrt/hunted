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
  if (mc < 22_000) {
    return {
      corridor: null,
      redZoneReason: `MC terlalu dini ($${Math.round(mc).toLocaleString()} < $22K) — belum teruji di kurva/kelulusan.`,
    };
  }
  if (mc > 78_000 && mc < 112_000) {
    return {
      corridor: null,
      redZoneReason: `ZONA MERAH $100K ($${Math.round(mc).toLocaleString()}): Area take-profit sniper kelulusan. Tunggu tembus bersih > $112K atau pullback ke < $78K.`,
    };
  }
  if (mc > 172_000 && mc < 220_000) {
    return {
      corridor: null,
      redZoneReason: `ZONA MERAH $200K ($${Math.round(mc).toLocaleString()}): Plafon dump dev modal kecil. Tunggu konfirmasi bersih > $220K.`,
    };
  }
  if (!includeMidCapCorridor && mc > 172_000) {
    return {
      corridor: null,
      redZoneReason: `Di luar fokus early-entry (MC $${Math.round(mc).toLocaleString()} > $172K).`,
    };
  }
  if (mc > 335_000 && mc <= 520_000) {
    return {
      corridor: null,
      redZoneReason: `ZONA MERAH $450K–$500K ($${Math.round(mc).toLocaleString()}): Tembok profit-taking early whale.`,
    };
  }
  if (mc > 520_000) {
    return {
      corridor: null,
      redZoneReason: `LATE-STAGE TOP ZONE ($${Math.round(mc).toLocaleString()} > $520K): Risiko beli di puncak tinggi, tidak masuk radar early-entry.`,
    };
  }

  const matched = ENTRY_CORRIDORS.find((c) => mc >= c.minMc && mc <= c.maxMc) ?? null;
  return { corridor: matched, redZoneReason: matched ? null : 'Di luar koridor entry aman.' };
}

/**
 * LAYER 2: ANTI-TOP EXTENSION, DELAY PROJECTION & ORGANIC ORDERFLOW GUARD.
 * Zero 11-flag OR-chain bypasses. Distinguishes clearly between:
 *  - HARD_VETO (manipulative botnet churn, whale siphon, insufficient liquidity, dead volume)
 *  - WAIT_DIP (token is healthy, but currently overextended or sitting in a Red Zone)
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
  const totalTx24h =
    Math.max(0, Math.round(asNum(pair.txns?.h24?.buys))) +
    Math.max(0, Math.round(asNum(pair.txns?.h24?.sells)));

  const buySellRatioM5 = buysM5 / Math.max(1, sellsM5);
  const avgTicketM5Usd = totalTxM5 > 0 ? volM5 / totalTxM5 : 0;
  const avgTicketH1Usd = totalTxH1 > 0 ? volH1 / totalTxH1 : 0;
  const turnoverH1Ratio = spotMc > 0 ? volH1 / spotMc : 0;
  const baselineM5Vol = volH1 > 0 ? volH1 / 12 : 0;
  const volAccelRatio = baselineM5Vol > 0 ? volM5 / baselineM5Vol : 1.0;

  const createdAtMs = asNum(pair.pairCreatedAt, 0);
  const ageMinutes =
    createdAtMs > 0 ? Math.max(1, (Date.now() - createdAtMs) / 60_000) : 60;

  const { corridor, redZoneReason } = classifyCleanCorridor(spotMc, cfg.includeMidCapCorridor);

  // 1. Corridor & Red Zone Check
  const inSafeCorridor = Boolean(corridor);
  const isRecoverableRedZone = !inSafeCorridor && spotMc >= 78_000 && spotMc <= 335_000;
  checks.push({
    id: 'corridor_zone',
    layer: 'ANTI_TOP',
    label: 'Entry Corridor & Red Zone',
    passed: inSafeCorridor,
    severity: isRecoverableRedZone ? 'WAIT_DIP' : 'HARD_VETO',
    actual: `$${Math.round(spotMc).toLocaleString()}`,
    threshold: cfg.includeMidCapCorridor ? '$22K–$78K · $112K–$172K · $220K–$335K' : '$22K–$78K · $112K–$172K',
    detail: corridor ? corridor.label : redZoneReason ?? 'Di luar koridor entry.',
  });
  if (!inSafeCorridor) {
    if (isRecoverableRedZone) waitReasons.push(redZoneReason ?? 'Sedang di Zona Merah transisi');
    else vetoReasons.push(redZoneReason ?? 'Di luar rentang MC early-entry');
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
  const estExecFrictionPct = 3.2 + PRIORITY_TIP_BUFFER_PCT; // conservative slippage + fees
  const netHeadroomPct = grossHeadroomPct - estExecFrictionPct;

  const projectedHitsWall = corridor ? projectedEntryMc > corridor.maxMc * 1.02 : false;
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

  // 3. Anti-Parabolic Extension Cap (The #1 reason retail buys the exact top)
  const m5TooHot = pM5 > cfg.maxM5ChangePct;
  const m5FallingKnife = pM5 < -6.0;
  const h1TooExtended = pH1 > cfg.maxH1ChangePct;
  const momentumClean = !m5TooHot && !m5FallingKnife && !h1TooExtended;

  checks.push({
    id: 'anti_parabolic',
    layer: 'ANTI_TOP',
    label: 'Anti-Chasing Extension Guard',
    passed: momentumClean,
    severity: m5FallingKnife ? 'HARD_VETO' : 'WAIT_DIP',
    actual: `5m ${pM5 >= 0 ? '+' : ''}${pM5.toFixed(1)}% · 1h ${pH1 >= 0 ? '+' : ''}${pH1.toFixed(1)}%`,
    threshold: `5m -6%..+${cfg.maxM5ChangePct}% · 1h <= +${cfg.maxH1ChangePct}%`,
    detail: m5TooHot
      ? `Candle 5m sudah melonjak vertikal +${pM5.toFixed(1)}% — dilarang kejar pucuk, tunggu koreksi sehat.`
      : h1TooExtended
      ? `Kenaikan 1 jam sudah parabolik +${pH1.toFixed(1)}% — rawan distribusi early buyer.`
      : m5FallingKnife
      ? `Tekanan jual aktif di 5m (${pM5.toFixed(1)}%) — jangan tangkap pisau jatuh.`
      : 'Laju kenaikan terkendali (bukan candle parabolik vertikal).',
  });
  if (m5FallingKnife) vetoReasons.push(`Tekanan jual aktif 5m (${pM5.toFixed(1)}%)`);
  if (m5TooHot) waitReasons.push(`Overextended 5m (+${pM5.toFixed(1)}% > +${cfg.maxM5ChangePct}%) — tunggu pullback`);
  if (h1TooExtended) waitReasons.push(`Parabolik 1h (+${pH1.toFixed(1)}% > +${cfg.maxH1ChangePct}%) — rawan distribusi`);

  // 4. Liquidity Floor & Depth Ratio
  const reqLiq = Math.max(cfg.minLiquidityUsd, corridor?.minLiqUsd ?? cfg.minLiquidityUsd);
  const reqLiqRatio = corridor?.minLiqMcRatioPct ?? 10.0;
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

  // 5. Organic Orderflow, Anti-Botnet Micro-Ticket & Anti-Siphon Divergence
  const minTicketM5 = corridor?.minAvgTicketM5Usd ?? 18.0;
  const isMicroBotnetH1 = totalTxH1 >= 250 && avgTicketH1Usd < 16.0;
  const isMicroTicketM5 = totalTxM5 >= 15 && avgTicketM5Usd < minTicketM5;
  const isWhaleSiphonM5 = buysM5 >= 12 && buySellRatioM5 >= 1.8 && pM5 < 0;
  const isSidewaysChurnH1 = turnoverH1Ratio >= 1.65 && pH1 < 12.0;
  const hasMinActivity = totalTxM5 >= 8 && volM5 >= 1_500 && buySellRatioM5 >= 1.15;
  const baseMinTx24h = corridor?.minTx24h ?? 100;
  // Freshly graduated DEX pools (PumpSwap / Raydium LaunchLab) reset pair txns.h24 to 0 at migration.
  // Scale pair-level tx floor cleanly for pools < 15m old while Layer 1 still enforces full on-chain holder count.
  const effectiveMinTx24h =
    ageMinutes < 15
      ? corridor?.id === 'A_PRE_100K'
        ? Math.min(baseMinTx24h, Math.max(40, Math.round(ageMinutes * 8)))
        : corridor?.id === 'B_POST_100K'
        ? Math.min(baseMinTx24h, Math.max(60, Math.round(ageMinutes * 18)))
        : Math.min(baseMinTx24h, Math.max(90, Math.round(ageMinutes * 30)))
      : baseMinTx24h;
  const tx24hOk = totalTx24h >= effectiveMinTx24h;

  const flowOk =
    !isMicroBotnetH1 &&
    !isMicroTicketM5 &&
    !isWhaleSiphonM5 &&
    !isSidewaysChurnH1 &&
    hasMinActivity &&
    tx24hOk;

  checks.push({
    id: 'organic_orderflow',
    layer: 'ORDERFLOW',
    label: 'Organic Flow & Anti-Siphon',
    passed: flowOk,
    severity: 'HARD_VETO',
    actual: `B/S ${buySellRatioM5.toFixed(2)}x (${buysM5}B/${sellsM5}S) · Avg5m $${avgTicketM5Usd.toFixed(
      0
    )} · Tx24h ${totalTx24h}`,
    threshold: `B/S >= 1.15x · Avg5m >= $${minTicketM5} · Tx >= ${effectiveMinTx24h}${
      ageMinutes < 15 ? ' (fresh grad)' : ''
    }`,
    detail: isMicroBotnetH1
      ? `Botnet spam H1 terdeteksi (${totalTxH1} tx dengan tiket rata-rata receh $${avgTicketH1Usd.toFixed(1)}).`
      : isWhaleSiphonM5
      ? `Divergensi Whale Siphon: klik beli ramai (${buysM5}B/${sellsM5}S) tapi harga turun (${pM5.toFixed(1)}%).`
      : isSidewaysChurnH1
      ? `Wash-trading/distribusi sideways: turnover 1h ${turnoverH1Ratio.toFixed(2)}x MC tapi harga hanya ${pH1.toFixed(1)}%.`
      : isMicroTicketM5
      ? `Rata-rata transaksi 5m terlalu kecil ($${avgTicketM5Usd.toFixed(1)} < $${minTicketM5}).`
      : !hasMinActivity
      ? 'Aktivitas beli 5 menit terlalu sepi atau rasio B/S < 1.15x.'
      : !tx24hOk
      ? `Riwayat transaksi pair masih di bawah ambang (${totalTx24h} < ${effectiveMinTx24h}).`
      : 'Tekanan beli organik dominan tanpa anomali wash-trading atau siphon.',
  });

  if (isMicroBotnetH1) vetoReasons.push(`Botnet micro-ticket H1 ($${avgTicketH1Usd.toFixed(1)}/tx)`);
  if (isWhaleSiphonM5) vetoReasons.push('Whale Siphon: klik beli tinggi tapi harga minus');
  if (isSidewaysChurnH1) vetoReasons.push('Wash-trading / distribusi sideways H1');
  if (isMicroTicketM5) vetoReasons.push(`Tiket 5m terlalu kecil ($${avgTicketM5Usd.toFixed(1)} < $${minTicketM5})`);
  if (!hasMinActivity) vetoReasons.push(`Volume/arus beli 5m belum kuat ($${Math.round(volM5).toLocaleString()}, B/S ${buySellRatioM5.toFixed(2)}x)`);
  if (!tx24hOk) vetoReasons.push(`Transaksi pair kurang (${totalTx24h} < ${effectiveMinTx24h})`);

  // 6. Pre-ATH Extension Guard (Khusus koin berusia >= 6 jam: jangan kejar yang sudah terbang 3x–4x dekat ATH lokal)
  const isNearLocalAth = ageMinutes >= 360 && (pH6 > 180 || pH24 > 300);
  const preAthOk = !isNearLocalAth;
  checks.push({
    id: 'pre_ath_extension',
    layer: 'ANTI_TOP',
    label: 'Pre-ATH Extension Guard (Usia >= 6 Jam)',
    passed: preAthOk,
    severity: 'WAIT_DIP',
    actual: `6h ${pH6 >= 0 ? '+' : ''}${pH6.toFixed(1)}% · 24h ${pH24 >= 0 ? '+' : ''}${pH24.toFixed(1)}%`,
    threshold: '6h <= +180% & 24h <= +300% (sebelum zona ATH lokal)',
    detail: preAthOk
      ? 'Masih di fase awal sebelum zona ATH — ruang ambil profit puluhan persen tetap sehat.'
      : 'Koin berusia >= 6 jam sudah naik terlalu tinggi (mendekati ATH lokal) — tunggu pullback/base baru.',
  });
  if (!preAthOk) {
    waitReasons.push(
      `Mendekati ATH lokal (6h ${pH6 >= 0 ? '+' : ''}${pH6.toFixed(0)}%, 24h ${pH24 >= 0 ? '+' : ''}${pH24.toFixed(0)}%) — tunggu pullback`
    );
  }

  // 7. Sleeper Revival / Aged Coin Awakening Detector (lahir >= 12 jam / beberapa hari lalu & baru mulai naik sebelum ATH)
  const avg5mVol24h = volH24 > 0 ? volH24 / 288 : 0;
  const volWake5mRatio = avg5mVol24h > 0 ? volM5 / avg5mVol24h : 0;
  const volH1Share24hPct = volH24 > 0 ? (volH1 / volH24) * 100 : 0;
  const buyRatio5mPct = totalTxM5 > 0 ? (buysM5 / totalTxM5) * 100 : 50;
  const buyRatio1hPct = totalTxH1 > 0 ? (buysH1 / totalTxH1) * 100 : 50;

  const hasVolumeWakeup =
    (volM5 >= 1_200 && volWake5mRatio >= 3.5) ||
    (volH1 >= 6_000 && volH1Share24hPct >= 30);
  const isEarlyPreAthWave =
    pM5 >= 1.0 && pM5 <= 22.0 && pH1 >= 2.0 && pH1 <= 85.0 && pH6 <= 140.0 && pH24 <= 220.0;
  const hasRevivalBuyers = buyRatio5mPct >= 54.0 && buyRatio1hPct >= 52.0 && buysM5 >= 4;

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
    },
  };
}
