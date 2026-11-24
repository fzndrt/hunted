import { useMemo, useRef, useState } from 'react';
import {
  Check,
  ChevronDown,
  ChevronRight,
  Copy,
  Download,
  FileCode2,
  FileDown,
  Folder,
  FolderOpen,
  FolderTree,
  List,
  Search,
} from 'lucide-react';
import { EMBEDDED_PYTHON_REPO_FILES, type EmbeddedRepoFile } from '../engine/embeddedPythonRepo';
import { buildClientRepoZipBlob } from '../utils/clientZip';

type RepoFile = EmbeddedRepoFile;

const FILE_PURPOSES: Record<string, string> = {
  'README.md': 'Panduan lengkap GitHub -> Vercel -> Cron -> Telegram Webhook + daftar Env Vars.',
  'vercel.json': 'Konfigurasi Vercel Python Serverless (maxDuration 60s, aman untuk Vercel Hobby Free).',
  'requirements.txt': 'Daftar dependensi Python minimal (httpx, websockets, python-dotenv, pytest).',
  '.env.example': 'Template Environment Variables untuk Vercel (TELEGRAM, CRON_SECRET, UPSTASH) & VPS.',
  '.gitignore': 'Mencegah file rahasia .env dan database SQLite ter-commit ke GitHub.',
  '.github/workflows/radar-cron.yml': 'Scheduler GRATIS tiap 5 menit via GitHub Actions untuk memicu Vercel /api/scan.',
  'api/index.py': 'Vercel GET / — Health check status Env Vars & penyimpanan (Upstash / SQLite).',
  'api/scan.py': 'Vercel GET/POST /api/scan — Jalankan Radar 8-feed + Alert Telegram + Exit Guard (dilindungi CRON_SECRET).',
  'api/webhook.py': 'Vercel POST /api/webhook — Terima perintah Telegram (/scan, /inspect, /track, /positions, /status).',
  'main.py': 'Entrypoint worker 24/7 opsional jika suatu saat dijalankan di VPS.',
  'bot/__init__.py': 'Penanda paket Python bot/.',
  'bot/config.py': 'Allowlist Hard-Quote Mint, 3 Koridor, & deteksi otomatis environment Vercel/Upstash.',
  'bot/models.py': 'Definisi dataclass terstruktur untuk seluruh 5 layer dan posisi Exit Guard.',
  'bot/security.py': 'Layer 1 — Veto keamanan biner (Hard-Quote, RugCheck fail-closed, Token-2022, LP, Pareto).',
  'bot/anti_top.py': 'Layer 2 — Koridor Early-Entry, Red Zone, delay 22s, Pre-ATH Guard, & Sleeper Revival.',
  'bot/execution.py': 'Layer 3 — Simulasi pulang-pergi Jupiter 0.1 SOL & 1.0 SOL (100% fail-closed).',
  'bot/scoring.py': 'Layer 4 — Skor aditif 5 pilar (0–100), Sleeper Revival tanpa penalti usia, & TP cepat +25%..+30%.',
  'bot/exit_guard.py': 'Layer 5 — Evaluasi Hard Stop-Loss, Breakeven, Trailing & AMM LP Retention √(MC/MC0).',
  'bot/storage.py': 'Persistensi Cloud Upstash Redis / Vercel KV REST (tahan cold start) + fallback SQLite otomatis.',
  'bot/feeds.py': 'Discovery paralel 8 feed (GeckoTerminal 5m/1h/New + DexScreener PumpSwap/Raydium/Profiles/Boosts/Top).',
  'bot/pipeline.py': 'Orkestrator audit paralel Layer 1–4 (teroptimasi latency Vercel Serverless).',
  'bot/pumpportal_stream.py': 'WebSocket PumpPortal (khusus mode VPS).',
  'bot/telegram_notifier.py': 'Formatter alert HTML aman & command handler bersama untuk Vercel Webhook & VPS.',
  'tests/test_engine.py': 'Suite 10 unit test pytest (anti-rugi, Sleeper Revival, AMM LP, & autentikasi Vercel).',
};

const COMPARISON_ROWS = [
  {
    area: 'Hard-Quote Validation',
    legacy: 'Cek ticker string ("NVDA", "USDG") + bypass otomatis jika liq >= $6.000 + salah kali q_amt * basePriceUsd.',
    clean: 'Strict allowlist alamat kontrak mint Solana saja (SOL, USDC, USDT, WBTC, cbBTC, USD1, PUMP). Zero bypass.',
  },
  {
    area: 'Anti-Top & Bypass Creep',
    legacy: '17.011 baris, 80 Modus, 11+ flag bypass di-OR-kan berantai di satu fungsi 1.635 baris (overfitting kronis).',
    clean: '5 layer modular terpisah. Veto keras tidak bisa di-bypass; kualitas sinyal dinilai lewat Additive Score 0–100.',
  },
  {
    area: 'Jupiter 1.0 SOL Depth Check',
    legacy: 'Fallback meng-clamp depth_loss_pct = min(12.5, ...) saat kuotasi gagal -> selalu lolos <13.5%.',
    clean: '100% fail-closed saat 1.0 SOL gagal atau HTTP 429; skala priceImpactPct * 100 akurat.',
  },
  {
    area: 'Exit Guard & Stop-Loss',
    legacy: 'Tanpa Hard Stop-Loss (slow bleed -50% lolos), deteksi LP pakai liquidity.usd mentah (keliru di AMM x*y=k).',
    clean: 'Unconditional Hard Stop-Loss, TP1 Jual 50% + Breakeven Stop, Trailing Peak, dan LP Retention ternormalisasi √(P/P0).',
  },
  {
    area: 'Persistence & Concurrency',
    legacy: 'File /tmp/*.json di Vercel (terhapus setiap cold start) tanpa thread lock saat 10 worker menulis paralel.',
    clean: 'SQLite WAL persisten di VPS + asyncio.Lock + deduplikasi cooldown alert. Anti lupa posisi & anti file korup.',
  },
  {
    area: 'PumpPortal & Smart Money',
    legacy: 'subscribeTokenTrade tanpa "keys", bonding curve vSol/85 lupa offset 30 SOL, Raydium/Serum dilabeli whale.',
    clean: 'Subscribe eksplisit dengan keys, rumus (vSol - 30) / 85, harga SOL dinamis, protokol dipisah dari daftar whale.',
  },
];

const CORRIDOR_MAP = [
  { range: '< $22K', status: 'Red Zone', action: 'Hindari — belum terbukti lolos seleksi awal / micro-rug', tone: 'text-rose-400' },
  { range: '$22K – $78K', status: 'Corridor A (Early Ignition)', action: 'Potensi 3x–10x+ · SL -18% · TP1 +45% · TP2 +110%', tone: 'text-emerald-400' },
  { range: '$78K – $112K', status: 'Red Zone ($100K Wall)', action: 'Tunggu tembus bersih > $112K atau pullback ke < $78K', tone: 'text-rose-400' },
  { range: '$112K – $172K', status: 'Corridor B (Post-100K)', action: 'Konfirmasi lolos sniper · SL -15% · TP1 +40% · TP2 +90%', tone: 'text-emerald-400' },
  { range: '$172K – $220K', status: 'Red Zone ($200K Wall)', action: 'Area dump dev modal kecil — jangan FOMO di sini', tone: 'text-rose-400' },
  { range: '$220K – $335K', status: 'Corridor C (Mid Expansion)', action: 'Opsional crowd momentum · SL -14% · TP1 +35% · TP2 +75%', tone: 'text-emerald-400' },
  { range: '> $335K', status: 'Red Zone / Late-Stage Top', action: 'Tidak masuk radar early-entry — kawal via moonbag saja', tone: 'text-rose-400' },
];

async function copyTextReliably(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // Fallback to textarea execCommand when iframe blocks Clipboard API
  }
  try {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.setAttribute('readonly', '');
    ta.style.position = 'fixed';
    ta.style.top = '-9999px';
    ta.style.left = '-9999px';
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    const ok = document.execCommand('copy');
    document.body.removeChild(ta);
    return ok;
  } catch {
    return false;
  }
}

function downloadSingleFile(file: RepoFile): void {
  const blob = new Blob([file.content], { type: 'text/plain;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = file.path.split('/').pop() || file.path;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

interface FolderGroup {
  id: string;
  label: string;
  files: RepoFile[];
}

export function ArchitectureView() {
  const repoFiles: readonly RepoFile[] = EMBEDDED_PYTHON_REPO_FILES;
  const [viewMode, setViewMode] = useState<'explorer' | 'all'>('explorer');
  const [selectedPath, setSelectedPath] = useState<string>('bot/__init__.py');
  const [filterQuery, setFilterQuery] = useState<string>('');
  const [openFolders, setOpenFolders] = useState<Record<string, boolean>>({
    api: true,
    bot: true,
    github: true,
    root: true,
    tests: true,
  });
  const [copiedKey, setCopiedKey] = useState<string | null>(null);
  const [showSpecs, setShowSpecs] = useState<boolean>(false);
  const textareaRefs = useRef<Record<string, HTMLTextAreaElement | null>>({});

  const filteredFiles = useMemo(() => {
    const q = filterQuery.trim().toLowerCase();
    if (!q) return repoFiles;
    return repoFiles.filter(
      (f) =>
        f.path.toLowerCase().includes(q) ||
        (FILE_PURPOSES[f.path] ?? '').toLowerCase().includes(q)
    );
  }, [repoFiles, filterQuery]);

  const folderGroups = useMemo<FolderGroup[]>(() => {
    const apiFiles: RepoFile[] = [];
    const botFiles: RepoFile[] = [];
    const githubFiles: RepoFile[] = [];
    const rootFiles: RepoFile[] = [];
    const testFiles: RepoFile[] = [];

    for (const f of filteredFiles) {
      if (f.path.startsWith('api/')) {
        apiFiles.push(f);
      } else if (f.path.startsWith('bot/')) {
        botFiles.push(f);
      } else if (f.path.startsWith('.github/')) {
        githubFiles.push(f);
      } else if (f.path.startsWith('tests/')) {
        testFiles.push(f);
      } else {
        rootFiles.push(f);
      }
    }

    const groups: FolderGroup[] = [];
    if (apiFiles.length > 0) groups.push({ id: 'api', label: 'api/ (Vercel)', files: apiFiles });
    if (botFiles.length > 0) groups.push({ id: 'bot', label: 'bot/', files: botFiles });
    if (githubFiles.length > 0) groups.push({ id: 'github', label: '.github/workflows/', files: githubFiles });
    if (rootFiles.length > 0) groups.push({ id: 'root', label: 'root (/)', files: rootFiles });
    if (testFiles.length > 0) groups.push({ id: 'tests', label: 'tests/', files: testFiles });
    return groups;
  }, [filteredFiles]);

  const activeFile: RepoFile =
    repoFiles.find((f) => f.path === selectedPath) ?? repoFiles[0];

  const activeLines = useMemo(() => activeFile.content.split('\n'), [activeFile]);

  const totalLines = useMemo(
    () => repoFiles.reduce((acc, f) => acc + f.content.split('\n').length, 0),
    [repoFiles]
  );

  const toggleFolder = (id: string) => {
    setOpenFolders((prev) => ({ ...prev, [id]: !prev[id] }));
  };

  const triggerCopy = async (key: string, text: string) => {
    const ok = await copyTextReliably(text);
    if (ok) {
      setCopiedKey(key);
      setTimeout(() => setCopiedKey((prev) => (prev === key ? null : prev)), 2000);
    } else {
      const ta = textareaRefs.current[key];
      if (ta) {
        ta.focus();
        ta.select();
      }
    }
  };

  const selectAllInBox = (key: string) => {
    const ta = textareaRefs.current[key];
    if (ta) {
      ta.focus();
      ta.select();
    }
  };

  const handleDownloadZip = () => {
    const blob = buildClientRepoZipBlob(repoFiles);
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'smart-money-fomo-radar-v2.zip';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };

  return (
    <div className="space-y-5">
      {/* Main IDE Repo Workbench */}
      <div className="bg-[#0F172A] border border-slate-800 rounded-lg overflow-hidden">
        {/* Workbench Top Bar */}
        <div className="px-4 py-3 border-b border-slate-800 bg-[#111827] flex flex-wrap items-center justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <FolderTree className="w-4 h-4 text-emerald-400 shrink-0" />
              <h2 className="text-sm font-semibold text-slate-100 truncate">
                python_bot/ — Arsitektur 5-Layer Clean ({repoFiles.length} File · {totalLines.toLocaleString()} baris)
              </h2>
            </div>
            <p className="text-xs text-slate-400 mt-0.5">
              Pilih file di panel kiri untuk melihat isi lengkap dan langsung <strong className="text-slate-200">Salin Isi File</strong> ke GitHub Anda, atau unduh seluruh repo sekaligus sebagai <code className="font-mono text-slate-300">.zip</code>.
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <div className="inline-flex items-center p-0.5 bg-[#0B1120] border border-slate-800 rounded-md">
              <button
                type="button"
                onClick={() => setViewMode('explorer')}
                className={`inline-flex items-center gap-1.5 px-2.5 py-1 text-xs font-medium rounded transition-colors cursor-pointer ${
                  viewMode === 'explorer'
                    ? 'bg-slate-800 text-white'
                    : 'text-slate-400 hover:text-slate-200'
                }`}
              >
                <FolderTree className="w-3.5 h-3.5" />
                <span>Explorer IDE</span>
              </button>
              <button
                type="button"
                onClick={() => setViewMode('all')}
                className={`inline-flex items-center gap-1.5 px-2.5 py-1 text-xs font-medium rounded transition-colors cursor-pointer ${
                  viewMode === 'all'
                    ? 'bg-slate-800 text-white'
                    : 'text-slate-400 hover:text-slate-200'
                }`}
              >
                <List className="w-3.5 h-3.5" />
                <span>Semua {repoFiles.length} File</span>
              </button>
            </div>

            <button
              type="button"
              onClick={handleDownloadZip}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold text-slate-950 bg-emerald-400 hover:bg-emerald-300 rounded-md transition-colors whitespace-nowrap cursor-pointer"
            >
              <Download className="w-3.5 h-3.5" />
              <span>Unduh .zip ({repoFiles.length} File)</span>
            </button>
          </div>
        </div>

        {/* Mode 1 (Default): IDE Split File Tree + Code Viewer */}
        {viewMode === 'explorer' && activeFile && (
          <div className="grid grid-cols-1 md:grid-cols-12 min-h-[540px] divide-y md:divide-y-0 md:divide-x divide-slate-800">
            {/* Mobile Compact File Selector (Immediately Visible Above Code on Phone Screens) */}
            <div className="md:hidden p-3 bg-[#0D1424] space-y-2">
              <label
                htmlFor="mobile-file-select"
                className="block text-[11px] font-semibold uppercase tracking-wider text-slate-400"
              >
                Pilih File ({repoFiles.length} File)
              </label>
              <select
                id="mobile-file-select"
                value={activeFile.path}
                onChange={(e) => setSelectedPath(e.target.value)}
                className="w-full px-3 py-2 text-xs font-mono bg-[#0B1120] border border-slate-700 rounded-md text-emerald-300 focus:outline-none focus:border-emerald-500"
              >
                {repoFiles.map((file, idx) => (
                  <option key={file.path} value={file.path}>
                    {idx + 1}. python_bot/{file.path} ({(file.sizeBytes / 1024).toFixed(1)} KB)
                  </option>
                ))}
              </select>
            </div>

            {/* Desktop Left File Tree Sidebar */}
            <div className="hidden md:flex md:col-span-4 lg:col-span-3 bg-[#0D1424] flex-col">
              {/* Filter Input */}
              <div className="p-2.5 border-b border-slate-800">
                <div className="relative">
                  <Search className="w-3.5 h-3.5 text-slate-500 absolute left-2.5 top-1/2 -translate-y-1/2 pointer-events-none" />
                  <input
                    type="text"
                    value={filterQuery}
                    onChange={(e) => setFilterQuery(e.target.value)}
                    placeholder="Cari file (mis. security.py)..."
                    className="w-full pl-8 pr-2.5 py-1.5 text-xs bg-[#0B1120] border border-slate-800 rounded text-slate-200 placeholder:text-slate-500 focus:outline-none focus:border-emerald-500/60"
                  />
                </div>
              </div>

              {/* Tree List */}
              <div className="flex-1 overflow-y-auto max-h-[580px] p-2 space-y-2">
                {folderGroups.map((group) => {
                  const isOpen = openFolders[group.id] ?? true;
                  return (
                    <div key={group.id}>
                      <button
                        type="button"
                        onClick={() => toggleFolder(group.id)}
                        className="w-full flex items-center gap-1.5 px-2 py-1 text-[11px] font-semibold uppercase tracking-wider text-slate-400 hover:text-slate-200 cursor-pointer"
                      >
                        {isOpen ? (
                          <ChevronDown className="w-3.5 h-3.5 text-slate-500 shrink-0" />
                        ) : (
                          <ChevronRight className="w-3.5 h-3.5 text-slate-500 shrink-0" />
                        )}
                        {isOpen ? (
                          <FolderOpen className="w-3.5 h-3.5 text-amber-400/80 shrink-0" />
                        ) : (
                          <Folder className="w-3.5 h-3.5 text-amber-400/80 shrink-0" />
                        )}
                        <span>{group.label}</span>
                        <span className="ml-auto font-mono text-[10px] text-slate-500 tabular-nums">
                          {group.files.length}
                        </span>
                      </button>

                      {isOpen && (
                        <div className="mt-0.5 space-y-0.5 pl-2">
                          {group.files.map((file) => {
                            const isSelected = file.path === activeFile.path;
                            const shortName = file.path.includes('/')
                              ? file.path.slice(file.path.indexOf('/') + 1)
                              : file.path;
                            return (
                              <button
                                key={file.path}
                                type="button"
                                onClick={() => setSelectedPath(file.path)}
                                className={`w-full flex items-center justify-between gap-2 px-2.5 py-1.5 text-left text-xs font-mono rounded transition-colors cursor-pointer ${
                                  isSelected
                                    ? 'bg-emerald-500/15 text-emerald-300 border border-emerald-500/30 font-semibold'
                                    : 'text-slate-300 hover:bg-slate-800/60 hover:text-white border border-transparent'
                                }`}
                              >
                                <span className="inline-flex items-center gap-2 truncate">
                                  <FileCode2
                                    className={`w-3.5 h-3.5 shrink-0 ${
                                      isSelected ? 'text-emerald-400' : 'text-slate-500'
                                    }`}
                                  />
                                  <span className="truncate">{shortName}</span>
                                </span>
                                <span className="text-[10px] text-slate-500 tabular-nums shrink-0">
                                  {(file.sizeBytes / 1024).toFixed(1)}K
                                </span>
                              </button>
                            );
                          })}
                        </div>
                      )}
                    </div>
                  );
                })}

                {folderGroups.length === 0 && (
                  <div className="px-3 py-6 text-center text-xs text-slate-500">
                    Tidak ada file yang cocok dengan &ldquo;{filterQuery}&rdquo;.
                  </div>
                )}
              </div>
            </div>

            {/* Right Active File Editor / Copy Pane */}
            <div className="md:col-span-8 lg:col-span-9 bg-[#0B1120] flex flex-col min-w-0">
              {/* File Path Tab & Action Toolbar */}
              <div className="px-4 py-3 border-b border-slate-800 bg-[#111827] flex flex-wrap items-center justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono text-xs sm:text-sm font-semibold text-emerald-300 break-all">
                      python_bot/{activeFile.path}
                    </span>
                    <button
                      type="button"
                      onClick={() => triggerCopy(`path-${activeFile.path}`, activeFile.path)}
                      title="Salin path file relatif untuk GitHub Create new file"
                      className="px-2 py-0.5 text-[11px] font-mono text-slate-300 bg-slate-800 hover:bg-slate-700 rounded transition-colors cursor-pointer"
                    >
                      {copiedKey === `path-${activeFile.path}` ? 'Path Tersalin' : 'Salin Path'}
                    </button>
                    <span className="font-mono text-[11px] text-slate-500 tabular-nums">
                      {activeLines.length} baris · {(activeFile.sizeBytes / 1024).toFixed(1)} KB
                    </span>
                  </div>
                  {FILE_PURPOSES[activeFile.path] && (
                    <div className="text-xs text-slate-400 mt-1">
                      {FILE_PURPOSES[activeFile.path]}
                    </div>
                  )}
                </div>

                <div className="flex flex-wrap items-center gap-2 shrink-0">
                  <button
                    type="button"
                    onClick={() => downloadSingleFile(activeFile)}
                    className="inline-flex items-center gap-1.5 px-2.5 py-1.5 text-xs font-medium bg-slate-800 hover:bg-slate-700 text-slate-200 rounded transition-colors cursor-pointer"
                  >
                    <FileDown className="w-3.5 h-3.5" />
                    <span>Unduh</span>
                  </button>
                  <button
                    type="button"
                    onClick={() => selectAllInBox(`explorer-${activeFile.path}`)}
                    className="px-2.5 py-1.5 text-xs font-medium bg-slate-800 hover:bg-slate-700 text-slate-200 rounded transition-colors cursor-pointer"
                  >
                    Blok Semua
                  </button>
                  <button
                    type="button"
                    onClick={() => triggerCopy(`explorer-${activeFile.path}`, activeFile.content)}
                    className={`inline-flex items-center gap-1.5 px-3.5 py-1.5 text-xs font-semibold rounded transition-colors cursor-pointer ${
                      copiedKey === `explorer-${activeFile.path}`
                        ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/40'
                        : 'bg-emerald-400 hover:bg-emerald-300 text-slate-950'
                    }`}
                  >
                    {copiedKey === `explorer-${activeFile.path}` ? (
                      <Check className="w-3.5 h-3.5" />
                    ) : (
                      <Copy className="w-3.5 h-3.5" />
                    )}
                    <span>
                      {copiedKey === `explorer-${activeFile.path}` ? 'Tersalin!' : 'Salin Isi File'}
                    </span>
                  </button>
                </div>
              </div>

              {/* Clean Full-Height Read-Only Code Editor Box (100% Native Select/Copy Friendly) */}
              <div className="flex-1 relative flex bg-[#0B1120] min-h-[520px]">
                <textarea
                  ref={(el) => {
                    textareaRefs.current[`explorer-${activeFile.path}`] = el;
                  }}
                  readOnly
                  spellCheck={false}
                  wrap="off"
                  value={activeFile.content}
                  aria-label={`Kode sumber ${activeFile.path}`}
                  className="w-full min-h-[540px] flex-1 p-4 bg-[#0B1120] font-mono text-[12.5px] leading-6 text-slate-200 focus:outline-none focus:ring-1 focus:ring-inset focus:ring-emerald-500/30 resize-y overflow-auto"
                />
              </div>

              {/* Bottom Quick Prev/Next File Navigation */}
              <div className="px-4 py-2.5 border-t border-slate-800 bg-[#0F172A] flex items-center justify-between gap-3 text-xs">
                {(() => {
                  const idx = repoFiles.findIndex((f) => f.path === activeFile.path);
                  const prevFile = idx > 0 ? repoFiles[idx - 1] : null;
                  const nextFile = idx >= 0 && idx < repoFiles.length - 1 ? repoFiles[idx + 1] : null;
                  return (
                    <>
                      <button
                        type="button"
                        disabled={!prevFile}
                        onClick={() => prevFile && setSelectedPath(prevFile.path)}
                        className="px-2.5 py-1 rounded bg-slate-800/80 hover:bg-slate-700 disabled:opacity-40 text-slate-200 font-mono truncate max-w-[44%] cursor-pointer disabled:cursor-default"
                      >
                        {prevFile ? `← ${prevFile.path}` : '← Awal'}
                      </button>
                      <span className="font-mono text-[11px] text-slate-500 tabular-nums shrink-0">
                        {idx + 1} / {repoFiles.length}
                      </span>
                      <button
                        type="button"
                        disabled={!nextFile}
                        onClick={() => nextFile && setSelectedPath(nextFile.path)}
                        className="px-2.5 py-1 rounded bg-slate-800/80 hover:bg-slate-700 disabled:opacity-40 text-slate-200 font-mono truncate max-w-[44%] cursor-pointer disabled:cursor-default"
                      >
                        {nextFile ? `${nextFile.path} →` : 'Akhir →'}
                      </button>
                    </>
                  );
                })()}
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Mode 2: All 18 Files Stacked — Ready to Copy-Paste One by One */}
      {viewMode === 'all' && repoFiles.length > 0 && (
        <div className="space-y-4">
          {repoFiles.map((file, idx) => {
            const key = `card-${file.path}`;
            const lineCount = file.content.split('\n').length;
            const isCopied = copiedKey === key;
            const boxHeightPx = Math.min(460, Math.max(130, lineCount * 19 + 24));
            return (
              <section
                key={file.path}
                id={`file-card-${file.path}`}
                className="bg-[#111827] border border-slate-800 rounded-lg overflow-hidden scroll-mt-20"
              >
                <div className="px-4 py-3 border-b border-slate-800 bg-[#0F172A] flex flex-wrap items-center justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-[11px] text-slate-500 tabular-nums">
                        #{idx + 1}
                      </span>
                      <code className="font-mono text-xs sm:text-sm font-semibold text-emerald-300 break-all">
                        python_bot/{file.path}
                      </code>
                      <span className="font-mono text-[11px] text-slate-500 tabular-nums whitespace-nowrap">
                        ({lineCount} baris)
                      </span>
                    </div>
                    {FILE_PURPOSES[file.path] && (
                      <p className="text-xs text-slate-400 mt-0.5">{FILE_PURPOSES[file.path]}</p>
                    )}
                  </div>

                  <div className="flex items-center gap-2 shrink-0">
                    <button
                      type="button"
                      onClick={() => selectAllInBox(key)}
                      className="px-2.5 py-1.5 text-xs font-medium bg-slate-800 hover:bg-slate-700 text-slate-200 rounded-md transition-colors cursor-pointer whitespace-nowrap"
                    >
                      Blok Semua
                    </button>
                    <button
                      type="button"
                      onClick={() => triggerCopy(key, file.content)}
                      className={`inline-flex items-center gap-1.5 px-3.5 py-1.5 text-xs font-semibold rounded-md transition-colors cursor-pointer whitespace-nowrap ${
                        isCopied
                          ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/40'
                          : 'bg-emerald-400 hover:bg-emerald-300 text-slate-950'
                      }`}
                    >
                      {isCopied ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
                      <span>{isCopied ? 'Tersalin!' : 'Salin Isi File'}</span>
                    </button>
                  </div>
                </div>

                <textarea
                  ref={(el) => {
                    textareaRefs.current[key] = el;
                  }}
                  readOnly
                  spellCheck={false}
                  wrap="off"
                  value={file.content}
                  style={{ height: `${boxHeightPx}px` }}
                  className="w-full p-4 bg-[#0B1120] font-mono text-[12px] leading-relaxed text-slate-200 focus:outline-none focus:ring-1 focus:ring-inset focus:ring-emerald-500/40 resize-y block"
                />
              </section>
            );
          })}
        </div>
      )}

      {/* Collapsible Architecture Specs (Peta Koridor & Perbandingan Arsitektur) */}
      <div className="bg-[#111827] border border-slate-800 rounded-lg overflow-hidden">
        <button
          type="button"
          onClick={() => setShowSpecs((v) => !v)}
          className="w-full px-5 py-3.5 flex items-center justify-between gap-3 text-left hover:bg-slate-800/40 transition-colors cursor-pointer"
        >
          <div>
            <div className="text-sm font-semibold text-slate-100">
              Ringkasan Peta Koridor &amp; Perbandingan Arsitektur 5-Layer
            </div>
            <div className="text-xs text-slate-400 mt-0.5">
              Klik untuk {showSpecs ? 'menyembunyikan' : 'membuka'} tabel koridor Market Cap ($22K–$335K) &amp; audit perbandingan monolit lama vs struktur baru.
            </div>
          </div>
          {showSpecs ? (
            <ChevronDown className="w-4 h-4 text-slate-400 shrink-0" />
          ) : (
            <ChevronRight className="w-4 h-4 text-slate-400 shrink-0" />
          )}
        </button>

        {showSpecs && (
          <div className="border-t border-slate-800 divide-y divide-slate-800">
            <div className="overflow-x-auto">
              <table className="w-full text-left border-collapse text-xs">
                <thead>
                  <tr className="border-b border-slate-800 text-[11px] text-slate-400 bg-[#0F172A]">
                    <th className="py-2.5 px-5">Rentang Market Cap</th>
                    <th className="py-2.5 px-4">Klasifikasi</th>
                    <th className="py-2.5 px-5">Aturan Disiplin Modal</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/70">
                  {CORRIDOR_MAP.map((row) => (
                    <tr key={row.range}>
                      <td className="py-3 px-5 font-mono font-semibold text-slate-100 whitespace-nowrap tabular-nums">
                        {row.range}
                      </td>
                      <td className={`py-3 px-4 font-semibold whitespace-nowrap ${row.tone}`}>
                        {row.status}
                      </td>
                      <td className="py-3 px-5 text-slate-300">{row.action}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="overflow-x-auto">
              <table className="w-full text-left border-collapse text-xs">
                <thead>
                  <tr className="border-b border-slate-800 text-[11px] text-slate-400 bg-[#0F172A]">
                    <th className="py-2.5 px-5">Modul</th>
                    <th className="py-2.5 px-4">Masalah di Monolit Lama</th>
                    <th className="py-2.5 px-5">Solusi Struktur Bersih</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/70">
                  {COMPARISON_ROWS.map((r) => (
                    <tr key={r.area}>
                      <td className="py-3.5 px-5 font-semibold text-slate-100 whitespace-nowrap align-top">
                        {r.area}
                      </td>
                      <td className="py-3.5 px-4 text-rose-300/90 align-top leading-relaxed">
                        {r.legacy}
                      </td>
                      <td className="py-3.5 px-5 text-emerald-300/90 align-top leading-relaxed">
                        {r.clean}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
