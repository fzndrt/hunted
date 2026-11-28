import type { CorridorDefinition, ScanConfig } from './types.ts';

export const SOL_MINT = 'So11111111111111111111111111111111111111112';

/**
 * STRICT HARD-QUOTE ALLOWLIST (Solana Mainnet Mint Addresses ONLY).
 * Zero ticker-string spoofing and zero liquidity-threshold bypasses.
 */
export const VERIFIED_HARD_QUOTE_MINTS: Record<string, string> = {
  So11111111111111111111111111111111111111112: 'SOL',
  EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v: 'USDC',
  Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB: 'USDT',
  '2u1tszSeqZ3qBWF3uNGPFc8TzMk2tdiwknnRMWGWjGWH': 'USDG',
  USD1ttGY1N17NEEHLmELoaybftRBUSErhqYiQzvEmuB: 'USD1',
  '3NZ9JMVBmGAqocybic2c7LQCJScmgsAZ6vQqTDzcqmJh': 'WBTC',
  cbbtcf3aa214zXHbiAZQwf4122FBYbraNdFqgw4iMij: 'cbBTC',
  mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So: 'mSOL',
  J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn: 'JitoSOL',
  bSo13r4TkiE4KumL71LsHTPpL2euBYLFx6h9HP3piy1: 'bSOL',
  pumpCmXqMfrsAkQ5r49WcJnRayYRqmXz6ae8H7H9Dfn: 'PUMP',
};

/**
 * Known AMM / Program / Burn authorities that must NEVER be counted as retail holders
 * OR mislabeled as Smart Money whales.
 */
export const KNOWN_PROTOCOL_AUTHORITIES = new Set<string>([
  '5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1'.toLowerCase(), // Raydium Authority V4
  '9xQeWvG816bUx9EPjHmaT23yvVM2ZWbrrpZb9PusVFin'.toLowerCase(), // Serum DEX v3 Program
  '11111111111111111111111111111111'.toLowerCase(), // System Program
  '1nc1nerator11111111111111111111111111111111'.toLowerCase(), // Solana Incinerator
  '675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8'.toLowerCase(), // Raydium AMM v4 Program
  'pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA'.toLowerCase(), // PumpSwap AMM Program
  'CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C'.toLowerCase(), // Raydium CPMM Program
  'LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo'.toLowerCase(), // Meteora DLMM Program
]);

export const SOLANA_BASE58_REGEX = /^[1-9A-HJ-NP-Za-km-z]{32,44}$/;

export function isValidSolanaMint(value: string): boolean {
  return SOLANA_BASE58_REGEX.test(value.trim());
}

export const DEFAULT_SCAN_CONFIG: ScanConfig = {
  reactionDelaySec: 22,
  minScore: 80,
  maxRoundTripLossPct: 5.5,
  maxDepthCliffLossPct: 13.5,
  minNetHeadroomPct: 18.0,
  maxM5ChangePct: 22.0,
  maxH1ChangePct: 210.0,
  minLiquidityUsd: 12_000,
  includeMidCapCorridor: true,
};

/**
 * v4 PumpSwap-native Early-Entry & Viral Base Corridors ($25K–$850K)
 * with raised organic holder depth to block weak dying launches.
 */
export const ENTRY_CORRIDORS: CorridorDefinition[] = [
  {
    id: 'A_PRE_100K',
    label: 'Corridor A · Early Post-Migration ($25K–$92K)',
    minMc: 25_000,
    maxMc: 92_000,
    nextWallMc: 125_000,
    minLiqUsd: 12_000,
    minLiqMcRatioPct: 12.0,
    minTx24h: 60,
    minAvgTicketM5Usd: 28.0,
    minHolders: 250,
    defaultStopLossPct: 16,
    tp1GainPct: 35,
    tp2GainPct: 85,
  },
  {
    id: 'B_POST_100K',
    label: 'Corridor B · Momentum Breakout ($92K–$210K)',
    minMc: 92_000,
    maxMc: 210_000,
    nextWallMc: 300_000,
    minLiqUsd: 18_000,
    minLiqMcRatioPct: 10.0,
    minTx24h: 180,
    minAvgTicketM5Usd: 28.0,
    minHolders: 350,
    defaultStopLossPct: 15,
    tp1GainPct: 35,
    tp2GainPct: 80,
  },
  {
    id: 'C_MID_EXPANSION',
    label: 'Corridor C · Expansion Runner ($210K–$390K)',
    minMc: 210_000,
    maxMc: 390_000,
    nextWallMc: 560_000,
    minLiqUsd: 26_000,
    minLiqMcRatioPct: 8.0,
    minTx24h: 350,
    minAvgTicketM5Usd: 28.0,
    minHolders: 600,
    defaultStopLossPct: 14,
    tp1GainPct: 30,
    tp2GainPct: 65,
  },
  {
    id: 'D_VIRAL_BASE',
    label: 'Corridor D · Viral Base Breakout ($390K–$850K)',
    minMc: 390_000,
    maxMc: 850_000,
    nextWallMc: 1_300_000,
    minLiqUsd: 40_000,
    minLiqMcRatioPct: 7.0,
    minTx24h: 800,
    minAvgTicketM5Usd: 28.0,
    minHolders: 1_800,
    defaultStopLossPct: 14,
    tp1GainPct: 35,
    tp2GainPct: 85,
  },
];

export const TEST_BUY_LAMPORTS = 100_000_000; // 0.1 SOL
export const DEPTH_STRESS_MULTIPLIER = 10; // 1.0 SOL sell stress test
export const PRIORITY_TIP_BUFFER_PCT = 1.2;
