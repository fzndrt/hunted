"""Pengirim Alert & Command Handler Telegram (HTML-safe, non-blocking)."""

from __future__ import annotations

import html
import logging
from typing import Optional
import httpx

from .config import BotConfig, SOL_MINT
from .models import AuditResult, TrackedPosition

logger = logging.getLogger(__name__)


def _esc(val: object) -> str:
    return html.escape(str(val or ""))


def format_early_microcap_alert(audit: AuditResult) -> str:
    """Format Alert JALUR 1 — Early Micro-Cap ($25K–$850K · Target Ratusan Persen)."""
    p = audit.pair
    at = audit.anti_top
    ex = audit.execution
    sec = audit.security
    st = audit.structure
    plan = audit.trade_plan

    age_days = at.age_minutes / 1440.0
    age_label = f"{age_days:.1f} hari" if age_days >= 1.0 else (f"{at.age_minutes / 60.0:.1f} jam" if at.age_minutes >= 60.0 else f"{at.age_minutes:.0f} menit")
    total_pools = max(int(p.pool_count or 1), int(sec.active_markets_count or 1))
    if st is not None and st.regime == "BULLISH_CONSOLIDATION_BREAKOUT":
        title_line = f"🟢 <b>[JALUR 1 · DAILY BREAKOUT] — ${_esc(p.symbol)}</b> (Skor <b>{audit.total_score}/100</b>)"
    elif at.is_corridor_d_viral_base:
        title_line = f"🟢 <b>[JALUR 1 · VIRAL BASE D] — ${_esc(p.symbol)}</b> (Skor <b>{audit.total_score}/100</b>)"
    elif at.is_hyper_viral_fasttrack:
        title_line = f"🟢 <b>[JALUR 1 · EARLY FAST-TRACK 6–9m] — ${_esc(p.symbol)}</b> (Skor <b>{audit.total_score}/100</b>)"
    elif st is not None and st.regime == "PULLBACK_RECLAIM" and at.is_viral_velocity:
        title_line = f"🟢 <b>[JALUR 1 · VIRAL PULLBACK RECLAIM] — ${_esc(p.symbol)}</b> (Skor <b>{audit.total_score}/100</b>)"
    elif at.is_sleeper_revival:
        title_line = f"🟢 <b>[JALUR 1 · SLEEPER REVIVAL] — ${_esc(p.symbol)}</b> (Skor <b>{audit.total_score}/100</b>)"
    elif at.is_steady_organic_climber:
        title_line = f"🟢 <b>[JALUR 1 · STEADY CLIMBER] — ${_esc(p.symbol)}</b> (Skor <b>{audit.total_score}/100</b>)"
    else:
        title_line = f"🟢 <b>[JALUR 1 · EARLY MICRO-CAP] — ${_esc(p.symbol)}</b> (Skor <b>{audit.total_score}/100</b>)"

    if st is not None and st.regime == "BULLISH_CONSOLIDATION_BREAKOUT":
        chart_line = (
            f"• Chart 1h (Akumulasi): Box <b>{st.base_range_pct:.0f}%</b> · Posisi <b>{st.range_position_pct:.0f}%</b> · "
            f"GreenVol <b>{st.green_vol_share_pct:.0f}%</b> · Wick <code>{st.upper_wick_pct:.0f}%</code> · Ekspansi <b>{st.vol_expansion_ratio:.1f}x</b>"
        )
    elif st is not None and st.ohlcv_verified:
        chart_line = (
            f"• Chart 5m ({_esc(st.regime)}): VWAP <b>{st.vwap_deviation_pct:+.1f}%</b> · "
            f"DD Peak <code>-{st.drawdown_from_peak_pct:.1f}%</code> · Wick <code>{st.upper_wick_pct:.0f}%</code> · GreenVol4 <b>{st.green_vol_last4_pct:.0f}%</b>"
        )
    else:
        chart_line = f"• Orderflow: 5m Accel <b>{at.vol_m5_accel_ratio:.2f}x</b> · 6h Buy <b>{at.buy_ratio_6h_pct:.0f}%</b>"

    boost_label = "Tanpa Paid Boost (Organik)" if not p.is_boosted else "Ada Paid Boost"
    pool_label = f"{total_pools} pool aktif (Smart LP)" if total_pools >= 2 else "1 pool utama"
    comm_parts = []
    if p.has_socials:
        comm_parts.append(f"Socials({p.social_count})")
    if p.has_website:
        comm_parts.append("Web")
    comm_label = " + ".join(comm_parts) if comm_parts else "Anonim"

    lines = [
        title_line,
        "🏷️ <i>Jalur 1: Koin Micro-Cap Awal ($25K–$850K) · Potensi Multi-X + Disciplined TP</i>",
        f"Nama: {_esc(p.name)} | DEX: <code>{_esc(p.dex_id)}</code> | Quote: <b>{_esc(sec.hard_quote_symbol)}</b> | Usia: <b>{age_label}</b>",
        f"Mint: <code>{_esc(p.mint)}</code>",
        "",
        "<b>🔥 5 Bukti Bahan Bakar Kenaikan (v5 Winner-DNA):</b>",
        f"1. Densitas Holder: <b>{sec.total_holders:,} dompet</b> (<b>${sec.mc_per_holder_usd:,.0f}/holder</b> · {sec.holders_per_min:.1f}/m)",
        f"2. Arus Modal: Vol5m <b>${p.vol_m5:,.0f}</b> (${at.avg_ticket_5m_usd:.0f}/tx) · Vol1h <b>${p.vol_h1:,.0f}</b> (<b>{at.turnover_h1_ratio:.2f}x</b> MC)"
        + (" · 🐋 <b>Whale Absorption</b>" if at.whale_absorption else ""),
        f"3. Anti-Freefall Chart: {chart_line[2:]}",
        f"4. Likuiditas & Smart LP: <b>${at.liq_usd:,.0f}</b> ({at.liq_mc_ratio_pct:.0f}% MC) · <b>{pool_label}</b>",
        f"5. Komunitas & Supply: <b>{comm_label}</b> · <b>{boost_label}</b> · Dev <code>{sec.dev_pct}%</code> · Top1 <code>{sec.top1_pct}%</code> · Top10 <code>{sec.top10_pct}%</code>",
        "",
        f"• Spot MC: <b>${at.spot_mc:,.0f}</b> → Proyeksi (+delay): <b>${at.projected_entry_mc:,.0f}</b>",
        f"• Koridor: {_esc(at.corridor_label)} (Headroom <b>+{at.net_headroom_pct:.1f}%</b>)",
        f"• 5m / 1h / 6h / 24h: <code>{at.p_m5:+.1f}%</code> / <code>{at.p_h1:+.1f}%</code> / <code>{at.p_h6:+.1f}%</code> / <code>{at.p_h24:+.1f}%</code>",
        f"• Buy 5m/1h/6h: <code>{at.buy_ratio_5m_pct:.0f}%/{at.buy_ratio_1h_pct:.0f}%/{at.buy_ratio_6h_pct:.0f}%</code> | Jupiter: 0.1S <code>-{ex.round_trip_loss_01_sol_pct}%</code> · 1.0S <code>-{ex.depth_loss_1_sol_pct}%</code>",
    ]
    if plan:
        lines.extend(
            [
                "",
                "<b>Rencana Disiplin Modal (Jalur 1 — Micro-Cap):</b>",
                f"• Batas Atas Entry: <b>&lt;= ${plan.max_safe_entry_mc:,.0f}</b> (jangan kejar di atas ini)",
                f"• Hard Stop-Loss: <b>${plan.hard_stop_mc:,.0f} (-{plan.hard_stop_pct:.0f}%)</b>",
                f"• TP1 (Jual 50% + BE): <b>${plan.tp1_mc:,.0f} (+{plan.tp1_pct:.0f}%)</b> [R:R {plan.risk_reward_to_tp1}x]",
                f"• TP2 (Jual 30% + Trail): <b>${plan.tp2_mc:,.0f} (+{plan.tp2_pct:.0f}%)</b> [R:R {plan.risk_reward_to_tp2}x]",
            ]
        )
    lines.append(
        f'\n<a href="https://jup.ag/swap/{SOL_MINT}-{p.mint}">Jupiter</a> · '
        f'<a href="https://dexscreener.com/solana/{p.mint}">DexScreener</a> · '
        f'<a href="https://rugcheck.xyz/tokens/{p.mint}">RugCheck</a>'
    )
    return "\n".join(lines)


def format_veteran_swing_alert(audit: AuditResult) -> str:
    """Format Alert JALUR 2 — Veteran Swing ($850K–$10M, Usia >= 7 Hari · Keluar-Masuk Cepat +18%..+35%)."""
    p = audit.pair
    at = audit.anti_top
    ex = audit.execution
    sec = audit.security
    st = audit.structure
    plan = audit.trade_plan

    age_days = at.age_minutes / 1440.0
    setup_label = (
        "PULLBACK RECLAIM"
        if (st is not None and st.regime == "PULLBACK_RECLAIM")
        else "BASE BREAKOUT"
    )
    chart_detail = (
        f"Box <b>{st.base_range_pct:.0f}%</b> · DD Peak <code>-{st.drawdown_from_peak_pct:.1f}%</code> · "
        f"GreenVol <b>{st.green_vol_share_pct:.0f}%</b> · Vol <b>{st.vol_expansion_ratio:.1f}x</b>"
        if st is not None and st.ohlcv_verified
        else f"1h <code>{at.p_h1:+.1f}%</code> · 6h <code>{at.p_h6:+.1f}%</code> · 24h <code>{at.p_h24:+.1f}%</code>"
    )
    lp_lock_txt = f"{sec.lp_locked_pct:.0f}% Lock" if sec.lp_locked_pct is not None else "LP Tebal"

    lines = [
        f"🔵 <b>[JALUR 2 · VETERAN SWING +18%..+35%] — ${_esc(p.symbol)}</b> (Skor <b>{audit.total_score}/100</b>)",
        "⚡ <b>MODE KELUAR-MASUK CEPAT</b> — <i>Koin mapan (&gt;=7 hari), ambil cuan puluhan persen lalu keluar! BUKAN hold 10x.</i>",
        f"Setup: <b>{setup_label}</b> | Nama: {_esc(p.name)} | Usia: <b>{age_days:.0f} hari</b> | DEX: <code>{_esc(p.dex_id)}</code>",
        f"Mint: <code>{_esc(p.mint)}</code>",
        "",
        "<b>🧭 4 Pilar Veteran Swing (Mid-Cap Likuid):</b>",
        f"1. Struktur 1h: {chart_detail}",
        f"2. Arus Swing: Vol5m <b>${p.vol_m5:,.0f}</b> (${at.avg_ticket_5m_usd:.0f}/tx) · Vol1h <b>${p.vol_h1:,.0f}</b> · Buy 5m/1h/6h <code>{at.buy_ratio_5m_pct:.0f}%/{at.buy_ratio_1h_pct:.0f}%/{at.buy_ratio_6h_pct:.0f}%</code>",
        f"3. Likuiditas & Eksekusi: LP <b>${at.liq_usd:,.0f}</b> ({at.liq_mc_ratio_pct:.1f}% MC · {lp_lock_txt}) · Jupiter 0.1S <code>-{ex.round_trip_loss_01_sol_pct}%</code> · 1S <code>-{ex.depth_loss_1_sol_pct}%</code>",
        f"4. Kebersihan Veteran: <b>{sec.total_holders:,} holder</b> (${sec.mc_per_holder_usd:,.0f}/H) · Dev <code>{sec.dev_pct}%</code> · Top1 <code>{sec.top1_pct}%</code> · Top10 <code>{sec.top10_pct}%</code> · RC <code>{sec.rugcheck_score}</code>",
        "",
        f"• Spot MC: <b>${at.spot_mc:,.0f}</b> → Proyeksi: <b>${at.projected_entry_mc:,.0f}</b>",
        f"• Pergerakan 5m / 1h / 6h / 24h: <code>{at.p_m5:+.1f}%</code> / <code>{at.p_h1:+.1f}%</code> / <code>{at.p_h6:+.1f}%</code> / <code>{at.p_h24:+.1f}%</code>",
    ]
    if plan:
        lines.extend(
            [
                "",
                "<b>🎯 Rencana Swing Cepat (Wajib Disiplin Keluar-Masuk):</b>",
                f"• Batas Atas Entry: <b>&lt;= ${plan.max_safe_entry_mc:,.0f}</b> (maks +4% dari spot, jangan FOMO)",
                f"• Hard Stop-Loss Ketat: <b>${plan.hard_stop_mc:,.0f} (-{plan.hard_stop_pct:.0f}%)</b>",
                f"• TP1 (Ambil 60% &amp; Geser SL ke BE): <b>${plan.tp1_mc:,.0f} (+{plan.tp1_pct:.0f}%)</b> [R:R {plan.risk_reward_to_tp1}x]",
                f"• TP2 (Tutup Sisa 40%): <b>${plan.tp2_mc:,.0f} (+{plan.tp2_pct:.0f}%)</b> [R:R {plan.risk_reward_to_tp2}x]",
            ]
        )
    lines.append(
        f'\n<a href="https://jup.ag/swap/{SOL_MINT}-{p.mint}">Jupiter</a> · '
        f'<a href="https://dexscreener.com/solana/{p.mint}">DexScreener</a> · '
        f'<a href="https://rugcheck.xyz/tokens/{p.mint}">RugCheck</a>'
    )
    return "\n".join(lines)


def format_entry_alert(audit: AuditResult) -> str:
    """Router format alert Dual-Track: Jalur 2 (Veteran Swing) vs Jalur 1 (Early Micro-Cap)."""
    if audit.signal_track == "VETERAN_SWING" or audit.anti_top.is_veteran_swing:
        return format_veteran_swing_alert(audit)
    return format_early_microcap_alert(audit)


def format_exit_alert(pos: TrackedPosition) -> str:
    icon = "🚨" if pos.signal in {"HARD_STOP_LOSS", "LP_PULL_EMERGENCY"} else (
        "💰" if pos.signal in {"TP1_DERISK_BREAKEVEN", "TP2_TAKE_PROFIT"} else "⚠️"
    )
    return "\n".join(
        [
            f"{icon} <b>EXIT GUARD — {_esc(pos.signal)} (${_esc(pos.symbol)})</b>",
            f"Mint: <code>{_esc(pos.mint)}</code>",
            f"• Entry MC: <b>${pos.entry_mc:,.0f}</b> → Live MC: <b>${pos.live_mc:,.0f}</b> (<b>{pos.pnl_pct:+.1f}%</b>)",
            f"• Peak MC: <b>${pos.peak_mc:,.0f}</b> | LP Retention (AMM): <b>{pos.real_lp_retention_pct if pos.real_lp_retention_pct is not None else '—'}%</b>",
            f"• Detail: {_esc(pos.signal_detail)}",
            f'\n<a href="https://jup.ag/swap/{pos.mint}-{SOL_MINT}">Jual Cepat di Jupiter</a>',
        ]
    )


class TelegramNotifier:
    def __init__(self, cfg: BotConfig) -> None:
        self.token = cfg.telegram_bot_token
        self.chat_id = cfg.telegram_chat_id
        self.swing_chat_id = cfg.telegram_swing_chat_id or cfg.telegram_chat_id
        self.enabled = bool(self.token and self.chat_id)
        self.last_error: str = ""
        self._last_update_id = 0

    def target_chat_for_audit(self, audit: AuditResult) -> str:
        if audit.signal_track == "VETERAN_SWING" or audit.anti_top.is_veteran_swing:
            return self.swing_chat_id
        return self.chat_id

    async def send_message(
        self, client: httpx.AsyncClient, text: str, chat_id: Optional[str] = None
    ) -> bool:
        target = chat_id or self.chat_id
        if not self.token or not target:
            self.last_error = "TELEGRAM_BOT_TOKEN atau TELEGRAM_CHAT_ID kosong"
            return False
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {
            "chat_id": target,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        try:
            resp = await client.post(url, json=payload, timeout=6.0)
            if resp.status_code == 200:
                self.last_error = ""
                return True
            desc = ""
            try:
                body = resp.json()
                if isinstance(body, dict):
                    desc = str(body.get("description") or "")[:160]
            except Exception:
                desc = ""
            self.last_error = f"HTTP {resp.status_code}" + (f": {desc}" if desc else "")
            logger.warning("Telegram sendMessage ditolak (%s)", self.last_error)
            return False
        except Exception as exc:
            self.last_error = f"Network error: {type(exc).__name__}"
            logger.warning("Gagal kirim pesan Telegram: %s", type(exc).__name__)
            return False

    async def poll_commands(self, client: httpx.AsyncClient) -> list[tuple[str, str]]:
        """Ambil perintah baru hanya dari chat_id pemilik yang terotorisasi."""
        if not self.enabled:
            return []
        url = f"https://api.telegram.org/bot{self.token}/getUpdates"
        params = {"timeout": 1, "offset": self._last_update_id + 1}
        try:
            resp = await client.get(url, params=params, timeout=6.0)
            if resp.status_code != 200:
                return []
            data = resp.json()
            commands: list[tuple[str, str]] = []
            for upd in data.get("result") or []:
                self._last_update_id = max(self._last_update_id, int(upd.get("update_id", 0)))
                msg = upd.get("message") or {}
                cid = str((msg.get("chat") or {}).get("id") or "")
                text = str(msg.get("text") or "").strip()
                # Keamanan: hanya eksekusi perintah dari TELEGRAM_CHAT_ID pemilik
                if cid == self.chat_id and text.startswith("/"):
                    commands.append((cid, text))
            return commands
        except Exception:
            return []


async def handle_telegram_command(
    client: httpx.AsyncClient,
    cfg: BotConfig,
    store: object,
    tg: TelegramNotifier,
    chat_id: str,
    text: str,
) -> None:
    """Handler perintah bersama untuk Vercel Webhook (/api/webhook) maupun VPS (main.py)."""
    from .config import is_valid_solana_mint
    from .feeds import fetch_pair_for_mint
    from .pipeline import inspect_mint, run_scan_cycle

    parts = text.strip().split()
    if not parts:
        return
    # Strip @BotUsername suffix if invoked in group/menu (e.g. /scan@MyBot -> /scan)
    cmd = parts[0].split("@")[0].lower()

    if cmd in {"/start", "/help"}:
        await tg.send_message(
            client,
            "\n".join(
                [
                    "<b>Corridor Desk v5.6 — Dual-Track Radar (Jalur 1 Micro-Cap &amp; Jalur 2 Veteran Swing)</b>",
                    f"Runtime: <code>{'Vercel Serverless' if cfg.is_vercel else 'VPS Worker'}</code> | Storage: <code>{getattr(store, 'mode', 'sqlite')}</code>",
                    "",
                    "<b>2 Jalur Alert Berbeda:</b>",
                    "• 🟢 <b>[JALUR 1 · EARLY MICRO-CAP]</b>: $25K–$850K, Anti-ATH &amp; Anti-20m-Death",
                    "• 🔵 <b>[JALUR 2 · VETERAN SWING]</b>: $850K–$10M, usia &gt;=7 hari, keluar-masuk cepat TP +18%/+35%, SL -10%",
                    "",
                    "<b>Daftar Perintah:</b>",
                    "• <code>/scan</code> — Pindai 11 feed organik Dual-Track + Watchlist Rescan",
                    "• <code>/inspect &lt;MINT&gt;</code> — Audit penuh 5 layer (otomatis deteksi Jalur 1 vs Jalur 2)",
                    "• <code>/track &lt;MINT&gt; &lt;ENTRY_MC&gt; &lt;MODAL_USD&gt;</code> — Masukkan ke Exit Guard",
                    "• <code>/positions</code> — Lihat PnL &amp; AMM LP Retention posisi aktif",
                    "• <code>/close &lt;MINT&gt;</code> — Hapus posisi dari Exit Guard",
                    "• <code>/status</code> — Cek konfigurasi Dual-Track, Watchlist &amp; penyimpanan",
                ]
            ),
            chat_id,
        )
        return

    if cmd == "/status":
        positions = await store.list_positions()  # type: ignore[attr-defined]
        wl_mints = await store.list_watchlist_mints(limit=15)  # type: ignore[attr-defined]
        swing_route = "Chat Terpisah (TELEGRAM_SWING_CHAT_ID)" if cfg.telegram_swing_chat_id else "Chat Utama (Header 🔵 [JALUR 2 · VETERAN SWING])"
        await tg.send_message(
            client,
            "\n".join(
                [
                    "<b>Status Bot Corridor Desk v5.6 (Dual-Track)</b>",
                    f"• Lingkungan: <code>{'Vercel Serverless' if cfg.is_vercel else 'VPS'}</code> | Storage: <code>{getattr(store, 'mode', 'sqlite')}</code>",
                    f"• Posisi Aktif Exit Guard: <b>{len(positions)}</b> | Watchlist Auto-Rescan: <b>{len(wl_mints)}</b>",
                    f"• Ambang Skor Minimal: <b>{cfg.min_score}/100</b> | Delay: <b>{cfg.reaction_delay_sec}s</b>",
                    "• 🟢 <b>Jalur 1 (Micro-Cap)</b>: $25K–$850K · Anti-ATH &amp; Anti-20m-Death",
                    f"• 🔵 <b>Jalur 2 (Veteran Swing)</b>: $850K–$10M · &gt;=7 hari · SL -10% / TP +18% / +35% · Route: {swing_route}",
                ]
            ),
            chat_id,
        )
        return

    if cmd == "/scan":
        await tg.send_message(client, "Memindai 11 feed Dual-Track (Jalur 1 Micro-Cap + Jalur 2 Veteran Swing)...", chat_id)
        wl_mints = await store.list_watchlist_mints(limit=15)  # type: ignore[attr-defined]
        results, _ = await run_scan_cycle(client, cfg, watchlist_mints=wl_mints)
        ready = [r for r in results if r.verdict == "ENTRY_READY"]
        watch = [r for r in results if r.verdict == "WATCH_PULLBACK"]
        for w in watch:
            if w.security.passed:
                await store.add_watchlist_mint(w.pair.mint, ttl_minutes=120)  # type: ignore[attr-defined]
        if not ready:
            extra = ""
            if watch:
                top_w = watch[0]
                track_badge = "🔵 Jalur 2" if top_w.signal_track == "VETERAN_SWING" else "🟢 Jalur 1"
                extra = f"\nKandidat Watch teratas ({track_badge}): <b>${_esc(top_w.pair.symbol)}</b> ({top_w.total_score}/100) — {_esc(top_w.headline)}"
            await tg.send_message(
                client,
                f"Scan v5.6 Dual-Track selesai ({len(results)} diaudit | {len(watch)} Watch Pullback | {len(wl_mints)} Watchlist). Belum ada token yang lolos semua layer.{extra}",
                chat_id,
            )
        else:
            for audit in ready[:4]:
                await tg.send_message(client, format_entry_alert(audit), chat_id)
        return

    if cmd == "/inspect" and len(parts) >= 2:
        mint = parts[1].strip()
        if not is_valid_solana_mint(mint):
            await tg.send_message(client, "Format mint Base58 tidak valid (32–44 karakter).", chat_id)
            return
        audit = await inspect_mint(client, mint, cfg)
        if not audit:
            await tg.send_message(client, "Pair Solana tidak ditemukan di DexScreener.", chat_id)
            return
        track_badge = "🔵 [JALUR 2 · VETERAN SWING]" if audit.signal_track == "VETERAN_SWING" else "🟢 [JALUR 1 · EARLY MICRO-CAP]"
        summary = (
            format_entry_alert(audit)
            if audit.verdict == "ENTRY_READY"
            else f"{track_badge}\n<b>{_esc(audit.verdict)}</b> — ${_esc(audit.pair.symbol)} ({audit.total_score}/100)\n{_esc(audit.headline)}"
        )
        await tg.send_message(client, summary, chat_id)
        return

    if cmd == "/track" and len(parts) >= 4:
        mint = parts[1].strip()
        if not is_valid_solana_mint(mint):
            await tg.send_message(client, "Format mint Base58 tidak valid.", chat_id)
            return
        try:
            entry_mc = float(parts[2])
            pos_usd = float(parts[3])
        except ValueError:
            await tg.send_message(client, "Format: <code>/track &lt;MINT&gt; &lt;ENTRY_MC&gt; &lt;MODAL_USD&gt;</code>", chat_id)
            return
        pair = await fetch_pair_for_mint(client, mint, ("manual_track",))
        pos = await store.add_position(  # type: ignore[attr-defined]
            mint=mint,
            symbol=pair.symbol if pair else "TOKEN",
            name=pair.name if pair else "Tracked Token",
            entry_mc=entry_mc,
            entry_liq_usd=pair.liquidity_usd if pair else 0.0,
            position_usd=pos_usd,
            entry_price_usd=pair.price_usd if pair else 0.0,
            pair_address=pair.pair_address if pair else "",
        )
        await tg.send_message(
            client,
            f"Tersimpan di Exit Guard (<code>{getattr(store, 'mode', 'sqlite')}</code>): "
            f"<b>${_esc(pos.symbol)}</b> | Entry ${pos.entry_mc:,.0f} | SL -{pos.hard_stop_pct:.0f}%",
            chat_id,
        )
        return

    if cmd == "/positions":
        positions = await store.list_positions()  # type: ignore[attr-defined]
        if not positions:
            await tg.send_message(client, "Belum ada posisi aktif di Exit Guard.", chat_id)
        else:
            lines = ["<b>Posisi Aktif Exit Guard:</b>"]
            for p in positions:
                lp_txt = f"{p.real_lp_retention_pct:.1f}%" if p.real_lp_retention_pct is not None else "—"
                lines.append(
                    f"• <b>${_esc(p.symbol)}</b> (<code>{_esc(p.mint[:6])}…</code>): "
                    f"<b>{p.pnl_pct:+.1f}%</b> | LP Ret {lp_txt} | {_esc(p.signal)}"
                )
            await tg.send_message(client, "\n".join(lines), chat_id)
        return

    if cmd == "/close" and len(parts) >= 2:
        ok = await store.remove_position_by_mint_or_id(parts[1])  # type: ignore[attr-defined]
        await tg.send_message(
            client,
            "Posisi ditutup dari Exit Guard." if ok else "Posisi tidak ditemukan.",
            chat_id,
        )


