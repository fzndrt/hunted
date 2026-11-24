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
  minScore: 72,
  maxRoundTripLossPct: 5.5,
  maxDepthCliffLossPct: 13.5,
  minNetHeadroomPct: 20.0,
  maxM5ChangePct: 28.0,
  maxH1ChangePct: 180.0,
  minLiquidityUsd: 10_000,
  includeMidCapCorridor: true,
};

/**
 * Clean Early-Entry Corridors designed strictly for early asymmetric R:R
 * while blocking psychological take-profit ceilings (Red Zones).
 */
export const ENTRY_CORRIDORS: CorridorDefinition[] = [
  {
    id: 'A_PRE_100K',
    label: 'Corridor A · Pre-100K Ignition ($22K–$78K)',
    minMc: 22_000,
    maxMc: 78_000,
    nextWallMc: 100_000,
    minLiqUsd: 10_000,
    minLiqMcRatioPct: 14.0,
    minTx24h: 120,
    minAvgTicketM5Usd: 18.0,
    minHolders: 75,
    defaultStopLossPct: 18,
    tp1GainPct: 45,
    tp2GainPct: 110,
  },
  {
    id: 'B_POST_100K',
    label: 'Corridor B · Post-100K Breakout ($112K–$172K)',
    minMc: 112_000,
    maxMc: 172_000,
    nextWallMc: 205_000,
    minLiqUsd: 18_000,
    minLiqMcRatioPct: 11.0,
    minTx24h: 280,
    minAvgTicketM5Usd: 24.0,
    minHolders: 160,
    defaultStopLossPct: 15,
    tp1GainPct: 40,
    tp2GainPct: 90,
  },
  {
    id: 'C_MID_EXPANSION',
    label: 'Corridor C · Mid-Cap Expansion ($220K–$335K)',
    minMc: 220_000,
    maxMc: 335_000,
    nextWallMc: 460_000,
    minLiqUsd: 28_000,
    minLiqMcRatioPct: 8.5,
    minTx24h: 500,
    minAvgTicketM5Usd: 30.0,
    minHolders: 300,
    defaultStopLossPct: 14,
    tp1GainPct: 35,
    tp2GainPct: 75,
  },
];

export const TEST_BUY_LAMPORTS = 100_000_000; // 0.1 SOL
export const DEPTH_STRESS_MULTIPLIER = 10; // 1.0 SOL sell stress test
export const PRIORITY_TIP_BUFFER_PCT = 1.2;
