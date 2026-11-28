"""Dataclass terstruktur untuk seluruh 5 layer mesin audit & Exit Guard."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

LayerName = Literal["SECURITY", "ANTI_TOP", "STRUCTURE", "EXECUTION"]
CheckSeverity = Literal["PASS", "WAIT_DIP", "VETO"]
Verdict = Literal["ENTRY_READY", "WATCH_PULLBACK", "REJECTED"]
SignalTrack = Literal["EARLY_MICROCAP", "VETERAN_SWING"]
StructureRegime = Literal[
    "CONTINUATION_BREAKOUT",
    "PULLBACK_RECLAIM",
    "BULLISH_CONSOLIDATION_BREAKOUT",
    "BULLISH_CONSOLIDATION_WAITING",
    "BEARISH_DISTRIBUTION",
    "EXHAUSTION_TOP",
    "BREAKDOWN_DUMP",
    "UNVERIFIED",
]
ExitSignalCode = Literal[
    "HOLDING_SAFE",
    "TP1_DERISK_BREAKEVEN",
    "TP2_TAKE_PROFIT",
    "TRAILING_PEAK_EXIT",
    "WHALE_DUMP_DIVERGENCE",
    "LP_PULL_EMERGENCY",
    "HARD_STOP_LOSS",
]


@dataclass(frozen=True)
class GateCheck:
    id: str
    layer: LayerName
    label: str
    passed: bool
    severity: CheckSeverity
    actual: str
    threshold: str
    detail: str


@dataclass(frozen=True)
class PairSnapshot:
    mint: str
    pair_address: str
    dex_id: str
    symbol: str
    name: str
    quote_mint: str
    quote_symbol: str
    price_usd: float
    market_cap: float
    liquidity_usd: float
    vol_m5: float
    vol_h1: float
    vol_h24: float
    buys_m5: int
    sells_m5: int
    buys_h1: int
    sells_h1: int
    buys_h24: int
    sells_h24: int
    price_change_m5: float
    price_change_h1: float
    price_change_h24: float
    pair_created_at_ms: int
    feed_sources: tuple[str, ...] = ()
    url: str = ""
    vol_h6: float = 0.0
    price_change_h6: float = 0.0
    buys_h6: int = 0
    sells_h6: int = 0
    pool_count: int = 1
    is_boosted: bool = False
    has_socials: bool = False
    social_count: int = 0
    has_website: bool = False
    has_image: bool = False


@dataclass(frozen=True)
class SecurityResult:
    passed: bool
    checks: tuple[GateCheck, ...]
    hard_quote_symbol: str
    rugcheck_score: Optional[int]
    dev_pct: Optional[float]
    top1_pct: Optional[float]
    top10_pct: Optional[float]
    lp_locked_pct: Optional[float]
    lp_locked_usd: Optional[float]
    is_meteora_nft_lp: bool
    pareto_status: str
    total_holders: int = 0
    holders_per_min: float = 0.0
    active_markets_count: int = 1
    clean_viral_pareto: bool = False
    mc_per_holder_usd: float = 0.0


@dataclass(frozen=True)
class AntiTopResult:
    passed: bool
    wait_dip_only: bool
    checks: tuple[GateCheck, ...]
    corridor_id: Optional[str]
    corridor_label: str
    spot_mc: float
    projected_entry_mc: float
    projected_delay_drift_pct: float
    net_headroom_pct: float
    age_minutes: float
    liq_usd: float
    liq_mc_ratio_pct: float
    p_m5: float
    p_h1: float
    tx_24h: int
    effective_min_tx_24h: int
    avg_ticket_1h_usd: float
    buy_ratio_5m_pct: float
    buy_ratio_1h_pct: float
    p_h6: float = 0.0
    p_h24: float = 0.0
    is_sleeper_revival: bool = False
    vol_wake_5m_ratio: float = 0.0
    vol_h1_share_24h_pct: float = 0.0
    avg_ticket_5m_usd: float = 0.0
    turnover_h1_ratio: float = 0.0
    whale_absorption: bool = False
    vol_m5_accel_ratio: float = 1.0
    buy_ratio_6h_pct: float = 50.0
    is_viral_velocity: bool = False
    is_pullback_contraction_ok: bool = False
    is_corridor_d_viral_base: bool = False
    is_hyper_viral_fasttrack: bool = False
    is_steady_organic_climber: bool = False
    has_community_presence: bool = False
    is_veteran_swing: bool = False
    signal_track: SignalTrack = "EARLY_MICROCAP"


@dataclass(frozen=True)
class StructureResult:
    passed: bool
    wait_dip_only: bool
    regime: StructureRegime
    ohlcv_verified: bool
    checks: tuple[GateCheck, ...]
    upper_wick_pct: float = 0.0
    drawdown_from_peak_pct: float = 0.0
    vol_expansion_ratio: float = 1.0
    base_range_pct: float = 0.0
    range_position_pct: float = 50.0
    green_vol_share_pct: float = 50.0
    higher_low_intact: bool = True
    vwap_deviation_pct: float = 0.0
    green_vol_last4_pct: float = 50.0
    summary: str = "Struktur chart terverifikasi."


@dataclass(frozen=True)
class ExecutionResult:
    passed: bool
    status: str
    checks: tuple[GateCheck, ...]
    round_trip_loss_01_sol_pct: Optional[float]
    depth_loss_1_sol_pct: Optional[float]
    buy_impact_01_sol_pct: Optional[float]
    sell_impact_01_sol_pct: Optional[float]


@dataclass(frozen=True)
class PillarScore:
    name: str
    score: int
    max_score: int
    summary: str


@dataclass(frozen=True)
class TradePlan:
    corridor_label: str
    max_safe_entry_mc: float
    hard_stop_mc: float
    hard_stop_pct: float
    tp1_mc: float
    tp1_pct: float
    tp2_mc: float
    tp2_pct: float
    risk_reward_to_tp1: float
    risk_reward_to_tp2: float


@dataclass(frozen=True)
class AuditResult:
    pair: PairSnapshot
    security: SecurityResult
    anti_top: AntiTopResult
    execution: ExecutionResult
    pillars: tuple[PillarScore, ...]
    total_score: int
    verdict: Verdict
    headline: str
    trade_plan: Optional[TradePlan]
    audited_at_ms: int
    structure: Optional[StructureResult] = None
    signal_track: SignalTrack = "EARLY_MICROCAP"


@dataclass
class TrackedPosition:
    id: str
    mint: str
    pair_address: str
    symbol: str
    name: str
    entry_mc: float
    entry_price_usd: float
    entry_liq_usd: float
    position_usd: float
    hard_stop_pct: float
    tp1_pct: float
    tp2_pct: float
    peak_mc: float
    tp1_hit: bool = False
    tp2_hit: bool = False
    live_mc: float = 0.0
    live_liq_usd: float = 0.0
    pnl_pct: float = 0.0
    real_lp_retention_pct: Optional[float] = None
    signal: ExitSignalCode = "HOLDING_SAFE"
    signal_detail: str = "Menunggu pengecekan live."
    created_at_ms: int = 0
    updated_at_ms: int = 0
    notes: str = ""
    extra: dict[str, str] = field(default_factory=dict)
