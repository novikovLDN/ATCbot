/**
 * Types + binders for the v3 metric endpoints. Mirrors
 * database/revenue.py, database/metrics.py and app/services/panel_stats.py;
 * the written contract is docs/dashboard/metrics.md. Money is kopecks.
 */
import { api } from "./api";

export interface Bucket {
  count: number;
  kopecks: number;
}

export interface Totals {
  since: string | null;
  until: string | null;
  net_kopecks: number;
  net_count: number;
  avg_check_kopecks: number;
  gross_kopecks: number;
  gross_count: number;
  gmv_resale_kopecks: number;
  vpn_kopecks: number;
  shop_kopecks: number;
  proxy_kopecks: number;
  game_kopecks: number;
  other_kopecks: number;
  by_class: Record<string, Bucket>;
  by_type: Record<string, Bucket>;
  by_provider: Record<string, Bucket>;
  by_product: Record<string, Bucket>;
  stars: Bucket;
  traffic_gb_sold: number;
  traffic_packs_sold: number;
  balance_funded: Bucket;
}

export interface SeriesPoint {
  date: string;
  kopecks: number;
  count: number;
}

export interface ProviderPerf {
  provider: string;
  /** Invoices opened in the window. */
  created: number;
  paid: number;
  /** Ended unpaid = expired_marked + abandoned. */
  expired: number;
  expired_marked: number;
  /** Still 'pending' although past expires_at (nothing sweeps them). */
  abandoned: number;
  /** Still inside their TTL. */
  pending: number;
  kopecks: number;
  /** paid ÷ (paid + ended unpaid). */
  success_rate: number | null;
  /** paid ÷ created. */
  conversion: number | null;
  invoiced_users: number;
  paid_users: number;
  user_conversion: number | null;
}

export interface MoneyReport {
  window_days: number;
  since: string;
  today: Totals;
  current: Totals;
  previous: Totals;
  delta_pct: { net: number | null; gross: number | null; count: number | null; avg_check: number | null; payers: number | null };
  payers: { payers: number; new: number; returning: number; payers_to_date: number };
  arppu_kopecks: number;
  arpu_kopecks: number;
  registered_users: number;
  series: SeriesPoint[];
  weekly: SeriesPoint[];
  monthly: SeriesPoint[];
  providers: ProviderPerf[];
  balance_spend: { count: number; kopecks: number; auto_renew: Bucket };
  refunds: { count: number; kopecks: number; recorded: boolean };
  referral_payouts: { count: number; kopecks: number; referrers: number };
  liabilities: {
    balance_kopecks: number;
    users_with_balance: number;
  };
  mrr: { mrr_kopecks: number; active_subscriptions: number };
}

export type SubKind = "paid" | "gift" | "granted" | "trial" | "bypass_only";

export interface ActiveSubs {
  total: number;
  with_access: number;
  paid: number;
  by_kind: Record<SubKind, number>;
  /** `granted` split by subscriptions.source (admin, referral, promo_link…). */
  granted_sources: Record<string, number>;
  by_tariff: Record<string, number>;
  auto_renew_paid: number;
  auto_renew_share: number | null;
  expiring_7d: { total: number; auto_renew_on: number; by_kind: Record<SubKind, number> };
}

export interface Renewals {
  grace_days: number;
  ending: number;
  renewed: number;
  churned: number;
  open: number;
  renewal_rate: number | null;
  churn_rate: number | null;
}

export interface FunnelStep {
  key: string;
  label: string;
  users: number | null;
  of_start: number | null;
  of_prev: number | null;
  recorded: boolean;
  note: string | null;
}

export interface Pipeline {
  days: number;
  expiring: number;
  by_kind: Record<"paid" | "gift" | "granted" | "trial", number>;
  auto_renew: number;
  manual: number;
  expected_list_kopecks: number;
  covered: number;
  covered_kopecks: number;
  not_covered: number;
  shortfall_kopecks: number;
  unpriced: number;
  by_day: { date: string; total: number; auto_renew: number }[];
}

export interface Motion {
  new: { count: number; kopecks: number; users: number };
  renewal: Bucket;
  auto_renew_balance: Bucket;
  new_share: number | null;
}

export interface SubscribersReport {
  window_days: number;
  active: ActiveSubs;
  renewals: Renewals;
  renewals_prev: Renewals;
  trial: {
    trials: number;
    matured_7d: number;
    matured_30d: number;
    paid_7d: number;
    paid_7d_matured: number;
    paid_30d: number;
    paid_30d_matured: number;
    paid_any: number;
    rate_7d: number | null;
    rate_30d: number | null;
    rate_any: number | null;
  };
  funnel: { steps: FunnelStep[] };
  motion: Motion;
  growth: { new_paying: number; churned: number; net: number; undecided: number };
  pipeline: Pipeline;
  auto_renew: { balance: Bucket; platega_recurring: Record<string, number> };
}

// ── v4: overview, health, engagement ─────────────────────────────────

export type SectionStatus = "ok" | "warning" | "critical" | "unknown";
export type SystemStatus = "ok" | "degraded" | "down" | "unknown";

export interface Reason {
  level: "down" | "degraded" | "critical" | "warning" | "info";
  key: string;
  text: string;
}

export interface Kpi {
  key: string;
  label: string;
  unit: "kopecks" | "count";
  value: number;
  prev: number;
  delta_pct: number | null;
}

export interface WorkerState {
  name: string;
  label: string;
  state: "ok" | "failing" | "paused" | "stale" | "starting";
  interval_s: number;
  registered_at: string;
  last_ok_at: string | null;
  last_ok_age_s: number | null;
  last_fail_at: string | null;
  last_error: string | null;
  ok_count: number;
  fail_count: number;
  skip_count: number;
  consecutive_fails: number;
}

export interface SystemHealth {
  checked_at: string;
  db: {
    ready: boolean;
    ok: boolean;
    error?: string;
    latency_ms?: number;
    pool?: { size: number; idle: number; in_use: number; min: number; max: number };
  };
  remnawave: { enabled: boolean; ok?: boolean; latency_ms?: number; error?: string };
  redis: { configured: boolean; ok?: boolean; latency_ms?: number; error?: string };
  webhook: {
    ok: boolean;
    error?: string;
    latency_ms?: number;
    url_set?: boolean;
    url_host?: string | null;
    pending_update_count?: number;
    /** v5: queue size at the previous check; null right after start. */
    pending_prev?: number | null;
    pending_growing?: boolean;
    last_error_at?: string | null;
    last_error_age_s?: number | null;
    last_error_message?: string | null;
    max_connections?: number | null;
  };
  workers: WorkerState[];
  flags: { background_workers?: boolean; auto_renewal?: boolean };
  alerts: { total: number; by_category: Record<string, number>; window_s: number; covered_s: number };
  uptime: { started_at: string | null; uptime_s: number | null };
  version: { git_sha: string | null; git_branch: string | null; environment: string | null; dashboard_built_at: string | null };
  overall: { status: SystemStatus; reasons: Reason[] };
}

export interface ProviderHealth {
  provider: string;
  enabled: boolean | null;
  last_paid_at: string | null;
  paid_7d: number;
  state: "ok" | "silent" | "rare" | "disabled";
  age_h: number | null;
  threshold_h: number | null;
}

export interface Verdict {
  status: SectionStatus;
  reasons: Reason[];
}

export interface PaymentsHealth {
  now: string;
  providers_24h: ProviderPerf[];
  providers_7d: ProviderPerf[];
  stuck: { provider: string; count: number; still_valid: number; oldest_at: string | null; created_24h: number }[];
  time_to_pay: { provider: string | null; count: number; p50_s: number | null; p90_s: number | null }[];
  errors_24h: {
    total: number;
    by_stage: { stage: string; count: number }[];
    by_provider: { provider: string; count: number }[];
    matrix: { stage: string; provider: string; count: number; last_at: string | null }[];
    telegram_money: number;
  };
  last_paid: Record<string, string>;
  paid_7d: Record<string, number>;
  providers: ProviderHealth[];
  verdict: Verdict;
}

export interface DeadJob {
  id: number;
  telegram_id: number;
  source: string;
  tariff_key: string;
  attempts: number;
  last_error: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface DeliveryHealth {
  now: string;
  queue: {
    available: boolean;
    pending_new?: number;
    retrying?: number;
    running?: number;
    dead?: number;
    shadow?: number;
    done_24h?: number;
    dead_24h?: number;
    max_attempts_open?: number;
    oldest_open_at?: string | null;
    oldest_open_age_s?: number | null;
  };
  dead_jobs: DeadJob[];
  activations: { pending: number; failed: number; max_attempts: number };
  errors_24h: Partial<Record<"provisioning_dead" | "provisioning_retry" | "mismatch" | "renewal_sync" | "bypass_topup", number>>;
  verdict: Verdict;
}

export interface Engagement {
  window_days: number;
  reminders: { users: number };
  automations: {
    available: boolean;
    sent: number;
    failed: number;
    blocked: number;
    skipped: number;
    by_key: { key: string; title: string; category: string; enabled: boolean; sent: number; failed: number; blocked: number; skipped: number }[];
  };
  broadcasts: {
    count: number;
    delivered: number;
    failed: number;
    delivery_rate: number | null;
    recent: { id: number; title: string | null; segment: string | null; created_at: string | null; delivered: number; failed: number; delivery_rate: number | null }[];
  };
  reach: { users: number; unreachable: number; unreachable_share: number | null };
  referrals: { invited: number; converted: number; cashback: { count: number; kopecks: number; referrers: number } };
}

export interface CohortReport {
  months: number;
  avg_ltv_kopecks: number;
  cohorts: { cohort: string; payers: number; revenue_kopecks: number; ltv_kopecks: (number | null)[] }[];
}

export interface Alert {
  level: "critical" | "warning" | "info";
  key: string;
  title: string;
  detail: string;
  link: string;
}

export interface LineValue {
  value: number;
  prev: number;
  delta_pct: number | null;
}

export interface OverviewReport {
  window_days: number;
  since: string;
  prev_since: string;
  prev_until: string;
  kpis: Kpi[];
  lines: Record<"shop" | "proxy" | "game" | "gross", LineValue>;
  series: SeriesPoint[];
  subscribers: {
    with_access: number;
    paid: number;
    by_kind: Record<SubKind, number>;
    granted_sources: Record<string, number>;
    expiring_7d: ActiveSubs["expiring_7d"];
    auto_renew_share: number | null;
    renewal_rate: number | null;
    churn_rate: number | null;
    pipeline: Pipeline | null;
  };
  health: { status: SystemStatus; reasons: Reason[]; checked_at: string | null };
  payments: {
    status: SectionStatus;
    reasons: Reason[];
    errors_24h: number;
    invoices_24h: number;
    paid_24h: number;
    conversion_24h: number | null;
    silent: string[];
  };
  delivery: {
    status: SectionStatus;
    reasons: Reason[];
    queue_open: number | null;
    dead: number | null;
    activations_pending: number;
  };
  panel: {
    checked: boolean;
    available: boolean;
    online_now: number | null;
    nodes_online: number | null;
    nodes_total: number | null;
    /** v5: disabled nodes are not problems; offline = offline + connecting. */
    nodes_enabled?: number | null;
    nodes_offline?: number | null;
    nodes_disabled?: number | null;
  };
  alerts: Alert[];
}

export interface PaymentErrorRow {
  id: number;
  telegram_id: number | null;
  username?: string | null;
  purchase_id: string | null;
  payment_provider: string | null;
  amount_rubles: number | null;
  stage: string;
  error_code: string | null;
  error_message: string | null;
  created_at: string | null;
}

export interface OperationsReport {
  hours: number;
  errors: {
    total: number;
    by_stage: { stage: string; count: number }[];
    by_provider: { provider: string; count: number }[];
  };
  errors_recent: PaymentErrorRow[];
  errors_daily: { date: string; stage: string; provider: string; count: number }[];
  provisioning: {
    available: boolean;
    by_status?: Record<string, number>;
    open?: number;
    dead?: number;
    oldest_open_at?: string | null;
  };
  queues: { pending_invoices: number; stuck_activations: number };
}

export interface PanelSystem {
  available: boolean;
  users_total?: number;
  status_counts?: Record<"ACTIVE" | "DISABLED" | "LIMITED" | "EXPIRED", number>;
  online_now?: number;
  online_day?: number;
  online_week?: number;
  never_online?: number;
  nodes_online?: number;
  traffic_lifetime_bytes?: number;
  uptime_seconds?: number;
}

export interface BandwidthStat {
  current: string | null;
  previous: string | null;
  current_bytes: number | null;
  previous_bytes: number | null;
}

export interface PanelOverview {
  system: PanelSystem;
  bandwidth: { available: boolean } & Partial<Record<"day" | "week" | "days30" | "month" | "year", BandwidthStat>>;
  hwid: {
    available: boolean;
    unique_devices?: number;
    devices?: number;
    avg_per_user?: number;
    by_platform?: { platform: string; count: number }[];
  };
  db: { premium_active: number; bypass_entities: number };
  discrepancy: {
    available: boolean;
    panel_active?: number;
    panel_active_or_limited?: number;
    db_expected?: number;
    db_premium_active?: number;
    db_bypass_entities?: number;
    difference?: number;
  };
  cache_ttl_seconds: number;
}

export interface PanelNode {
  uuid: string;
  name: string;
  country: string | null;
  state: "online" | "offline" | "connecting" | "disabled";
  status_message: string | null;
  users_online: number;
  traffic_used_bytes: number | null;
  traffic_limit_bytes: number | null;
  xray_uptime_seconds: number;
  provider: string | null;
}

export interface PanelNodes {
  available: boolean;
  nodes: PanelNode[];
  total?: number;
  /** v5: switched off in the panel on purpose. */
  disabled?: number;
  enabled?: number;
  online?: number;
  offline?: number;
  users_online?: number;
}

export interface PanelBandwidth {
  available: boolean;
  categories: string[];
  total?: number[];
  series: { name: string; country: string | null; total_bytes: number; data: number[] }[];
}

export const metricsApi = {
  overview: (days: number) => api.get<OverviewReport>(`/metrics/overview?days=${days}`),
  money: (days: number) => api.get<MoneyReport>(`/metrics/money?days=${days}`),
  subscribers: (days: number, graceDays = 3) =>
    api.get<SubscribersReport>(`/metrics/subscribers?days=${days}&grace_days=${graceDays}`),
  cohorts: (months = 12) => api.get<CohortReport>(`/metrics/cohorts?months=${months}`),
  operations: (hours: number) => api.get<OperationsReport>(`/metrics/operations?hours=${hours}`),
  panelOverview: () => api.get<PanelOverview>("/panel/overview"),
  panelNodes: () => api.get<PanelNodes>("/panel/nodes"),
  panelBandwidth: (days: number) => api.get<PanelBandwidth>(`/panel/bandwidth?days=${days}`),
  health: () => api.get<SystemHealth>("/metrics/health"),
  paymentsHealth: () => api.get<PaymentsHealth>("/metrics/payments-health"),
  delivery: () => api.get<DeliveryHealth>("/metrics/delivery"),
  engagement: (days: number) => api.get<Engagement>(`/metrics/engagement?days=${days}`),
};

/** Labels for payment_errors.stage — the codes the payment code writes. */
export const STAGE_LABEL: Record<string, string> = {
  amount_mismatch: "Сумма не совпала",
  webhook_provider_mismatch: "Провайдер не совпал",
  webhook_purchase_not_found: "Вебхук: покупка не найдена",
  confirm_purchase_not_found: "Подтверждение: покупка не найдена",
  telegram_purchase_not_found: "Telegram: покупка не найдена",
  telegram_payment_service_error: "Telegram: сбой сервиса оплаты",
  telegram_payment_rejected: "Telegram: оплата отклонена",
  telegram_gift_failed: "Telegram: подарок не выдан",
  telegram_traffic_pack_failed: "Telegram: пакет трафика не выдан",
  telegram_traffic_pack_delivery_failed: "Telegram: пакет не доставлен",
  shop_admin_notify_failed: "Магазин: админ не уведомлён",
  provisioning: "Выдача доступа (очередь)",
  renewal_sync: "Синхронизация продления",
  bypass_topup: "Пополнение ГБ обхода",
  platega_recurring_disabled: "Platega: рекуррент выключен",
  signature_invalid: "Подпись вебхука неверна",
};

export const WORKER_STATE_LABEL: Record<WorkerState["state"], string> = {
  ok: "Работает",
  failing: "Падает",
  paused: "Пропускает циклы",
  stale: "Не отчитывается",
  starting: "Запускается",
};

/** Dashboard labels for backend keys — one vocabulary on every screen. */
export const PROVIDER_LABEL: Record<string, string> = {
  platega: "Platega",
  wata: "WATA",
  cryptobot: "CryptoBot",
  telegram_payment: "Telegram",
  telegram_stars: "Telegram Stars",
  lava: "Lava",
  balance: "С баланса",
  unknown: "Без провайдера",
};

export const PRODUCT_LABEL: Record<string, string> = {
  basic: "Basic",
  plus: "Plus",
  combo_basic: "Combo Basic",
  combo_plus: "Combo Plus",
  gift: "Подарочные подписки",
  traffic_pack: "Пакеты трафика",
  topup: "Пополнения баланса",
  shop_telegram_premium: "Магазин: Telegram Premium",
  shop_telegram_stars: "Магазин: Telegram Stars",
  shop_steam: "Магазин: Steam",
  shop_apple_id: "Магазин: Apple ID",
  shop_spotify: "Магазин: Spotify",
  proxy: "Прокси",
  game: "Игра",
  subscription_other: "Подписки (другое)",
  other: "Прочее",
};

export const CLASS_LABEL: Record<string, string> = {
  subscription: "Подписки",
  traffic: "Трафик",
  topup: "Пополнения",
  shop: "Магазин",
  proxy: "Прокси",
  game: "Игра",
  other: "Прочее",
};

export const KIND_LABEL: Record<SubKind, string> = {
  paid: "Платные",
  gift: "Подарок",
  granted: "Выданы вручную",
  trial: "Пробный период",
  bypass_only: "Только обход",
};
