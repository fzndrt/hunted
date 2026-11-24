export function formatCompactUsd(val: number | null | undefined): string {
  if (val === null || val === undefined || !Number.isFinite(val)) return '—';
  const abs = Math.abs(val);
  const sign = val < 0 ? '-' : '';
  if (abs >= 1_000_000_000) return `${sign}$${(abs / 1_000_000_000).toFixed(2)}B`;
  if (abs >= 1_000_000) return `${sign}$${(abs / 1_000_000).toFixed(2)}M`;
  if (abs >= 1_000) return `${sign}$${(abs / 1_000).toFixed(1)}K`;
  return `${sign}$${abs.toFixed(0)}`;
}

export function formatPct(val: number | null | undefined, showPlus = true): string {
  if (val === null || val === undefined || !Number.isFinite(val)) return '—';
  const prefix = showPlus && val > 0 ? '+' : '';
  return `${prefix}${val.toFixed(1)}%`;
}

export function formatPriceUsd(val: number | null | undefined): string {
  if (!val || !Number.isFinite(val)) return '—';
  if (val >= 1) return `$${val.toFixed(3)}`;
  if (val >= 0.01) return `$${val.toFixed(4)}`;
  if (val >= 0.0001) return `$${val.toFixed(6)}`;
  return `$${val.toExponential(2)}`;
}

export function shortMint(mint: string): string {
  if (!mint || mint.length <= 12) return mint;
  return `${mint.slice(0, 5)}...${mint.slice(-4)}`;
}

export function formatAge(minutes: number): string {
  if (!Number.isFinite(minutes) || minutes < 1) return '<1m';
  if (minutes < 60) return `${Math.round(minutes)}m`;
  if (minutes < 1440) return `${(minutes / 60).toFixed(1)}h`;
  return `${(minutes / 1440).toFixed(1)}d`;
}
