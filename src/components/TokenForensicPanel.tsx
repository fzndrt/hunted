import { useState } from 'react';
import {
  AlertTriangle,
  CheckCircle2,
  Copy,
  Check,
  ExternalLink,
  ShieldAlert,
  ShieldCheck,
  Plus,
} from 'lucide-react';
import type { AuditedToken } from '../engine/types';
import { formatAge, formatCompactUsd, formatPct, formatPriceUsd, shortMint } from '../utils/format';

interface TokenForensicPanelProps {
  token: AuditedToken;
  onTrackPosition: (payload: {
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
  }) => Promise<void>;
}

export function TokenForensicPanel({ token, onTrackPosition }: TokenForensicPanelProps) {
  const [copied, setCopied] = useState(false);
  const [capitalUsd, setCapitalUsd] = useState<string>('1000');
  const [riskPerTradePct, setRiskPerTradePct] = useState<string>('2');
  const [tracking, setTracking] = useState(false);
  const [trackedMsg, setTrackedMsg] = useState<string | null>(null);

  const capital = Math.max(0, Number(capitalUsd) || 0);
  const riskPct = Math.min(20, Math.max(0.25, Number(riskPerTradePct) || 2));
  const stopPct = token.tradePlan?.hardStopPct ?? 15;
  const maxRiskUsd = capital * (riskPct / 100);
  const recommendedPositionUsd = stopPct > 0 ? Math.min(capital, Math.round(maxRiskUsd / (stopPct / 100))) : 0;

  const allChecks = [
    ...token.security.checks,
    ...token.antiTop.checks,
    ...token.execution.checks,
  ];

  const handleCopyMint = async () => {
    try {
      await navigator.clipboard.writeText(token.mint);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard unavailable in restricted context
    }
  };

  const handleTrack = async () => {
    setTracking(true);
    setTrackedMsg(null);
    try {
      await onTrackPosition({
        mint: token.mint,
        pairAddress: token.pairAddress,
        symbol: token.symbol,
        name: token.name,
        entryMc: token.antiTop.metrics.spotMc,
        entryPriceUsd: token.priceUsd,
        entryLiqUsd: token.antiTop.metrics.liqUsd,
        positionUsd: recommendedPositionUsd || 100,
        hardStopPct: token.tradePlan?.hardStopPct ?? 15,
        tp1Pct: token.tradePlan?.tp1Pct ?? 45,
        tp2Pct: token.tradePlan?.tp2Pct ?? 100,
      });
      setTrackedMsg('Posisi masuk ke Exit Guard');
    } catch (err) {
      setTrackedMsg(err instanceof Error ? err.message : 'Gagal melacak posisi');
    } finally {
      setTracking(false);
    }
  };

  const verdictStyle =
    token.verdict === 'ENTRY_READY'
      ? 'text-emerald-400'
      : token.verdict === 'WATCH_PULLBACK'
      ? 'text-amber-400'
      : 'text-rose-400';

  const verdictLabel =
    token.verdict === 'ENTRY_READY'
      ? 'Entry Ready'
      : token.verdict === 'WATCH_PULLBACK'
      ? 'Watch Pullback'
      : 'Rejected (Veto)';

  return (
    <div className="bg-[#111827] border border-slate-800 rounded-lg divide-y divide-slate-800/80">
      {/* Header Summary */}
      <div className="p-5 space-y-3">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2 text-xs text-slate-400">
              <span className={`font-semibold ${verdictStyle}`}>{verdictLabel}</span>
              {token.antiTop.metrics.isSleeperRevival && (
                <>
                  <span aria-hidden="true">·</span>
                  <span className="font-mono font-semibold text-cyan-300">
                    SLEEPER REVIVAL (Vol x{token.antiTop.metrics.volWake5mRatio.toFixed(1)})
                  </span>
                </>
              )}
              <span aria-hidden="true">·</span>
              <span className="capitalize">{token.dexId}</span>
              <span aria-hidden="true">·</span>
              <span>Usia {formatAge(token.antiTop.metrics.ageMinutes)}</span>
            </div>
            <h2 className="mt-1 text-lg font-semibold text-slate-100 truncate">
              {token.name} <span className="font-mono text-slate-400">${token.symbol}</span>
            </h2>
            <div className="mt-1 flex items-center gap-2 text-xs font-mono text-slate-400">
              <span>{shortMint(token.mint)}</span>
              <button
                type="button"
                onClick={handleCopyMint}
                className="inline-flex items-center gap-1 text-slate-400 hover:text-slate-100 transition-colors cursor-pointer"
                title="Salin alamat mint"
              >
                {copied ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
                <span>{copied ? 'Disalin' : 'Salin'}</span>
              </button>
            </div>
          </div>

          <div className="text-right shrink-0">
            <div className="text-xs text-slate-400">Skor Konviksi</div>
            <div className={`font-mono text-2xl font-semibold tabular-nums ${verdictStyle}`}>
              {token.totalScore}
              <span className="text-sm text-slate-500">/100</span>
            </div>
          </div>
        </div>

        <p className="text-sm text-slate-300 leading-relaxed">{token.verdictHeadline}</p>

        {/* External Verified Links */}
        <div className="flex flex-wrap items-center gap-4 pt-1 text-xs font-medium">
          <a
            href={token.links.jupiter}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 text-emerald-400 hover:text-emerald-300 transition-colors"
          >
            <span>Jupiter Swap</span>
            <ExternalLink className="w-3.5 h-3.5" />
          </a>
          <a
            href={token.links.dexScreener}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 text-slate-300 hover:text-white transition-colors"
          >
            <span>DexScreener</span>
            <ExternalLink className="w-3.5 h-3.5" />
          </a>
          <a
            href={token.links.rugcheck}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 text-slate-300 hover:text-white transition-colors"
          >
            <span>RugCheck</span>
            <ExternalLink className="w-3.5 h-3.5" />
          </a>
          <a
            href={token.links.solscan}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 text-slate-300 hover:text-white transition-colors"
          >
            <span>Solscan</span>
            <ExternalLink className="w-3.5 h-3.5" />
          </a>
        </div>
      </div>

      {/* Key Live Market Snapshot */}
      <div className="grid grid-cols-2 sm:grid-cols-4 divide-y sm:divide-y-0 sm:divide-x divide-slate-800/80">
        <div className="p-4">
          <div className="text-xs text-slate-400">Spot MC / Harga</div>
          <div className="mt-1 font-mono text-sm font-semibold text-slate-100 tabular-nums">
            {formatCompactUsd(token.antiTop.metrics.spotMc)}
          </div>
          <div className="mt-0.5 font-mono text-xs text-slate-400 tabular-nums">
            {formatPriceUsd(token.priceUsd)}
          </div>
        </div>
        <div className="p-4">
          <div className="text-xs text-slate-400">Proyeksi MC (+Delay)</div>
          <div className="mt-1 font-mono text-sm font-semibold text-slate-100 tabular-nums">
            {formatCompactUsd(token.antiTop.metrics.projectedEntryMc)}
          </div>
          <div className="mt-0.5 font-mono text-xs text-slate-400 tabular-nums">
            Drift {formatPct(token.antiTop.metrics.projectedDelayDriftPct)} · Net{' '}
            {formatPct(token.antiTop.metrics.netHeadroomPct)}
          </div>
        </div>
        <div className="p-4">
          <div className="text-xs text-slate-400">Likuiditas & Quote</div>
          <div className="mt-1 font-mono text-sm font-semibold text-slate-100 tabular-nums">
            {formatCompactUsd(token.antiTop.metrics.liqUsd)}
          </div>
          <div className="mt-0.5 font-mono text-xs text-slate-400 tabular-nums">
            {token.antiTop.metrics.liqMcRatioPct.toFixed(1)}% MC · {token.security.metrics.hardQuoteSymbol}
          </div>
        </div>
        <div className="p-4">
          <div className="text-xs text-slate-400">Jupiter 0.1S / 1.0S</div>
          <div className="mt-1 font-mono text-sm font-semibold text-slate-100 tabular-nums">
            {token.execution.metrics.roundTripLoss01SolPct !== null
              ? `-${token.execution.metrics.roundTripLoss01SolPct}%`
              : 'Fail'}{' '}
            /{' '}
            {token.execution.metrics.depthLoss1SolPct !== null
              ? `-${token.execution.metrics.depthLoss1SolPct}%`
              : 'Fail'}
          </div>
          <div className="mt-0.5 font-mono text-xs text-slate-400 tabular-nums">
            {token.execution.status}
          </div>
        </div>
      </div>

      {/* 5-Pillar Additive Score Breakdown */}
      <div className="p-5 space-y-3">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-slate-100">Rincian 5 Pilar Skor (Additive)</h3>
          <span className="text-xs text-slate-400">Tanpa bypass tersembunyi</span>
        </div>
        <div className="space-y-2.5">
          {token.pillars.map((pillar) => {
            const pct = pillar.maxScore > 0 ? Math.round((pillar.score / pillar.maxScore) * 100) : 0;
            return (
              <div key={pillar.name} className="space-y-1">
                <div className="flex items-center justify-between text-xs">
                  <div className="flex items-center gap-2 min-w-0">
                    <span className="font-medium text-slate-200 whitespace-nowrap">{pillar.name}</span>
                    <span className="text-slate-500 truncate">{pillar.summary}</span>
                  </div>
                  <span className="font-mono font-medium text-slate-200 tabular-nums shrink-0 ml-2">
                    {pillar.score}/{pillar.maxScore}
                  </span>
                </div>
                <div className="h-1.5 w-full bg-slate-800 rounded-sm overflow-hidden">
                  <div
                    className={`h-full transition-all duration-200 ${
                      pct >= 75 ? 'bg-emerald-500' : pct >= 45 ? 'bg-amber-500' : 'bg-rose-500'
                    }`}
                    style={{ width: `${pct}%` }}
                  />
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Real-Money Risk Sizing & Trade Plan */}
      <div className="p-5 space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h3 className="text-sm font-semibold text-slate-100">Rencana Eksekusi & Position Sizing (Real Money)</h3>
            <p className="text-xs text-slate-400 mt-0.5">
              Ukuran beli dihitung mundur dari risiko maksimal per trade dan jarak Hard Stop-Loss.
            </p>
          </div>
          {token.verdict !== 'REJECTED' && (
            <button
              type="button"
              onClick={handleTrack}
              disabled={tracking}
              className="inline-flex items-center gap-1.5 px-3.5 py-2 text-xs font-medium text-slate-950 bg-emerald-400 hover:bg-emerald-300 disabled:opacity-50 rounded-md transition-colors whitespace-nowrap shrink-0 cursor-pointer"
            >
              <Plus className="w-3.5 h-3.5" />
              <span>{tracking ? 'Menyimpan...' : 'Lacak di Exit Guard'}</span>
            </button>
          )}
        </div>

        {trackedMsg && (
          <div className="text-xs font-medium text-emerald-400">{trackedMsg}</div>
        )}

        {token.tradePlan ? (
          <>
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
              <div>
                <label className="block text-xs text-slate-400 mb-1" htmlFor="capital-input">
                  Total Modal Trading ($)
                </label>
                <input
                  id="capital-input"
                  type="number"
                  min={10}
                  step={50}
                  value={capitalUsd}
                  onChange={(e) => setCapitalUsd(e.target.value)}
                  className="w-full px-3 py-1.5 bg-[#0B1120] border border-slate-700 rounded-md text-sm font-mono text-slate-100 focus:outline-none focus:border-emerald-500"
                />
              </div>
              <div>
                <label className="block text-xs text-slate-400 mb-1" htmlFor="risk-input">
                  Risiko per Trade (%)
                </label>
                <input
                  id="risk-input"
                  type="number"
                  min={0.5}
                  max={10}
                  step={0.5}
                  value={riskPerTradePct}
                  onChange={(e) => setRiskPerTradePct(e.target.value)}
                  className="w-full px-3 py-1.5 bg-[#0B1120] border border-slate-700 rounded-md text-sm font-mono text-slate-100 focus:outline-none focus:border-emerald-500"
                />
              </div>
              <div className="col-span-2 sm:col-span-1 flex flex-col justify-end pb-1">
                <span className="text-xs text-slate-400">Ukuran Entry Maksimal</span>
                <span className="font-mono text-base font-semibold text-emerald-400 tabular-nums">
                  ${recommendedPositionUsd.toLocaleString()}
                </span>
                <span className="font-mono text-xs text-slate-500 tabular-nums">
                  Max Rugi ${Math.round(maxRiskUsd).toLocaleString()} saat SL -{stopPct}%
                </span>
              </div>
            </div>

            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 pt-2 border-t border-slate-800/80 text-xs">
              <div>
                <div className="text-slate-400">Batas Atas Entry</div>
                <div className="mt-0.5 font-mono font-semibold text-slate-100 tabular-nums">
                  {formatCompactUsd(token.tradePlan.maxSafeEntryMc)}
                </div>
                <div className="text-slate-500">Jangan FOMO di atas ini</div>
              </div>
              <div>
                <div className="text-slate-400">Hard Stop-Loss</div>
                <div className="mt-0.5 font-mono font-semibold text-rose-400 tabular-nums">
                  {formatCompactUsd(token.tradePlan.hardStopMc)} (-{token.tradePlan.hardStopPct}%)
                </div>
                <div className="text-slate-500">Wajib cut disiplin</div>
              </div>
              <div>
                <div className="text-slate-400">TP1 (Jual 50% + BE)</div>
                <div className="mt-0.5 font-mono font-semibold text-emerald-400 tabular-nums">
                  {formatCompactUsd(token.tradePlan.tp1Mc)} (+{token.tradePlan.tp1Pct}%)
                </div>
                <div className="text-slate-500">R:R {token.tradePlan.riskRewardToTp1}x</div>
              </div>
              <div>
                <div className="text-slate-400">TP2 (Jual 30% + Trail)</div>
                <div className="mt-0.5 font-mono font-semibold text-emerald-300 tabular-nums">
                  {formatCompactUsd(token.tradePlan.tp2Mc)} (+{token.tradePlan.tp2Pct}%)
                </div>
                <div className="text-slate-500">R:R {token.tradePlan.riskRewardToTp2}x</div>
              </div>
            </div>
          </>
        ) : (
          <div className="text-xs text-rose-400">
            Rencana entry dinonaktifkan karena token gagal pada gerbang keamanan / eksekusi keras (Veto).
          </div>
        )}
      </div>

      {/* Complete Gate-by-Gate Forensic Table */}
      <div className="p-5 space-y-3">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-slate-100">Audit Gerbang 4 Layer ({allChecks.length} Aturan)</h3>
          <span className="text-xs font-mono text-slate-400 tabular-nums">
            {allChecks.filter((c) => c.passed).length}/{allChecks.length} Lolos
          </span>
        </div>

        <div className="divide-y divide-slate-800/70">
          {allChecks.map((check) => (
            <div key={check.id} className="py-3 first:pt-0 last:pb-0 flex items-start gap-3">
              <div className="mt-0.5 shrink-0">
                {check.passed ? (
                  <CheckCircle2 className="w-4 h-4 text-emerald-400" />
                ) : check.severity === 'WAIT_DIP' ? (
                  <AlertTriangle className="w-4 h-4 text-amber-400" />
                ) : (
                  <ShieldAlert className="w-4 h-4 text-rose-400" />
                )}
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <div className="text-xs font-semibold text-slate-100">
                    {check.label}{' '}
                    <span className="font-normal text-slate-500">· {check.layer}</span>
                  </div>
                  <div className="font-mono text-xs text-slate-300 tabular-nums">{check.actual}</div>
                </div>
                <p className="mt-1 text-xs text-slate-400 leading-relaxed">{check.detail}</p>
                <div className="mt-0.5 font-mono text-[11px] text-slate-500">
                  Syarat: {check.threshold}
                </div>
              </div>
            </div>
          ))}
        </div>

        {token.security.passed && token.execution.passed && (
          <div className="pt-2 flex items-center gap-2 text-xs text-emerald-400">
            <ShieldCheck className="w-4 h-4 shrink-0" />
            <span>Kontrak, distribusi Pareto, hard-quote mint, dan jual 1.0 SOL terverifikasi bersih.</span>
          </div>
        )}
      </div>
    </div>
  );
}
