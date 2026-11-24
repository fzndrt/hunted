import { useMemo, useState } from 'react';
import { RefreshCw, Search, SlidersHorizontal } from 'lucide-react';
import type { AuditedToken, ScanConfig, ScanSummary, Verdict } from '../engine/types';
import { formatCompactUsd, formatPct, shortMint } from '../utils/format';
import { TokenForensicPanel } from './TokenForensicPanel';

interface RadarViewProps {
  scan: ScanSummary | null;
  loading: boolean;
  error: string | null;
  config: ScanConfig;
  onChangeConfig: (next: ScanConfig) => void;
  onRunScan: () => void;
  onTrackPosition: Parameters<typeof TokenForensicPanel>[0]['onTrackPosition'];
}

type FilterTab = 'ALL' | Verdict;

export function RadarView({
  scan,
  loading,
  error,
  config,
  onChangeConfig,
  onRunScan,
  onTrackPosition,
}: RadarViewProps) {
  const [filterTab, setFilterTab] = useState<FilterTab>('ALL');
  const [query, setQuery] = useState('');
  const [selectedMint, setSelectedMint] = useState<string | null>(null);
  const [showParams, setShowParams] = useState(false);

  const filteredTokens = useMemo(() => {
    if (!scan) return [];
    const q = query.trim().toLowerCase();
    return scan.tokens.filter((t) => {
      if (filterTab !== 'ALL' && t.verdict !== filterTab) return false;
      if (!q) return true;
      return (
        t.symbol.toLowerCase().includes(q) ||
        t.name.toLowerCase().includes(q) ||
        t.mint.toLowerCase().includes(q)
      );
    });
  }, [scan, filterTab, query]);

  const selectedToken: AuditedToken | null = useMemo(() => {
    if (!filteredTokens.length) return null;
    if (selectedMint) {
      const match = filteredTokens.find((t) => t.mint === selectedMint);
      if (match) return match;
    }
    return filteredTokens[0];
  }, [filteredTokens, selectedMint]);

  return (
    <div className="space-y-6">
      {/* KPI Summary Strip (Flat single-elevation with hairline dividers) */}
      <div className="bg-[#111827] border border-slate-800 rounded-lg grid grid-cols-2 lg:grid-cols-4 divide-y lg:divide-y-0 lg:divide-x divide-slate-800">
        <div className="p-4">
          <div className="text-xs text-slate-400">Entry Ready (Lolos 4 Layer)</div>
          <div className="mt-1 font-mono text-2xl font-semibold text-emerald-400 tabular-nums">
            {scan ? scan.entryReadyCount : '—'}
          </div>
          <div className="mt-0.5 text-xs text-slate-500">
            Siap masuk sesuai batas MC pasca-delay
          </div>
        </div>
        <div className="p-4">
          <div className="text-xs text-slate-400">Watch Pullback</div>
          <div className="mt-1 font-mono text-2xl font-semibold text-amber-400 tabular-nums">
            {scan ? scan.watchPullbackCount : '—'}
          </div>
          <div className="mt-0.5 text-xs text-slate-500">
            Aman on-chain, tunggu koreksi / tembus tembok
          </div>
        </div>
        <div className="p-4">
          <div className="text-xs text-slate-400">Ditolak (Anti-Scam & Pucuk)</div>
          <div className="mt-1 font-mono text-2xl font-semibold text-rose-400 tabular-nums">
            {scan ? scan.rejectedCount : '—'}
          </div>
          <div className="mt-0.5 text-xs text-slate-500">
            Veto keras: Honeypot, Nano-Swarm, Siphon, Late-Top
          </div>
        </div>
        <div className="p-4">
          <div className="text-xs text-slate-400">Cakupan Scan Live</div>
          <div className="mt-1 font-mono text-2xl font-semibold text-slate-100 tabular-nums">
            {scan ? `${scan.stage2AuditedCount}/${scan.discoveredCount}` : '—'}
          </div>
          <div className="mt-0.5 font-mono text-xs text-slate-500 tabular-nums">
            {scan ? `${(scan.durationMs / 1000).toFixed(1)}s · Delay ${config.reactionDelaySec}s` : 'Belum dipindai'}
          </div>
        </div>
      </div>

      {/* Controls Bar: Filter Tabs + Search + Parameter Drawer Toggle */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="inline-flex items-center gap-1 p-1 bg-[#111827] border border-slate-800 rounded-lg">
          {(
            [
              ['ALL', `Semua (${scan?.tokens.length ?? 0})`],
              ['ENTRY_READY', `Entry Ready (${scan?.entryReadyCount ?? 0})`],
              ['WATCH_PULLBACK', `Watch Pullback (${scan?.watchPullbackCount ?? 0})`],
              ['REJECTED', `Rejected (${scan?.rejectedCount ?? 0})`],
            ] as [FilterTab, string][]
          ).map(([tab, label]) => (
            <button
              key={tab}
              type="button"
              onClick={() => setFilterTab(tab)}
              className={`px-3 py-1.5 text-xs font-medium rounded-md transition-colors whitespace-nowrap shrink-0 cursor-pointer ${
                filterTab === tab
                  ? 'bg-slate-800 text-white'
                  : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              {label}
            </button>
          ))}
        </div>

        <div className="flex items-center gap-2 flex-1 sm:flex-initial justify-end">
          <div className="relative flex-1 sm:w-60">
            <Search className="w-3.5 h-3.5 text-slate-500 absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none" />
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Cari simbol atau mint..."
              className="w-full pl-8 pr-3 py-1.5 bg-[#111827] border border-slate-800 rounded-md text-xs text-slate-100 placeholder:text-slate-500 focus:outline-none focus:border-slate-600"
            />
          </div>
          <button
            type="button"
            onClick={() => setShowParams((v) => !v)}
            className={`inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md border transition-colors whitespace-nowrap shrink-0 cursor-pointer ${
              showParams
                ? 'bg-slate-800 border-slate-700 text-white'
                : 'bg-[#111827] border-slate-800 text-slate-300 hover:border-slate-700'
            }`}
          >
            <SlidersHorizontal className="w-3.5 h-3.5" />
            <span>Parameter</span>
          </button>
        </div>
      </div>

      {/* Collapsible Parameter Bar */}
      {showParams && (
        <div className="bg-[#111827] border border-slate-800 rounded-lg p-4 space-y-3">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold text-slate-200">
              Kalibrasi Filter Anti-Pucuk & Eksekusi
            </span>
            <button
              type="button"
              onClick={onRunScan}
              disabled={loading}
              className="text-xs font-medium text-emerald-400 hover:text-emerald-300 cursor-pointer"
            >
              Terapkan & Scan Ulang
            </button>
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-5 gap-3 text-xs">
            <label className="space-y-1">
              <span className="text-slate-400">Delay Eksekusi (detik)</span>
              <input
                type="number"
                min={5}
                max={60}
                value={config.reactionDelaySec}
                onChange={(e) =>
                  onChangeConfig({ ...config, reactionDelaySec: Number(e.target.value) || 22 })
                }
                className="w-full px-2.5 py-1.5 bg-[#0B1120] border border-slate-700 rounded font-mono text-slate-100"
              />
            </label>
            <label className="space-y-1">
              <span className="text-slate-400">Skor Minimal (0–100)</span>
              <input
                type="number"
                min={50}
                max={95}
                value={config.minScore}
                onChange={(e) =>
                  onChangeConfig({ ...config, minScore: Number(e.target.value) || 72 })
                }
                className="w-full px-2.5 py-1.5 bg-[#0B1120] border border-slate-700 rounded font-mono text-slate-100"
              />
            </label>
            <label className="space-y-1">
              <span className="text-slate-400">Batas Candle 5m (%)</span>
              <input
                type="number"
                min={10}
                max={50}
                value={config.maxM5ChangePct}
                onChange={(e) =>
                  onChangeConfig({ ...config, maxM5ChangePct: Number(e.target.value) || 28 })
                }
                className="w-full px-2.5 py-1.5 bg-[#0B1120] border border-slate-700 rounded font-mono text-slate-100"
              />
            </label>
            <label className="space-y-1">
              <span className="text-slate-400">Min Headroom (%)</span>
              <input
                type="number"
                min={10}
                max={50}
                value={config.minNetHeadroomPct}
                onChange={(e) =>
                  onChangeConfig({ ...config, minNetHeadroomPct: Number(e.target.value) || 20 })
                }
                className="w-full px-2.5 py-1.5 bg-[#0B1120] border border-slate-700 rounded font-mono text-slate-100"
              />
            </label>
            <label className="flex items-center gap-2 pt-4 cursor-pointer">
              <input
                type="checkbox"
                checked={config.includeMidCapCorridor}
                onChange={(e) =>
                  onChangeConfig({ ...config, includeMidCapCorridor: e.target.checked })
                }
                className="rounded border-slate-700 bg-[#0B1120] accent-emerald-500"
              />
              <span className="text-slate-300">Sertakan Koridor C ($220K–$335K)</span>
            </label>
          </div>
        </div>
      )}

      {/* Error State */}
      {error && (
        <div className="bg-[#111827] border border-rose-900/70 rounded-lg p-4 flex items-center justify-between gap-4">
          <div className="text-sm text-rose-300">{error}</div>
          <button
            type="button"
            onClick={onRunScan}
            className="px-3 py-1.5 text-xs font-medium bg-slate-800 hover:bg-slate-700 text-slate-100 rounded-md whitespace-nowrap cursor-pointer"
          >
            Coba Lagi
          </button>
        </div>
      )}

      {/* Main Split Grid: High-Density Table (Left) + 5-Layer Forensic Inspector (Right) */}
      <div className="grid grid-cols-1 xl:grid-cols-12 gap-6 items-start">
        <div className="xl:col-span-7 bg-[#111827] border border-slate-800 rounded-lg overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse">
              <thead>
                <tr className="border-b border-slate-800 text-[11px] font-medium text-slate-400">
                  <th className="py-3 px-4">Token</th>
                  <th className="py-3 px-3 text-right">Spot / Proj MC</th>
                  <th className="py-3 px-3 text-right">5m / 1h</th>
                  <th className="py-3 px-3 text-right">LP</th>
                  <th className="py-3 px-3 text-right">0.1S / 1S</th>
                  <th className="py-3 px-3 text-right">Skor</th>
                  <th className="py-3 px-4 text-right">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/70 text-xs">
                {loading && !scan &&
                  Array.from({ length: 6 }).map((_, idx) => (
                    <tr key={idx} className="h-12">
                      <td colSpan={7} className="px-4 py-3">
                        <div className="h-4 bg-slate-800/70 rounded w-full" />
                      </td>
                    </tr>
                  ))}

                {!loading && filteredTokens.length === 0 && (
                  <tr>
                    <td colSpan={7} className="px-6 py-12 text-center">
                      <p className="text-sm text-slate-300">
                        {scan
                          ? 'Tidak ada token yang cocok dengan filter ini pada siklus scan terakhir.'
                          : 'Belum ada data scan live. Klik Scan Market untuk mengaudit kolam Solana saat ini.'}
                      </p>
                      <button
                        type="button"
                        onClick={onRunScan}
                        disabled={loading}
                        className="mt-4 inline-flex items-center gap-1.5 px-4 py-2 text-xs font-medium text-slate-950 bg-emerald-400 hover:bg-emerald-300 rounded-md transition-colors cursor-pointer"
                      >
                        <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
                        <span>Scan Market Live</span>
                      </button>
                    </td>
                  </tr>
                )}

                {filteredTokens.map((t) => {
                  const isSelected = selectedToken?.mint === t.mint;
                  const statusColor =
                    t.verdict === 'ENTRY_READY'
                      ? 'text-emerald-400'
                      : t.verdict === 'WATCH_PULLBACK'
                      ? 'text-amber-400'
                      : 'text-rose-400';
                  const statusText =
                    t.verdict === 'ENTRY_READY'
                      ? 'Entry Ready'
                      : t.verdict === 'WATCH_PULLBACK'
                      ? 'Watch Dip'
                      : 'Rejected';

                  return (
                    <tr
                      key={t.mint}
                      onClick={() => setSelectedMint(t.mint)}
                      className={`cursor-pointer transition-colors ${
                        isSelected ? 'bg-slate-800/80' : 'hover:bg-slate-800/40'
                      }`}
                    >
                      <td className="py-3 px-4">
                        <div className="flex items-center gap-1.5 font-semibold text-slate-100 truncate max-w-[170px]">
                          <span className="truncate">${t.symbol}</span>
                          {t.antiTop.metrics.isSleeperRevival && (
                            <span className="font-mono text-[10px] font-semibold text-cyan-300 shrink-0">
                              REVIVAL
                            </span>
                          )}
                        </div>
                        <div className="font-mono text-[11px] text-slate-500">
                          {shortMint(t.mint)} · {t.security.metrics.hardQuoteSymbol}
                        </div>
                      </td>
                      <td className="py-3 px-3 text-right font-mono tabular-nums">
                        <div className="text-slate-100">
                          {formatCompactUsd(t.antiTop.metrics.spotMc)}
                        </div>
                        <div className="text-[11px] text-slate-500">
                          → {formatCompactUsd(t.antiTop.metrics.projectedEntryMc)}
                        </div>
                      </td>
                      <td className="py-3 px-3 text-right font-mono tabular-nums">
                        <div
                          className={
                            t.antiTop.metrics.pM5 >= 0 ? 'text-emerald-400' : 'text-rose-400'
                          }
                        >
                          {formatPct(t.antiTop.metrics.pM5)}
                        </div>
                        <div className="text-[11px] text-slate-400">
                          {formatPct(t.antiTop.metrics.pH1)}
                        </div>
                      </td>
                      <td className="py-3 px-3 text-right font-mono tabular-nums">
                        <div className="text-slate-200">
                          {formatCompactUsd(t.antiTop.metrics.liqUsd)}
                        </div>
                        <div className="text-[11px] text-slate-500">
                          {t.antiTop.metrics.liqMcRatioPct.toFixed(0)}% MC
                        </div>
                      </td>
                      <td className="py-3 px-3 text-right font-mono tabular-nums">
                        <div className="text-slate-200">
                          {t.execution.metrics.roundTripLoss01SolPct !== null
                            ? `-${t.execution.metrics.roundTripLoss01SolPct}%`
                            : 'Fail'}
                        </div>
                        <div className="text-[11px] text-slate-500">
                          {t.execution.metrics.depthLoss1SolPct !== null
                            ? `-${t.execution.metrics.depthLoss1SolPct}%`
                            : 'Fail'}
                        </div>
                      </td>
                      <td className="py-3 px-3 text-right font-mono font-semibold tabular-nums text-slate-100">
                        {t.totalScore}
                      </td>
                      <td className={`py-3 px-4 text-right font-medium whitespace-nowrap ${statusColor}`}>
                        {statusText}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {scan && scan.feedNotes.length > 0 && (
            <div className="px-4 py-2.5 border-t border-slate-800/80 text-[11px] text-slate-500 flex flex-wrap gap-x-3 gap-y-1">
              {scan.feedNotes.map((note) => (
                <span key={note}>{note}</span>
              ))}
            </div>
          )}
        </div>

        {/* Right Side: Live Forensic Breakdown */}
        <div className="xl:col-span-5">
          {selectedToken ? (
            <TokenForensicPanel token={selectedToken} onTrackPosition={onTrackPosition} />
          ) : (
            <div className="bg-[#111827] border border-slate-800 rounded-lg p-8 text-center">
              <p className="text-sm text-slate-400">
                Pilih satu token dari daftar untuk membedah keamanan kontrak, kurva Pareto, kedalaman jual Jupiter 1.0 SOL, dan rencana Stop-Loss / TP.
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
