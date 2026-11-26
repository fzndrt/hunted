import type {
  AntiTopResult,
  ExecutionAuditResult,
  ScanConfig,
  ScorePillar,
  SecurityAuditResult,
  TradePlan,
  Verdict,
} from './types.ts';

function clamp(v: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, v));
}

/**
 * LAYER 4: TRANSPARENT ADDITIVE CONVICTION SCORE (0–100) + TRADE PLAN.
 * Zero OR-chained bypasses:
 *  - Any Hard Veto in Security, Execution, or Orderflow -> REJECTED.
 *  - Clean Security + Execution + Orderflow, but overextended/Red Zone -> WATCH_PULLBACK.
 *  - All 3 hard layers passed AND score >= minScore -> ENTRY_READY.
 */
export function computeScoreAndVerdict(
  security: SecurityAuditResult,
  antiTop: AntiTopResult,
  execution: ExecutionAuditResult,
  cfg: ScanConfig
): {
  verdict: Verdict;
  verdictHeadline: string;
  totalScore: number;
  pillars: ScorePillar[];
  tradePlan: TradePlan | null;
} {
  const m = antiTop.metrics;
  const sm = security.metrics;
  const em = execution.metrics;

  // Pillar 1: Early Entry Headroom (0–25)
  const headroomPts = clamp((m.netHeadroomPct / 45) * 18, 0, 18);
  const earlyMcBonus =
    antiTop.corridor?.id === 'A_PRE_100K'
      ? 7
      : antiTop.corridor?.id === 'B_POST_100K'
      ? 5
      : antiTop.corridor?.id === 'C_MID_EXPANSION' || antiTop.corridor?.id === 'D_VIRAL_BASE'
      ? 4
      : 0;
  const p1 = Math.round(clamp(headroomPts + earlyMcBonus, 0, 25));

  // Pillar 2: Organic Orderflow Quality (0–25)
  const rawBsPts = clamp(((m.buySellRatioM5 - 1.0) / 1.8) * 11, 0, 11);
  const whaleAbsorption = m.pM5 >= 2.0 && m.buySellRatioM5 >= 0.72 && m.buySellRatioM5 < 1.05;
  const bsPts = whaleAbsorption ? Math.max(rawBsPts, 7.5) : rawBsPts;
  const ticketPts = clamp((m.avgTicketM5Usd / 95) * 8, 0, 8);
  const accelPts = m.isViralVelocity
    ? 6
    : m.isPullbackContractionOk
    ? Math.max(4, clamp(((m.volAccelRatio - 0.8) / 2.2) * 6, 0, 6))
    : clamp(((m.volAccelRatio - 0.8) / 2.2) * 6, 0, 6);
  const p2 = Math.round(clamp(bsPts + ticketPts + accelPts, 0, 25));

  // Pillar 3: Holder Density & Supply Health (0–20)
  let p3 = 0;
  if (security.source === 'RUGCHECK_LIVE') {
    const devPts = sm.creatorBalancePct <= 0.1 ? 5 : sm.creatorBalancePct <= 1.0 ? 3 : 1;
    const distPts =
      sm.top10PurePct >= 5 && sm.top10PurePct <= 20
        ? 6
        : sm.top10PurePct <= 27
        ? 4
        : sm.top10PurePct <= 30
        ? 2
        : 0;
    const density = sm.mcPerHolderUsd ?? 0;
    const densityPts = density > 0 && density <= 130 ? 5 : density > 0 && density <= 210 ? 3 : density > 0 && density <= 280 ? 1 : 0;
    const holderPts = clamp((sm.totalHolders / 800) * 4, 0, 4);
    p3 = Math.round(clamp(devPts + distPts + densityPts + holderPts, 0, 20));
  }

  // Pillar 4: Liquidity Depth & Execution (0–20)
  let p4 = 0;
  if (execution.status === 'VERIFIED' && em.roundTripLoss01SolPct !== null && em.depthLoss1SolPct !== null) {
    const rtPts = clamp(((cfg.maxRoundTripLossPct - em.roundTripLoss01SolPct) / cfg.maxRoundTripLossPct) * 8, 0, 8);
    const depthPts = clamp(((cfg.maxDepthCliffLossPct - em.depthLoss1SolPct) / cfg.maxDepthCliffLossPct) * 7, 0, 7);
    const liqRatioPts = clamp((m.liqMcRatioPct / 20) * 5, 0, 5);
    p4 = Math.round(clamp(rtPts + depthPts + liqRatioPts, 0, 20));
  } else {
    p4 = Math.round(clamp((m.liqMcRatioPct / 25) * 5, 0, 5));
  }

  // Pillar 5: Controlled Ignition, Community DNA & Organic No-Boost (0–10)
  let p5 = 0;
  if (m.isSleeperRevival) {
    p5 = 7;
  } else if (m.pM5 >= 2.0 && m.pM5 <= 16.0) {
    p5 = 7;
  } else if (m.pM5 > 16.0 && m.pM5 <= 24.0) {
    p5 = 5;
  } else if (m.pM5 >= 0 && m.pM5 < 2.0) {
    p5 = 4;
  }
  if (!m.isBoosted) p5 += 2;
  if (m.hasCommunityPresence) p5 += 1;
  p5 = Math.min(10, p5);

  const pillars: ScorePillar[] = [
    {
      name: 'Early Headroom',
      score: p1,
      maxScore: 25,
      summary: `Net +${m.netHeadroomPct.toFixed(1)}% ke tembok $${Math.round(m.nextWallMc).toLocaleString()}`,
    },
    {
      name: 'Organic Orderflow',
      score: p2,
      maxScore: 25,
      summary: `B/S ${m.buySellRatioM5.toFixed(2)}x · Avg $${m.avgTicketM5Usd.toFixed(0)} · Accel ${m.volAccelRatio.toFixed(1)}x`,
    },
    {
      name: 'Supply & Holders',
      score: p3,
      maxScore: 20,
      summary: `Dev ${sm.creatorBalancePct}% · Top10 ${sm.top10PurePct}% · ${sm.totalHolders.toLocaleString()} holders`,
    },
    {
      name: 'Liquidity Execution',
      score: p4,
      maxScore: 20,
      summary:
        em.roundTripLoss01SolPct !== null
          ? `0.1S -${em.roundTripLoss01SolPct}% · 1S -${em.depthLoss1SolPct ?? '?'}%`
          : 'Unverified quote',
    },
    {
      name: m.isSleeperRevival ? 'Sleeper Revival' : 'Controlled Ignition',
      score: p5,
      maxScore: 10,
      summary: m.isSleeperRevival
        ? `Bangun Tidur · Vol x${m.volWake5mRatio.toFixed(1)} · 6h ${m.pH6 >= 0 ? '+' : ''}${m.pH6.toFixed(0)}%`
        : `5m ${m.pM5 >= 0 ? '+' : ''}${m.pM5.toFixed(1)}% · 1h ${m.pH1 >= 0 ? '+' : ''}${m.pH1.toFixed(1)}%`,
    },
  ];

  const totalScore = pillars.reduce((sum, p) => sum + p.score, 0);

  // Build deterministic trade plan whenever corridor exists or token is pullback-watch
  let tradePlan: TradePlan | null = null;
  if (m.spotMc > 0) {
    const baseEntryMc = Math.max(m.spotMc, m.projectedEntryMc);
    let stopPct = antiTop.corridor?.defaultStopLossPct ?? 15;
    let tp1Pct = antiTop.corridor?.tp1GainPct ?? 40;
    let tp2Pct = antiTop.corridor?.tp2GainPct ?? 95;
    if (m.isSleeperRevival) {
      if (antiTop.corridor?.id === 'A_PRE_100K') {
        stopPct = 15;
        tp1Pct = 30;
        tp2Pct = 65;
      } else if (antiTop.corridor?.id === 'B_POST_100K') {
        stopPct = 14;
        tp1Pct = 28;
        tp2Pct = 60;
      } else {
        stopPct = 12;
        tp1Pct = 25;
        tp2Pct = 50;
      }
    }
    const maxSafeEntryMc = antiTop.corridor
      ? Math.round(Math.min(antiTop.corridor.maxMc, baseEntryMc * 1.05))
      : Math.round(m.spotMc * 0.88);
    const hardStopMc = Math.round(baseEntryMc * (1 - stopPct / 100));
    const tp1Mc = Math.round(baseEntryMc * (1 + tp1Pct / 100));
    const tp2Mc = Math.round(baseEntryMc * (1 + tp2Pct / 100));

    tradePlan = {
      maxSafeEntryMc,
      hardStopMc,
      hardStopPct: stopPct,
      tp1Mc,
      tp1Pct,
      tp2Mc,
      tp2Pct,
      riskRewardToTp1: Number((tp1Pct / stopPct).toFixed(2)),
      riskRewardToTp2: Number((tp2Pct / stopPct).toFixed(2)),
    };
  }

  // Strict Hierarchical Verdict (ZERO bypasses!)
  if (!security.passed) {
    return {
      verdict: 'REJECTED',
      verdictHeadline: security.vetoReasons[0] ?? 'Gagal audit keamanan on-chain',
      totalScore,
      pillars,
      tradePlan: null,
    };
  }

  if (!execution.passed) {
    return {
      verdict: 'REJECTED',
      verdictHeadline: execution.vetoReasons[0] ?? 'Gagal simulasi eksekusi Jupiter',
      totalScore,
      pillars,
      tradePlan: null,
    };
  }

  if (antiTop.vetoReasons.length > 0) {
    return {
      verdict: 'REJECTED',
      verdictHeadline: antiTop.vetoReasons[0],
      totalScore,
      pillars,
      tradePlan: null,
    };
  }

  if (antiTop.isPullbackWatch) {
    return {
      verdict: 'WATCH_PULLBACK',
      verdictHeadline: antiTop.waitReasons[0] ?? 'Aman secara on-chain, tunggu pullback atau konfirmasi tembok',
      totalScore,
      pillars,
      tradePlan,
    };
  }

  if (totalScore < cfg.minScore) {
    return {
      verdict: 'WATCH_PULLBACK',
      verdictHeadline: `Kontrak aman tapi skor konviksi ${totalScore}/100 masih di bawah ambang ${cfg.minScore}`,
      totalScore,
      pillars,
      tradePlan,
    };
  }

  return {
    verdict: 'ENTRY_READY',
    verdictHeadline: m.isSleeperRevival
      ? `SLEEPER REVIVAL PRE-ATH · Vol Wake x${m.volWake5mRatio.toFixed(1)} · Target TP1 +${tradePlan?.tp1Pct ?? 30}% / TP2 +${tradePlan?.tp2Pct ?? 65}%`
      : `Lolos 4 layer ketat · ${antiTop.corridor?.label ?? 'Early Corridor'} · Net headroom +${m.netHeadroomPct.toFixed(1)}%`,
    totalScore,
    pillars,
    tradePlan,
  };
}
