import { useState, type FormEvent } from 'react';
import { Plus, RefreshCw, Trash2, ExternalLink } from 'lucide-react';
import type { ExitSignalCode, TrackedPosition } from '../engine/types';
import { formatCompactUsd, formatPct, shortMint } from '../utils/format';

interface ExitGuardViewProps {
  positions: TrackedPosition[];
  checking: boolean;
  onRefreshExits: () => Promise<void>;
  onAddPosition: (payload: {
    mint: string;
    symbol: string;
    name: string;
    entryMc: number;
    entryPriceUsd: number;
    entryLiqUsd: number;
    positionUsd: number;
    hardStopPct: number;
    tp1Pct: number;
    tp2Pct: number;
  }) => Promise<void>;
  onDeletePosition: (id: string) => Promise<void>;
}

function getSignalMeta(signal: ExitSignalCode): { label: string; color: string } {
  switch (signal) {
    case 'HARD_STOP_LOSS':
      return { label: 'CUT LOSS (SL)', color: 'text-rose-400' };
    case 'LP_PULL_EMERGENCY':
      return { label: 'DARURAT LP PULL', color: 'text-rose-400' };
    case 'WHALE_DUMP_DIVERGENCE':
      return { label: 'WHALE DUMP', color: 'text-amber-400' };
    case 'TRAILING_PEAK_EXIT':
      return { label: 'TRAILING EXIT', color: 'text-amber-400' };
    case 'TP1_DERISK_BREAKEVEN':
      return { label: 'TP1 · JUAL 50%', color: 'text-emerald-400' };
    case 'TP2_TAKE_PROFIT':
      return { label: 'TP2 · JUAL 30%', color: 'text-emerald-300' };
    default:
      return { label: 'HOLDING SAFE', color: 'text-slate-300' };
  }
}

export function ExitGuardView({
  positions,
  checking,
  onRefreshExits,
  onAddPosition,
  onDeletePosition,
}: ExitGuardViewProps) {
  const [showForm, setShowForm] = useState(false);
  const [mint, setMint] = useState('');
  const [symbol, setSymbol] = useState('');
  const [entryMc, setEntryMc] = useState('50000');
  const [entryLiqUsd, setEntryLiqUsd] = useState('16000');
  const [positionUsd, setPositionUsd] = useState('150');
  const [hardStopPct, setHardStopPct] = useState('15');
  const [tp1Pct, setTp1Pct] = useState('45');
  const [tp2Pct, setTp2Pct] = useState('100');
  const [formError, setFormError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setFormError(null);
    setSaving(true);
    try {
      await onAddPosition({
        mint: mint.trim(),
        symbol: symbol.trim().toUpperCase() || 'TOKEN',
        name: symbol.trim().toUpperCase() || 'Tracked Token',
        entryMc: Number(entryMc),
        entryPriceUsd: 0,
        entryLiqUsd: Number(entryLiqUsd) || 0,
        positionUsd: Number(positionUsd) || 100,
        hardStopPct: Number(hardStopPct) || 15,
        tp1Pct: Number(tp1Pct) || 45,
        tp2Pct: Number(tp2Pct) || 100,
      });
      setMint('');
      setSymbol('');
      setShowForm(false);
    } catch (err) {
      setFormError(err instanceof Error ? err.message : 'Gagal menambahkan posisi.');
    } finally {
      setSaving(false);
    }
  };

  const urgentCount = positions.filter(
    (p) =>
      p.signal === 'HARD_STOP_LOSS' ||
      p.signal === 'LP_PULL_EMERGENCY' ||
      p.signal === 'TRAILING_PEAK_EXIT' ||
      p.signal === 'WHALE_DUMP_DIVERGENCE'
  ).length;

  const tpHitCount = positions.filter(
    (p) => p.signal === 'TP1_DERISK_BREAKEVEN' || p.signal === 'TP2_TAKE_PROFIT'
  ).length;

  const totalDeployedUsd = positions.reduce((s, p) => s + (p.positionUsd || 0), 0);

  return (
    <div className="space-y-6">
      {/* Exit Guard Summary Strip */}
      <div className="bg-[#111827] border border-slate-800 rounded-lg grid grid-cols-2 lg:grid-cols-4 divide-y lg:divide-y-0 lg:divide-x divide-slate-800">
        <div className="p-4">
          <div className="text-xs text-slate-400">Posisi Aktif Tersimpan</div>
          <div className="mt-1 font-mono text-2xl font-semibold text-slate-100 tabular-nums">
            {positions.length}
          </div>
          <div className="mt-0.5 text-xs text-slate-500">Persisten disk + mutex (bukan /tmp)</div>
        </div>
        <div className="p-4">
          <div className="text-xs text-slate-400">Total Modal Aktif</div>
          <div className="mt-1 font-mono text-2xl font-semibold text-slate-100 tabular-nums">
            ${totalDeployedUsd.toLocaleString()}
          </div>
          <div className="mt-0.5 text-xs text-slate-500">Terlindungi Hard Stop-Loss</div>
        </div>
        <div className="p-4">
          <div className="text-xs text-slate-400">Sinyal Keluar / Cut Loss</div>
          <div className="mt-1 font-mono text-2xl font-semibold text-rose-400 tabular-nums">
            {urgentCount}
          </div>
          <div className="mt-0.5 text-xs text-slate-500">SL, LP Pull AMM, atau Trailing Stop</div>
        </div>
        <div className="p-4">
          <div className="text-xs text-slate-400">Target Profit Tercapai</div>
          <div className="mt-1 font-mono text-2xl font-semibold text-emerald-400 tabular-nums">
            {tpHitCount}
          </div>
          <div className="mt-0.5 text-xs text-slate-500">TP1 (Jual 50% + BE) / TP2</div>
        </div>
      </div>

      {/* Action Bar */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-slate-100">
            Monitor Exit & AMM LP Retention (x · y = k Normalized)
          </h2>
          <p className="text-xs text-slate-400 mt-0.5">
            Retensi LP dihitung dengan <span className="font-mono text-slate-300">LP_live / (LP_entry × √(MC_live/MC_entry))</span> agar penarikan LP riil terpisah dari fluktuasi harga normal.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setShowForm((v) => !v)}
            className="inline-flex items-center gap-1.5 px-3.5 py-2 text-xs font-medium bg-[#111827] hover:bg-slate-800 border border-slate-700 text-slate-100 rounded-md transition-colors whitespace-nowrap cursor-pointer"
          >
            <Plus className="w-3.5 h-3.5" />
            <span>Tambah Posisi Manual</span>
          </button>
          <button
            type="button"
            onClick={onRefreshExits}
            disabled={checking || positions.length === 0}
            className="inline-flex items-center gap-1.5 px-3.5 py-2 text-xs font-semibold text-slate-950 bg-emerald-400 hover:bg-emerald-300 disabled:opacity-50 rounded-md transition-colors whitespace-nowrap cursor-pointer"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${checking ? 'animate-spin' : ''}`} />
            <span>{checking ? 'Memeriksa...' : 'Cek Harga & Exit Live'}</span>
          </button>
        </div>
      </div>

      {/* Add Manual Position Form */}
      {showForm && (
        <form
          onSubmit={handleSubmit}
          className="bg-[#111827] border border-slate-800 rounded-lg p-5 space-y-4"
        >
          <div className="text-sm font-semibold text-slate-100">Catat Posisi Entry Baru</div>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3 text-xs">
            <label className="space-y-1 sm:col-span-2">
              <span className="text-slate-400">Solana Token Mint (Base58)</span>
              <input
                type="text"
                required
                value={mint}
                onChange={(e) => setMint(e.target.value)}
                placeholder="Alamat mint kontrak Solana..."
                className="w-full px-3 py-2 bg-[#0B1120] border border-slate-700 rounded font-mono text-slate-100"
              />
            </label>
            <label className="space-y-1">
              <span className="text-slate-400">Simbol Token</span>
              <input
                type="text"
                required
                value={symbol}
                onChange={(e) => setSymbol(e.target.value)}
                placeholder="contoh: BONK"
                className="w-full px-3 py-2 bg-[#0B1120] border border-slate-700 rounded font-mono text-slate-100"
              />
            </label>
            <label className="space-y-1">
              <span className="text-slate-400">Modal Entry ($)</span>
              <input
                type="number"
                required
                min={1}
                value={positionUsd}
                onChange={(e) => setPositionUsd(e.target.value)}
                className="w-full px-3 py-2 bg-[#0B1120] border border-slate-700 rounded font-mono text-slate-100"
              />
            </label>
            <label className="space-y-1">
              <span className="text-slate-400">Entry Market Cap ($)</span>
              <input
                type="number"
                required
                min={1000}
                value={entryMc}
                onChange={(e) => setEntryMc(e.target.value)}
                className="w-full px-3 py-2 bg-[#0B1120] border border-slate-700 rounded font-mono text-slate-100"
              />
            </label>
            <label className="space-y-1">
              <span className="text-slate-400">Entry LP Pool ($)</span>
              <input
                type="number"
                min={0}
                value={entryLiqUsd}
                onChange={(e) => setEntryLiqUsd(e.target.value)}
                className="w-full px-3 py-2 bg-[#0B1120] border border-slate-700 rounded font-mono text-slate-100"
              />
            </label>
            <label className="space-y-1">
              <span className="text-slate-400">Hard Stop-Loss (%)</span>
              <input
                type="number"
                min={5}
                max={40}
                value={hardStopPct}
                onChange={(e) => setHardStopPct(e.target.value)}
                className="w-full px-3 py-2 bg-[#0B1120] border border-slate-700 rounded font-mono text-slate-100"
              />
            </label>
            <div className="grid grid-cols-2 gap-2">
              <label className="space-y-1">
                <span className="text-slate-400">TP1 (%)</span>
                <input
                  type="number"
                  min={15}
                  value={tp1Pct}
                  onChange={(e) => setTp1Pct(e.target.value)}
                  className="w-full px-2.5 py-2 bg-[#0B1120] border border-slate-700 rounded font-mono text-slate-100"
                />
              </label>
              <label className="space-y-1">
                <span className="text-slate-400">TP2 (%)</span>
                <input
                  type="number"
                  min={30}
                  value={tp2Pct}
                  onChange={(e) => setTp2Pct(e.target.value)}
                  className="w-full px-2.5 py-2 bg-[#0B1120] border border-slate-700 rounded font-mono text-slate-100"
                />
              </label>
            </div>
          </div>

          {formError && <div className="text-xs text-rose-400">{formError}</div>}

          <div className="flex items-center justify-end gap-2">
            <button
              type="button"
              onClick={() => setShowForm(false)}
              className="px-3.5 py-2 text-xs text-slate-300 hover:text-white cursor-pointer"
            >
              Batal
            </button>
            <button
              type="submit"
              disabled={saving}
              className="px-4 py-2 text-xs font-semibold text-slate-950 bg-emerald-400 hover:bg-emerald-300 disabled:opacity-50 rounded-md cursor-pointer"
            >
              {saving ? 'Menyimpan...' : 'Simpan Posisi'}
            </button>
          </div>
        </form>
      )}

      {/* Active Positions Table */}
      <div className="bg-[#111827] border border-slate-800 rounded-lg overflow-hidden">
        {positions.length === 0 ? (
          <div className="p-10 text-center space-y-2">
            <p className="text-sm font-medium text-slate-200">
              Belum ada posisi aktif yang dipantau.
            </p>
            <p className="text-xs text-slate-400 max-w-md mx-auto">
              Klik "Lacak di Exit Guard" dari hasil Live Radar / Token Inspector atau tambahkan posisi manual agar Hard Stop-Loss dan AMM LP Retention mengawal modal Anda.
            </p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse">
              <thead>
                <tr className="border-b border-slate-800 text-[11px] font-medium text-slate-400">
                  <th className="py-3 px-4">Token</th>
                  <th className="py-3 px-3 text-right">Entry → Live MC</th>
                  <th className="py-3 px-3 text-right">PnL / Peak</th>
                  <th className="py-3 px-3 text-right">LP Retention</th>
                  <th className="py-3 px-3 text-right">SL / TP1 / TP2</th>
                  <th className="py-3 px-4">Status Exit Guard</th>
                  <th className="py-3 px-4 text-right">Aksi</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/70 text-xs">
                {positions.map((pos) => {
                  const meta = getSignalMeta(pos.signal);
                  const pnlColor =
                    (pos.pnlPct ?? 0) >= 0 ? 'text-emerald-400' : 'text-rose-400';
                  const lpColor =
                    pos.realLpRetentionPct !== null && pos.realLpRetentionPct < 85
                      ? 'text-rose-400'
                      : 'text-slate-200';
                  return (
                    <tr key={pos.id} className="hover:bg-slate-800/30">
                      <td className="py-3.5 px-4">
                        <div className="font-semibold text-slate-100">${pos.symbol}</div>
                        <div className="font-mono text-[11px] text-slate-500">
                          {shortMint(pos.mint)} · ${pos.positionUsd.toLocaleString()}
                        </div>
                      </td>
                      <td className="py-3.5 px-3 text-right font-mono tabular-nums">
                        <div className="text-slate-400">{formatCompactUsd(pos.entryMc)}</div>
                        <div className="text-slate-100 font-semibold">
                          → {formatCompactUsd(pos.liveMc)}
                        </div>
                      </td>
                      <td className="py-3.5 px-3 text-right font-mono tabular-nums">
                        <div className={`font-semibold ${pnlColor}`}>
                          {formatPct(pos.pnlPct)}
                        </div>
                        <div className="text-[11px] text-slate-500">
                          Peak {formatCompactUsd(pos.peakMc)}
                        </div>
                      </td>
                      <td className={`py-3.5 px-3 text-right font-mono tabular-nums ${lpColor}`}>
                        <div>
                          {pos.realLpRetentionPct !== null ? `${pos.realLpRetentionPct}%` : '—'}
                        </div>
                        <div className="text-[11px] text-slate-500">
                          LP {formatCompactUsd(pos.liveLiqUsd)}
                        </div>
                      </td>
                      <td className="py-3.5 px-3 text-right font-mono text-[11px] text-slate-300 tabular-nums">
                        <div className="text-rose-400">-{pos.hardStopPct}%</div>
                        <div className="text-emerald-400">
                          +{pos.tp1Pct}% / +{pos.tp2Pct}%
                        </div>
                      </td>
                      <td className="py-3.5 px-4 max-w-xs">
                        <div className={`font-semibold ${meta.color}`}>{meta.label}</div>
                        <div className="text-[11px] text-slate-400 mt-0.5 leading-snug">
                          {pos.signalDetail}
                        </div>
                      </td>
                      <td className="py-3.5 px-4 text-right whitespace-nowrap">
                        <div className="inline-flex items-center gap-2">
                          <a
                            href={`https://jup.ag/swap/${pos.mint}-So11111111111111111111111111111111111111112`}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="inline-flex items-center gap-1 text-emerald-400 hover:text-emerald-300 font-medium"
                            title="Jual di Jupiter"
                          >
                            <span>Jual</span>
                            <ExternalLink className="w-3 h-3" />
                          </a>
                          <button
                            type="button"
                            onClick={() => onDeletePosition(pos.id)}
                            className="p-1.5 text-slate-500 hover:text-rose-400 transition-colors cursor-pointer"
                            title="Tutup / Hapus posisi"
                          >
                            <Trash2 className="w-3.5 h-3.5" />
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
