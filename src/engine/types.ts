export type Verdict = 'ENTRY_READY' | 'WATCH_PULLBACK' | 'REJECTED';

export type CorridorId = 'A_PRE_100K' | 'B_POST_100K' | 'C_MID_EXPANSION' | 'D_VIRAL_BASE' | 'RED_ZONE';

export interface ScanConfig {
  reactionDelaySec: number;
  minScore: number;
  maxRoundTripLossPct: number;
  maxDepthCliffLossPct: number;
  minNetHeadroomPct: number;
  maxM5ChangePct: number;
  maxH1ChangePct: number;
  minLiquidityUsd: number;
  includeMidCapCorridor: boolean;
}

export interface CorridorDefinition {
  id: CorridorId;
  label: string;
  minMc: number;
  maxMc: number;
  nextWallMc: number;
  minLiqUsd: number;
  minLiqMcRatioPct: number;
  minTx24h: number;
  minAvgTicketM5Usd: number;
  minHolders: number;
  defaultStopLossPct: number;
  tp1GainPct: number;
  tp2GainPct: number;
}

export interface GateCheck {
  id: string;
  layer: 'SECURITY' | 'ANTI_TOP' | 'EXECUTION' | 'ORDERFLOW';
  label: string;
  passed: boolean;
  severity: 'HARD_VETO' | 'WAIT_DIP' | 'INFO';
  actual: string;
  threshold: string;
  detail: string;
}

export interface SecurityAuditResult {
  passed: boolean;
  source: 'RUGCHECK_LIVE' | 'UNAVAILABLE';
  vetoReasons: string[];
  checks: GateCheck[];
  metrics: {
    mintAuthorityRevoked: boolean;
    freezeAuthorityRevoked: boolean;
    hasDangerousToken2022Ext: boolean;
    transferFeePct: number;
    creatorBalancePct: number;
    totalHolders: number;
    top10PurePct: number;
    maxSinglePurePct: number;
    rank5PurePct: number;
    outsidePoolPct: number;
    lpLockedPct: number | null;
    hardQuoteVerified: boolean;
    hardQuoteSymbol: string;
    hardQuoteReserveRatioPct: number | null;
    rugScore: number | null;
    holdersPerMin?: number;
    cleanViralPareto?: boolean;
  };
}

export interface AntiTopResult {
  passed: boolean;
  isPullbackWatch: boolean;
  corridor: CorridorDefinition | null;
  redZoneReason: string | null;
  vetoReasons: string[];
  waitReasons: string[];
  checks: GateCheck[];
  metrics: {
    spotMc: number;
    projectedEntryMc: number;
    projectedDelayDriftPct: number;
    nextWallMc: number;
    grossHeadroomPct: number;
    netHeadroomPct: number;
    ageMinutes: number;
    liqUsd: number;
    liqMcRatioPct: number;
    pM5: number;
    pH1: number;
    pH6: number;
    pH24: number;
    volM5: number;
    volH1: number;
    buysM5: number;
    sellsM5: number;
    buySellRatioM5: number;
    avgTicketM5Usd: number;
    avgTicketH1Usd: number;
    turnoverH1Ratio: number;
    volAccelRatio: number;
    isSleeperRevival: boolean;
    volWake5mRatio: number;
    volH1Share24hPct: number;
    isViralVelocity?: boolean;
    isPullbackContractionOk?: boolean;
    isCorridorDViralBase?: boolean;
    isHyperViralFastTrack?: boolean;
    isSteadyOrganicClimber?: boolean;
  };
}

export interface ExecutionAuditResult {
  passed: boolean;
  status: 'VERIFIED' | 'FAILED' | 'RATE_LIMITED';
  vetoReasons: string[];
  checks: GateCheck[];
  metrics: {
    buyPriceImpactPct: number | null;
    roundTripLoss01SolPct: number | null;
    depthLoss1SolPct: number | null;
    buyRouteHops: number | null;
    sellRouteHops: number | null;
    swapUsdValue: number | null;
  };
}

export interface ScorePillar {
  name: string;
  score: number;
  maxScore: number;
  summary: string;
}

export interface TradePlan {
  maxSafeEntryMc: number;
  hardStopMc: number;
  hardStopPct: number;
  tp1Mc: number;
  tp1Pct: number;
  tp2Mc: number;
  tp2Pct: number;
  riskRewardToTp1: number;
  riskRewardToTp2: number;
}

export interface AuditedToken {
  mint: string;
  pairAddress: string;
  dexId: string;
  name: string;
  symbol: string;
  priceUsd: number;
  verdict: Verdict;
  verdictHeadline: string;
  totalScore: number;
  pillars: ScorePillar[];
  security: SecurityAuditResult;
  antiTop: AntiTopResult;
  execution: ExecutionAuditResult;
  tradePlan: TradePlan | null;
  links: {
    dexScreener: string;
    jupiter: string;
    solscan: string;
    rugcheck: string;
  };
  auditedAt: number;
}

export interface ScanSummary {
  scannedAt: number;
  durationMs: number;
  discoveredCount: number;
  stage1PassedCount: number;
  stage2AuditedCount: number;
  entryReadyCount: number;
  watchPullbackCount: number;
  rejectedCount: number;
  config: ScanConfig;
  tokens: AuditedToken[];
  feedNotes: string[];
}

export type ExitSignalCode =
  | 'HOLDING_SAFE'
  | 'HARD_STOP_LOSS'
  | 'LP_PULL_EMERGENCY'
  | 'WHALE_DUMP_DIVERGENCE'
  | 'TP1_DERISK_BREAKEVEN'
  | 'TP2_TAKE_PROFIT'
  | 'TRAILING_PEAK_EXIT';

export interface TrackedPosition {
  id: string;
  mint: string;
  pairAddress: string;
  symbol: string;
  name: string;
  entryMc: number;
  entryPriceUsd: number;
  entryLiqUsd: number;
  positionUsd: number;
  hardStopPct: number;
  tp1Pct: number;
  tp2Pct: number;
  peakMc: number;
  tp1Hit: boolean;
  entryTs: number;
  lastCheckedTs: number | null;
  liveMc: number | null;
  livePriceUsd: number | null;
  liveLiqUsd: number | null;
  pnlPct: number | null;
  realLpRetentionPct: number | null;
  drawdownFromPeakPct: number | null;
  signal: ExitSignalCode;
  signalHeadline: string;
  signalDetail: string;
  notes?: string;
}
