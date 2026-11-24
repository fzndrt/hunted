import { useCallback, useEffect, useState } from 'react';
import {
  Activity,
  Layers,
  RefreshCw,
  Search,
  ShieldAlert,
} from 'lucide-react';
import { DEFAULT_SCAN_CONFIG, isValidSolanaMint, VERIFIED_HARD_QUOTE_MINTS } from './engine/constants';
import { runLiveCleanScan } from './engine/scanner';
import type { DexPairLike } from './engine/security';
import type { ExitSignalCode, ScanConfig, ScanSummary, TrackedPosition } from './engine/types';
import { ArchitectureView } from './components/ArchitectureView';
import { ExitGuardView } from './components/ExitGuardView';
import { InspectorView } from './components/InspectorView';
import { RadarView } from './components/RadarView';

type ActiveTab = 'radar' | 'inspector' | 'exits' | 'architecture';

const LOCAL_POSITIONS_KEY = 'corridor_desk_positions_v2';

const NAV_ITEMS: Array<{ id: ActiveTab; label: string; breadcrumb: string; icon: typeof Activity }> = [
  { id: 'architecture', label: 'Kode Repo (24 File)', breadcrumb: 'Explorer & Salin Kode (24 File · Vercel Ready)', icon: Layers },
  { id: 'radar', label: 'Pelacak Koin', breadcrumb: 'Pelacak Koin Live', icon: Activity },
  { id: 'inspector', label: 'Audit Mint', breadcrumb: 'Audit Mint', icon: Search },
  { id: 'exits', label: 'Exit Guard', breadcrumb: 'Exit Guard', icon: ShieldAlert },
];

function readLocalPositions(): TrackedPosition[] {
  try {
    const raw = localStorage.getItem(LOCAL_POSITIONS_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? (parsed as TrackedPosition[]) : [];
  } catch {
    return [];
  }
}

function writeLocalPositions(items: TrackedPosition[]): void {
  try {
    localStorage.setItem(LOCAL_POSITIONS_KEY, JSON.stringify(items));
  } catch {
    // ignore storage quota errors
  }
}

function evaluateClientExit(pos: TrackedPosition, livePair: DexPairLike): TrackedPosition {
  const liveMc = Number(livePair.marketCap || livePair.fdv || 0);
  const livePriceUsd = Number(livePair.priceUsd || 0);
  const liveLiqUsd = Number(livePair.liquidity?.usd || 0);
  const peakMc = Math.max(pos.peakMc, liveMc);
  const pnlPct = pos.entryMc > 0 ? Number((((liveMc - pos.entryMc) / pos.entryMc) * 100).toFixed(2)) : 0;
  const peakPnlPct = pos.entryMc > 0 ? ((peakMc - pos.entryMc) / pos.entryMc) * 100 : 0;
  const drawdownFromPeakPct =
    peakMc > 0 ? Number((((peakMc - liveMc) / peakMc) * 100).toFixed(2)) : 0;

  let realLpRetentionPct: number | null = null;
  if (pos.entryLiqUsd > 0 && pos.entryMc > 0 && liveMc > 0) {
    const expectedLp = pos.entryLiqUsd * Math.sqrt(liveMc / pos.entryMc);
    if (expectedLp > 0) {
      realLpRetentionPct = Number(((liveLiqUsd / expectedLp) * 100).toFixed(1));
    }
  }

  const tp1Hit = pos.tp1Hit || pnlPct >= pos.tp1Pct;
  const buysM5 = Number(livePair.txns?.m5?.buys ?? 0);
  const sellsM5 = Number(livePair.txns?.m5?.sells ?? 0);
  const pM5 = Number(livePair.priceChange?.m5 ?? 0);

  let signal: ExitSignalCode = 'HOLDING_SAFE';
  let signalHeadline = 'Holding Safe';
  let signalDetail = `Posisi aman (${pnlPct >= 0 ? '+' : ''}${pnlPct}%). Peak $${Math.round(peakMc).toLocaleString()}.`;

  if (realLpRetentionPct !== null && realLpRetentionPct < 80) {
    signal = 'LP_PULL_EMERGENCY';
    signalHeadline = 'Darurat Tarik LP';
    signalDetail = `LP riil menyusut ke ${realLpRetentionPct}% setelah normalisasi AMM √(MC/MC0) — JUAL SEGERA.`;
  } else {
    const stopFloor = pos.tp1Hit ? 0 : -Math.abs(pos.hardStopPct);
    if (pnlPct <= stopFloor) {
      signal = 'HARD_STOP_LOSS';
      signalHeadline = pos.tp1Hit ? 'Breakeven Stop' : 'Hard Stop-Loss';
      signalDetail = pos.tp1Hit
        ? `Harga kembali ke titik impas (${pnlPct}%) pasca-TP1 — tutup sisa posisi.`
        : `Hard Stop-Loss tersentuh (${pnlPct}% <= -${pos.hardStopPct}%) — wajib cut loss disiplin.`;
    } else if (peakPnlPct >= 30) {
      const trailLimit = peakPnlPct >= 150 ? 12 : peakPnlPct >= 80 ? 14 : 18;
      if (drawdownFromPeakPct >= trailLimit) {
        signal = 'TRAILING_PEAK_EXIT';
        signalHeadline = 'Trailing Stop Aktif';
        signalDetail = `Turun -${drawdownFromPeakPct}% dari puncak $${Math.round(peakMc).toLocaleString()} (batas -${trailLimit}%).`;
      }
    }

    if (signal === 'HOLDING_SAFE') {
      if (sellsM5 >= 8 && sellsM5 > buysM5 * 1.8 && pM5 <= -12) {
        signal = 'WHALE_DUMP_DIVERGENCE';
        signalHeadline = 'Whale Dump 5m';
        signalDetail = `Tekanan jual 5m berat (${sellsM5}S vs ${buysM5}B, ${pM5.toFixed(1)}%) — pertimbangkan kunci profit.`;
      } else if (pnlPct >= pos.tp2Pct) {
        signal = 'TP2_TAKE_PROFIT';
        signalHeadline = 'TP2 Tercapai';
        signalDetail = `Profit +${pnlPct}% (>= +${pos.tp2Pct}%) — jual 30% dan kawal sisa 20% dengan trailing stop.`;
      } else if (pnlPct >= pos.tp1Pct) {
        signal = 'TP1_DERISK_BREAKEVEN';
        signalHeadline = 'TP1 Tercapai';
        signalDetail = `Profit +${pnlPct}% (>= +${pos.tp1Pct}%) — jual 50% untuk amankan modal & geser SL ke Breakeven.`;
      }
    }
  }

  return {
    ...pos,
    peakMc,
    tp1Hit,
    lastCheckedTs: Date.now(),
    liveMc,
    livePriceUsd,
    liveLiqUsd,
    pnlPct,
    realLpRetentionPct,
    drawdownFromPeakPct,
    signal,
    signalHeadline,
    signalDetail,
  };
}

export default function App() {
  const [activeTab, setActiveTab] = useState<ActiveTab>('architecture');
  const [config, setConfig] = useState<ScanConfig>(DEFAULT_SCAN_CONFIG);
  const [scan, setScan] = useState<ScanSummary | null>(null);
  const [scanLoading, setScanLoading] = useState(false);
  const [scanError, setScanError] = useState<string | null>(null);

  const [positions, setPositions] = useState<TrackedPosition[]>(() => readLocalPositions());
  const [checkingExits, setCheckingExits] = useState(false);

  const runScan = useCallback(async () => {
    setScanLoading(true);
    setScanError(null);
    try {
      const summary = await runLiveCleanScan(config);
      setScan(summary);
    } catch (err) {
      setScanError(err instanceof Error ? err.message : 'Gagal menjalankan scan pasar Solana.');
    } finally {
      setScanLoading(false);
    }
  }, [config]);

  const refreshExits = useCallback(async () => {
    const current = readLocalPositions();
    if (current.length === 0) return;
    setCheckingExits(true);
    try {
      const uniqueMints = Array.from(new Set(current.map((p) => p.mint).filter(isValidSolanaMint)));
      const liveMap = new Map<string, DexPairLike>();
      for (let i = 0; i < uniqueMints.length; i += 25) {
        const chunk = uniqueMints.slice(i, i + 25);
        try {
          const resp = await fetch(
            `https://api.dexscreener.com/tokens/v1/solana/${chunk.join(',')}`,
            { headers: { Accept: 'application/json' } }
          );
          if (!resp.ok) continue;
          const pairs = (await resp.json()) as DexPairLike[];
          if (!Array.isArray(pairs)) continue;
          for (const pair of pairs) {
            if (String(pair?.chainId ?? '').toLowerCase() !== 'solana') continue;
            const m = String(pair?.baseToken?.address ?? '').trim();
            if (!m) continue;
            const prev = liveMap.get(m);
            const curHard = pair.quoteToken?.address && VERIFIED_HARD_QUOTE_MINTS[pair.quoteToken.address] ? 1 : 0;
            const prevHard = prev?.quoteToken?.address && VERIFIED_HARD_QUOTE_MINTS[prev.quoteToken.address] ? 1 : 0;
            if (
              !prev ||
              curHard > prevHard ||
              (curHard === prevHard && Number(pair.liquidity?.usd ?? 0) > Number(prev.liquidity?.usd ?? 0))
            ) {
              liveMap.set(m, pair);
            }
          }
        } catch {
          // continue next chunk
        }
      }

      const updated = current.map((pos) => {
        const pair = liveMap.get(pos.mint);
        return pair ? evaluateClientExit(pos, pair) : pos;
      });
      setPositions(updated);
      writeLocalPositions(updated);
    } finally {
      setCheckingExits(false);
    }
  }, []);

  const handleTrackPosition = useCallback(
    async (payload: {
      mint: string;
      pairAddress?: string;
      symbol: string;
      name: string;
      entryMc: number;
      entryPriceUsd: number;
      entryLiqUsd: number;
      positionUsd: number;
      hardStopPct: number;
      tp1Pct: number;
      tp2Pct: number;
    }) => {
      const cleanMint = payload.mint.trim();
      if (!isValidSolanaMint(cleanMint)) {
        throw new Error('Alamat mint Solana tidak valid (wajib Base58 32–44 karakter).');
      }
      if (!Number.isFinite(payload.entryMc) || payload.entryMc <= 0) {
        throw new Error('Entry Market Cap harus angka positif.');
      }
      const now = Date.now();
      const created: TrackedPosition = {
        id: `pos-${now.toString(36)}-${Math.random().toString(36).slice(2, 6)}`,
        mint: cleanMint,
        pairAddress: payload.pairAddress ?? '',
        symbol: payload.symbol.trim().toUpperCase() || 'TOKEN',
        name: payload.name.trim() || 'Solana Token',
        entryMc: payload.entryMc,
        entryPriceUsd: payload.entryPriceUsd,
        entryLiqUsd: payload.entryLiqUsd,
        positionUsd: payload.positionUsd,
        hardStopPct: payload.hardStopPct,
        tp1Pct: payload.tp1Pct,
        tp2Pct: payload.tp2Pct,
        peakMc: payload.entryMc,
        tp1Hit: false,
        entryTs: now,
        lastCheckedTs: now,
        liveMc: payload.entryMc,
        livePriceUsd: payload.entryPriceUsd,
        liveLiqUsd: payload.entryLiqUsd,
        pnlPct: 0,
        realLpRetentionPct: payload.entryLiqUsd > 0 ? 100 : null,
        drawdownFromPeakPct: 0,
        signal: 'HOLDING_SAFE',
        signalHeadline: 'Holding Safe',
        signalDetail: 'Posisi tercatat di Exit Guard. Klik Cek Harga & Exit Live untuk memperbarui.',
      };
      setPositions((prev) => {
        const next = [created, ...prev];
        writeLocalPositions(next);
        return next;
      });
    },
    []
  );

  const handleDeletePosition = useCallback(async (id: string) => {
    setPositions((prev) => {
      const next = prev.filter((p) => p.id !== id);
      writeLocalPositions(next);
      return next;
    });
  }, []);

  useEffect(() => {
    if (activeTab === 'radar' && !scan && !scanLoading) {
      runScan();
    }
  }, [activeTab, scan, scanLoading, runScan]);

  const urgentExits = positions.filter(
    (p) =>
      p.signal === 'HARD_STOP_LOSS' ||
      p.signal === 'LP_PULL_EMERGENCY' ||
      p.signal === 'TRAILING_PEAK_EXIT'
  ).length;

  return (
    <div className="min-h-screen bg-[#0B1120] text-slate-100 flex">
      {/* Desktop Left Sidebar (240px fixed) */}
      <aside className="hidden lg:flex lg:flex-col lg:w-60 lg:shrink-0 border-r border-slate-800 bg-[#0F172A]">
        <div className="h-14 px-5 flex items-center border-b border-slate-800">
          <span className="text-base font-bold tracking-tight text-white whitespace-nowrap shrink-0">
            Corridor Desk
          </span>
        </div>

        <nav className="flex-1 p-3 space-y-1">
          {NAV_ITEMS.map((item) => {
            const Icon = item.icon;
            const active = activeTab === item.id;
            return (
              <button
                key={item.id}
                type="button"
                onClick={() => setActiveTab(item.id)}
                className={`w-full flex items-center justify-between px-3 py-2.5 text-sm font-medium rounded-md transition-colors whitespace-nowrap cursor-pointer ${
                  active
                    ? 'bg-slate-800 text-white'
                    : 'text-slate-400 hover:text-slate-100 hover:bg-slate-800/50'
                }`}
              >
                <span className="inline-flex items-center gap-2.5">
                  <Icon className="w-4 h-4 shrink-0" />
                  <span>{item.label}</span>
                </span>
                {item.id === 'exits' && positions.length > 0 && (
                  <span
                    className={`font-mono text-xs tabular-nums ${
                      urgentExits > 0 ? 'text-rose-400 font-semibold' : 'text-slate-400'
                    }`}
                  >
                    {positions.length}
                  </span>
                )}
              </button>
            );
          })}
        </nav>

        <div className="p-4 border-t border-slate-800 text-xs text-slate-400 space-y-1.5">
          <div className="font-medium text-slate-300">Disiplin Modal Real Money</div>
          <p className="leading-relaxed text-slate-500">
            Risiko maksimal 1%–2% per trade. Patuhi Hard Stop-Loss dan kunci modal awal di TP1.
          </p>
        </div>
      </aside>

      {/* Main Content Area */}
      <div className="flex-1 min-w-0 flex flex-col">
        {/* Top Header with Always-Visible Quick Navigation for Narrow Preview Panes */}
        <header className="border-b border-slate-800 bg-[#0B1120] sticky top-0 z-20">
          <div className="h-14 px-4 sm:px-6 flex items-center justify-between gap-3">
            <div className="flex items-center gap-2 text-sm truncate">
              <span className="font-bold text-white lg:hidden whitespace-nowrap">Corridor Desk</span>
              <span className="text-slate-500 hidden lg:inline whitespace-nowrap">Corridor Desk</span>
              <span className="text-slate-600" aria-hidden="true">/</span>
              <h1 className="font-semibold text-slate-100 truncate">
                {NAV_ITEMS.find((n) => n.id === activeTab)?.breadcrumb}
              </h1>
            </div>

            <div className="flex items-center gap-2 shrink-0">
              <button
                type="button"
                onClick={() => setActiveTab(activeTab === 'architecture' ? 'radar' : 'architecture')}
                className="px-3 py-1.5 text-xs font-medium text-slate-200 bg-[#111827] hover:bg-slate-800 border border-slate-700 rounded-md transition-colors whitespace-nowrap shrink-0 cursor-pointer"
              >
                {activeTab === 'architecture' ? 'Pelacak Koin' : 'Salin Kode (24 File)'}
              </button>
              <button
                type="button"
                onClick={() => {
                  setActiveTab('radar');
                  runScan();
                }}
                disabled={scanLoading}
                className="inline-flex items-center gap-1.5 px-3.5 py-1.5 text-xs font-semibold text-slate-950 bg-emerald-400 hover:bg-emerald-300 disabled:opacity-50 rounded-md transition-colors whitespace-nowrap shrink-0 cursor-pointer"
              >
                <RefreshCw className={`w-3.5 h-3.5 ${scanLoading ? 'animate-spin' : ''}`} />
                <span>{scanLoading ? 'Memindai...' : 'Scan Koin'}</span>
              </button>
            </div>
          </div>

          {/* Compact Mobile / Split-Preview Navigation Bar */}
          <div className="lg:hidden px-4 pb-2.5 flex items-center gap-1.5 overflow-x-auto">
            {NAV_ITEMS.map((item) => {
              const Icon = item.icon;
              const active = activeTab === item.id;
              return (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => setActiveTab(item.id)}
                  className={`inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md transition-colors whitespace-nowrap shrink-0 cursor-pointer ${
                    active
                      ? 'bg-slate-800 text-white border border-slate-700'
                      : 'bg-[#111827] text-slate-400 hover:text-slate-100 border border-slate-800'
                  }`}
                >
                  <Icon className="w-3.5 h-3.5 shrink-0" />
                  <span>{item.label}</span>
                  {item.id === 'exits' && positions.length > 0 && (
                    <span className="font-mono text-[11px] text-emerald-400">{positions.length}</span>
                  )}
                </button>
              );
            })}
          </div>
        </header>

        <main className="flex-1 p-4 sm:p-6 max-w-[1440px] w-full mx-auto">
          {activeTab === 'radar' && (
            <RadarView
              scan={scan}
              loading={scanLoading}
              error={scanError}
              config={config}
              onChangeConfig={setConfig}
              onRunScan={runScan}
              onTrackPosition={handleTrackPosition}
            />
          )}

          {activeTab === 'inspector' && (
            <InspectorView config={config} onTrackPosition={handleTrackPosition} />
          )}

          {activeTab === 'exits' && (
            <ExitGuardView
              positions={positions}
              checking={checkingExits}
              onRefreshExits={refreshExits}
              onAddPosition={handleTrackPosition}
              onDeletePosition={handleDeletePosition}
            />
          )}

          {activeTab === 'architecture' && <ArchitectureView />}
        </main>
      </div>
    </div>
  );
}
