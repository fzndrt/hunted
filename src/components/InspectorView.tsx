import { useState, type FormEvent } from 'react';
import { Search, ShieldCheck } from 'lucide-react';
import { inspectSingleMint } from '../engine/scanner';
import type { AuditedToken, ScanConfig } from '../engine/types';
import { TokenForensicPanel } from './TokenForensicPanel';

interface InspectorViewProps {
  config: ScanConfig;
  onTrackPosition: Parameters<typeof TokenForensicPanel>[0]['onTrackPosition'];
}

export function InspectorView({ config, onTrackPosition }: InspectorViewProps) {
  const [mintInput, setMintInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<AuditedToken | null>(null);

  const runInspect = async (rawMint: string) => {
    const clean = rawMint.trim();
    if (!clean) return;
    setLoading(true);
    setError(null);
    try {
      const audited = await inspectSingleMint(clean, config);
      if (!audited) {
        setResult(null);
        setError('Pair Solana tidak ditemukan di DexScreener untuk alamat mint tersebut.');
        return;
      }
      setResult(audited);
    } catch (err) {
      setResult(null);
      setError(err instanceof Error ? err.message : 'Gagal mengaudit alamat mint tersebut.');
    } finally {
      setLoading(false);
    }
  };

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    runInspect(mintInput);
  };

  return (
    <div className="space-y-6 max-w-5xl">
      <div className="bg-[#111827] border border-slate-800 rounded-lg p-5 space-y-4">
        <div>
          <h2 className="text-base font-semibold text-slate-100">
            Pre-Trade Token Gatekeeper (Audit Sebelum Beli)
          </h2>
          <p className="mt-1 text-xs text-slate-400 leading-relaxed">
            Tempel alamat kontrak Solana (Base58) sebelum mengeksekusi uang riil. Mesin menjalankan 4 layer secara paralel: Hard-Quote Mint Allowlist, RugCheck + Hukum Pareto, Anti-Top Delay {config.reactionDelaySec}s, dan simulasi pulang-pergi Jupiter 0.1 SOL + 1.0 SOL.
          </p>
        </div>

        <form onSubmit={handleSubmit} className="flex flex-col sm:flex-row gap-3">
          <div className="relative flex-1">
            <Search className="w-4 h-4 text-slate-500 absolute left-3.5 top-1/2 -translate-y-1/2 pointer-events-none" />
            <input
              type="text"
              value={mintInput}
              onChange={(e) => setMintInput(e.target.value)}
              placeholder="Tempel Solana Token Mint Address (contoh: ...pump)"
              className="w-full pl-10 pr-4 py-2.5 bg-[#0B1120] border border-slate-700 rounded-md font-mono text-xs text-slate-100 placeholder:text-slate-500 focus:outline-none focus:border-emerald-500"
            />
          </div>
          <button
            type="submit"
            disabled={loading || !mintInput.trim()}
            className="inline-flex items-center justify-center gap-2 px-5 py-2.5 text-xs font-semibold text-slate-950 bg-emerald-400 hover:bg-emerald-300 disabled:opacity-50 rounded-md transition-colors whitespace-nowrap shrink-0 cursor-pointer"
          >
            <ShieldCheck className="w-4 h-4" />
            <span>{loading ? 'Mengaudit On-Chain...' : 'Audit Token Sekarang'}</span>
          </button>
        </form>

        {error && <div className="text-xs text-rose-400">{error}</div>}
      </div>

      {loading && (
        <div className="bg-[#111827] border border-slate-800 rounded-lg p-6 space-y-3">
          <div className="h-5 bg-slate-800 rounded w-1/3" />
          <div className="h-4 bg-slate-800/70 rounded w-2/3" />
          <div className="grid grid-cols-4 gap-3 pt-2">
            <div className="h-14 bg-slate-800/60 rounded" />
            <div className="h-14 bg-slate-800/60 rounded" />
            <div className="h-14 bg-slate-800/60 rounded" />
            <div className="h-14 bg-slate-800/60 rounded" />
          </div>
        </div>
      )}

      {!loading && result && (
        <TokenForensicPanel token={result} onTrackPosition={onTrackPosition} />
      )}

      {!loading && !result && !error && (
        <div className="bg-[#111827] border border-slate-800 rounded-lg p-8 text-center space-y-2">
          <p className="text-sm font-medium text-slate-200">
            Jangan pernah entry manual tanpa melewati 4 gerbang keras.
          </p>
          <p className="text-xs text-slate-400 max-w-xl mx-auto leading-relaxed">
            Setiap token yang Anda periksa di sini langsung dievaluasi terhadap batas atas entry pasca-delay, risiko Hollow-Bin 1.0 SOL, dan menghasilkan kalkulator ukuran posisi otomatis sesuai toleransi risiko modal Anda.
          </p>
        </div>
      )}
    </div>
  );
}
