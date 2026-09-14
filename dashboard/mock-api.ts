/**
 * Dev-only mock API.
 *
 * Lets the dashboard be opened and reviewed without a running bot, a
 * database or a login. Enabled ONLY when `VITE_MOCK_API=1` is set and
 * only in `vite serve` — it is a dev-server plugin, so nothing here can
 * reach a production build. Real auth is untouched.
 *
 * Unknown endpoints return an empty 200 rather than a 500: a screen that
 * renders with no rows is reviewable, a screen that throws is not.
 */
import type { Plugin } from "vite";

const DAY = 86_400_000;

/** Deterministic pseudo-random so the charts don't reshuffle on reload. */
function seeded(seed: number) {
  let s = seed;
  return () => {
    s = (s * 1664525 + 1013904223) % 4294967296;
    return s / 4294967296;
  };
}

function buildSeries(days: number) {
  const rnd = seeded(42);
  const out: { date: string; kopecks: number; count: number }[] = [];
  const now = Date.now();
  for (let i = days - 1; i >= 0; i--) {
    const d = new Date(now - i * DAY);
    // Weekends run lighter — makes the "which days sell better" reading
    // visible rather than a flat noise band.
    const weekend = [0, 6].includes(d.getUTCDay());
    const base = weekend ? 28_000 : 46_000;
    const kopecks = Math.round((base + rnd() * base * 0.7) * 100);
    out.push({
      date: d.toISOString().slice(0, 10),
      kopecks,
      count: Math.max(1, Math.round(kopecks / 100 / 420)),
    });
  }
  return out;
}

function totals(series: { kopecks: number; count: number }[]) {
  const net_kopecks = series.reduce((s, p) => s + p.kopecks, 0);
  const net_count = series.reduce((s, p) => s + p.count, 0);
  const sub = Math.round(net_kopecks * 0.62);
  const traffic = Math.round(net_kopecks * 0.11);
  const topup = net_kopecks - sub - traffic;
  return {
    since: null,
    until: null,
    net_kopecks,
    net_count,
    avg_check_kopecks: net_count ? Math.round(net_kopecks / net_count) : 0,
    by_class: {
      subscription: { count: Math.round(net_count * 0.62), kopecks: sub },
      traffic: { count: Math.round(net_count * 0.18), kopecks: traffic },
      topup: { count: Math.round(net_count * 0.2), kopecks: topup },
      shop: { count: 30, kopecks: 38_00_000 },
      proxy: { count: 4, kopecks: 3_20_000 },
      game: { count: 12, kopecks: 3_40_000 },
    },
    by_type: {},
    gmv_resale_kopecks: 41_20_000,
  };
}

function paymentsKpi(days: number) {
  const series = buildSeries(days);
  const current = totals(series);
  const prevSeries = buildSeries(days).map((p) => ({
    ...p,
    kopecks: Math.round(p.kopecks * 0.88),
    count: Math.max(1, Math.round(p.count * 0.9)),
  }));
  const previous = totals(prevSeries);
  const today = totals(series.slice(-1));

  const pct = (a: number, b: number) =>
    b ? Math.round(((a - b) / b) * 1000) / 10 : null;

  return {
    window_days: days,
    today,
    current,
    previous,
    delta_pct: {
      net: pct(current.net_kopecks, previous.net_kopecks),
      count: pct(current.net_count, previous.net_count),
      avg_check: pct(current.avg_check_kopecks, previous.avg_check_kopecks),
    },
    series,
    providers: [
      { provider: "platega", paid: 412, expired: 63, pending: 7, kopecks: 18_74_00_00, success_rate: 86.7 },
      { provider: "cryptobot", paid: 96, expired: 12, pending: 2, kopecks: 5_12_00_00, success_rate: 88.9 },
      { provider: "telegram_stars", paid: 71, expired: 4, pending: 0, kopecks: 2_86_00_00, success_rate: 94.7 },
      { provider: "wata", paid: 38, expired: 51, pending: 3, kopecks: 1_44_00_00, success_rate: 42.7 },
    ],
    mrr: { mrr_kopecks: 21_43_00_00, active_subscriptions: 734 },
  };
}

/**
 * A synthetic user population.
 *
 * Deliberately not uniform: the listing exists to make outliers findable,
 * so the fixture has to contain some. Every twelfth user has blocked the
 * bot, every seventh was granted access by hand rather than paying, and a
 * slice have paid subscriptions that never passed a byte of traffic —
 * which is the churn signal the screen is meant to surface.
 */
const USERNAMES = [
  "ivan", "maria", "alex", "dmitry", "olga", "sergey", "anna", "pavel",
  "elena", "nikita", "yulia", "artem", "ksenia", "roman", "vera", "igor",
];

function buildUsers(n = 240) {
  const rnd = seeded(7);
  const now = Date.now();
  return Array.from({ length: n }, (_, i) => {
    const hasSub = rnd() > 0.32;
    const manual = i % 7 === 0;
    const createdDaysAgo = Math.floor(rnd() * 400);
    // Spread expiry across the past and the near future so the "lapsing
    // this week" preset actually has something to find.
    const expiresInDays = Math.floor(rnd() * 60) - 12;
    return {
      telegram_id: 100_000 + i * 137,
      username: rnd() > 0.12 ? `${USERNAMES[i % USERNAMES.length]}${i}` : null,
      language: rnd() > 0.8 ? "en" : "ru",
      created_at: new Date(now - createdDaysAgo * DAY).toISOString(),
      last_seen_at:
        rnd() > 0.25 ? new Date(now - Math.floor(rnd() * 30) * DAY).toISOString() : null,
      is_reachable: i % 12 !== 0,
      has_active_sub: hasSub && expiresInDays > 0,
      subscription_type: hasSub ? (rnd() > 0.6 ? "plus" : "basic") : null,
      expires_at: hasSub ? new Date(now + expiresInDays * DAY).toISOString() : null,
      source: hasSub ? (manual ? "admin" : rnd() > 0.85 ? "trial" : "payment") : null,
      balance_kopecks: rnd() > 0.6 ? Math.floor(rnd() * 300_000) : 0,
      referral_level: i % 23 === 0 ? "vip" : "base",
    };
  });
}

const MOCK_USERS = buildUsers();

function mockUserDetail(tg: number) {
  const u = MOCK_USERS.find((x) => x.telegram_id === tg) ?? MOCK_USERS[0];
  const active = u.has_active_sub;
  // One in five carries a failed activation — the "paid and got nothing"
  // case the tech card is built to make visible.
  const failed = tg % 5 === 0;
  return {
    user: {
      telegram_id: u.telegram_id,
      username: u.username,
      language: u.language,
      created_at: u.created_at,
      last_seen_at: u.last_seen_at,
      is_reachable: u.is_reachable,
      referral_code: `REF${u.telegram_id.toString(36).toUpperCase()}`,
      referral_level: u.referral_level,
      referrer_id: tg % 3 === 0 ? MOCK_USERS[2].telegram_id : null,
      balance: u.balance_kopecks,
      trial_used_at: new Date(Date.parse(u.created_at) + DAY).toISOString(),
      trial_expires_at: new Date(Date.parse(u.created_at) + 4 * DAY).toISOString(),
      cashback_fixed_percent: tg % 11 === 0 ? 15 : null,
      cashback_floor_percent: 5,
      farm_plot_count: 3,
    },
    balance_rubles: Math.round(u.balance_kopecks / 100),
    subscription: u.subscription_type
      ? {
          telegram_id: u.telegram_id,
          expires_at: u.expires_at,
          subscription_type: u.subscription_type,
          status: active ? "active" : "expired",
          source: u.source,
          vpn_key: `vless://${u.telegram_id}-key@edge.example:443?type=tcp#atlas`,
          vpn_key_plus: `vless://${u.telegram_id}-plus@edge.example:443?type=tcp#atlas-plus`,
          remnawave_premium_uuid: "6f1c2e10-77aa-4b3c-9c11-8c9f0a1b2c3d",
          remnawave_premium_sub_url: `https://rmnw.example/sub/${u.telegram_id}`,
          auto_renew: tg % 4 !== 0,
          last_auto_renewal_at: active ? new Date(Date.now() - 12 * DAY).toISOString() : null,
          activation_status: failed ? "failed" : "active",
          activation_attempts: failed ? 3 : 1,
          last_activation_error: failed ? "remnawave: 502 bad gateway" : null,
          activated_at: failed ? null : new Date(Date.parse(u.created_at) + DAY).toISOString(),
          // A paid-but-never-connected slice.
          last_bytes: tg % 6 === 0 ? 0 : Math.floor(Math.random() * 8e10),
          first_traffic_at: tg % 6 === 0 ? null : new Date(Date.now() - 20 * DAY).toISOString(),
          admin_grant_days: u.source === "admin" ? 30 : null,
          is_combo: tg % 9 === 0,
          is_bypass_only: false,
          country: "nl",
        }
      : null,
    subscription_is_active: active,
    trial: {
      trial_used_at: new Date(Date.parse(u.created_at) + DAY).toISOString(),
      trial_expires_at: new Date(Date.parse(u.created_at) + 4 * DAY).toISOString(),
    },
    discount:
      tg % 8 === 0
        ? {
            discount_percent: 20,
            expires_at: new Date(Date.now() + 3 * DAY).toISOString(),
            created_by: 1,
            created_at: new Date(Date.now() - DAY).toISOString(),
          }
        : null,
    traffic_discount: null,
    cashback_fixed_percent: tg % 11 === 0 ? 15 : null,
    cashback_effective_percent: tg % 11 === 0 ? 15 : 7,
    referral_link: `https://t.me/atlassecure_bot?start=ref_${u.telegram_id.toString(36)}`,
  };
}

// ── v3 fixtures (shapes mirror database/revenue.py & friends) ─────────

function fullTotals(series: { kopecks: number; count: number }[]) {
  const t = totals(series);
  const net = t.net_kopecks;
  return {
    ...t,
    gross_kopecks: net + 41_20_000 + 3_40_000,
    gross_count: t.net_count + 46,
    vpn_kopecks: net,
    shop_kopecks: 38_00_000,
    proxy_kopecks: 3_20_000,
    game_kopecks: 3_40_000,
    other_kopecks: 0,
    by_provider: {
      platega: { count: 412, kopecks: Math.round(net * 0.52) },
      wata: { count: 150, kopecks: Math.round(net * 0.21) },
      telegram_payment: { count: 88, kopecks: Math.round(net * 0.14) },
      cryptobot: { count: 41, kopecks: Math.round(net * 0.08) },
      telegram_stars: { count: 30, kopecks: Math.round(net * 0.05) },
    },
    by_product: {
      basic: { count: 300, kopecks: Math.round(net * 0.3) },
      plus: { count: 120, kopecks: Math.round(net * 0.18) },
      combo_basic: { count: 60, kopecks: Math.round(net * 0.09) },
      combo_plus: { count: 25, kopecks: Math.round(net * 0.05) },
      traffic_pack: { count: 90, kopecks: Math.round(net * 0.11) },
      topup: { count: 140, kopecks: Math.round(net * 0.27) },
      shop_steam: { count: 9, kopecks: 26_00_000 },
      shop_telegram_premium: { count: 14, kopecks: 12_00_000 },
      game: { count: 12, kopecks: 3_40_000 },
    },
    stars: { count: 30, kopecks: Math.round(net * 0.05) },
    traffic_gb_sold: 1_840,
    traffic_packs_sold: 90,
    balance_funded: { count: 0, kopecks: 0 },
  };
}

function mockMoney(days: number) {
  const kpi = paymentsKpi(days);
  const series = kpi.series;
  const weekly = Array.from({ length: 12 }, (_, i) => ({
    date: new Date(Date.now() - (11 - i) * 7 * DAY).toISOString().slice(0, 10),
    kopecks: 2_60_00_00 + Math.round(Math.sin(i) * 40_00_00) + i * 9_00_00,
    count: 520 + i * 7,
  }));
  const monthly = Array.from({ length: 12 }, (_, i) => ({
    date: new Date(Date.UTC(2025, 9 + i, 1)).toISOString().slice(0, 10),
    kopecks: 9_00_00_00 + i * 60_00_00 + Math.round(Math.cos(i) * 80_00_00),
    count: 2100 + i * 40,
  }));
  return {
    window_days: days,
    since: new Date(Date.now() - days * DAY).toISOString(),
    today: fullTotals(series.slice(-1)),
    current: fullTotals(series),
    previous: fullTotals(series.map((p) => ({ ...p, kopecks: Math.round(p.kopecks * 0.9) }))),
    delta_pct: { ...kpi.delta_pct, gross: 8.1, payers: 5.4 },
    payers: { payers: 612, new: 188, returning: 424, payers_to_date: 5_310 },
    arppu_kopecks: 1_48_000,
    arpu_kopecks: 7_300,
    registered_users: 12_480,
    series,
    weekly,
    monthly,
    providers: mockProviders(),
    balance_spend: { count: 214, kopecks: 71_20_000, auto_renew: { count: 96, kopecks: 32_40_000 } },
    refunds: { count: 0, kopecks: 0, recorded: true },
    referral_payouts: { count: 57, kopecks: 8_90_000, referrers: 31 },
    liabilities: { balance_kopecks: 3_84_00_00, users_with_balance: 1_204 },
    mrr: kpi.mrr,
  };
}

function mockProviders(scale = 1) {
  const row = (provider: string, created: number, paid: number, marked: number, abandoned: number, pending: number, kopecks: number) => ({
    provider,
    created: Math.round(created * scale),
    paid: Math.round(paid * scale),
    expired: Math.round((marked + abandoned) * scale),
    expired_marked: Math.round(marked * scale),
    abandoned: Math.round(abandoned * scale),
    pending,
    kopecks: Math.round(kopecks * scale),
    success_rate: Math.round((paid / (paid + marked + abandoned)) * 1000) / 10,
    conversion: Math.round((paid / created) * 1000) / 10,
    invoiced_users: Math.round(created * 0.9 * scale),
    paid_users: Math.round(paid * 0.95 * scale),
    user_conversion: Math.round(((paid * 0.95) / (created * 0.9)) * 1000) / 10,
  });
  return [
    row("platega", 530, 412, 41, 70, 7, 18_74_00_00),
    row("wata", 250, 150, 38, 58, 4, 7_10_00_00),
    row("telegram_payment", 95, 88, 2, 5, 0, 3_40_00_00),
    row("cryptobot", 80, 41, 12, 25, 2, 1_52_00_00),
    row("telegram_stars", 34, 30, 1, 3, 0, 1_12_00_00),
    row("unknown", 26, 23, 1, 2, 0, 38_00_000),
  ];
}

function mockPipeline() {
  const totals = [18, 31, 27, 40, 22, 35, 29, 12];
  const autos = [6, 11, 9, 14, 8, 12, 10, 4];
  const by_day = totals.map((total, i) => ({
    date: new Date(Date.now() + i * DAY).toISOString().slice(0, 10),
    total,
    auto_renew: autos[i],
  }));
  const auto = autos.reduce((s, v) => s + v, 0);
  return {
    days: 7,
    expiring: totals.reduce((s, v) => s + v, 0),
    by_kind: { paid: 160, gift: 4, granted: 10, trial: 40 },
    auto_renew: auto,
    manual: totals.reduce((s, v) => s + v, 0) - auto,
    expected_list_kopecks: 51 * 19_900 + 23 * 34_900,
    covered: 51,
    covered_kopecks: 51 * 19_900,
    not_covered: 23,
    shortfall_kopecks: 23 * 21_400,
    unpriced: 0,
    by_day,
  };
}

function mockOverview(days: number) {
  const m = mockMoney(days);
  const series = buildSeries(Math.max(days, 14));
  const net = days === 1 ? m.today.net_kopecks : m.current.net_kopecks;
  const count = days === 1 ? m.today.net_count : m.current.net_count;
  const f = days === 1 ? 1 / 30 : days / 30;
  const kp = (key: string, label: string, unit: "kopecks" | "count", value: number, prev: number) => ({
    key, label, unit, value, prev,
    delta_pct: prev ? Math.round(((value - prev) / prev) * 1000) / 10 : null,
  });
  const payers = Math.max(1, Math.round(612 * f));
  const prevPayers = Math.round(payers * 0.95);
  const now = Date.now();
  return {
    window_days: days,
    since: new Date(now - days * DAY).toISOString(),
    prev_since: new Date(now - 2 * days * DAY).toISOString(),
    prev_until: new Date(now - days * DAY).toISOString(),
    kpis: [
      kp("revenue", "Выручка VPN", "kopecks", net, Math.round(net * 0.91)),
      kp("payments", "Оплат", "count", count, Math.round(count * 0.94)),
      kp("payers", "Платящие", "count", payers, prevPayers),
      kp("new_payers", "Новые платящие", "count", Math.round(188 * f), Math.round(201 * f)),
      kp("new_users", "Новые пользователи", "count", Math.round(3_950 * f), Math.round(3_610 * f)),
      kp("arppu", "ARPPU", "kopecks", Math.round(net / payers), Math.round((net * 0.91) / prevPayers)),
    ],
    lines: {
      shop: { value: Math.round(38_00_000 * f), prev: Math.round(41_00_000 * f), delta_pct: -7.3 },
      proxy: { value: Math.round(3_20_000 * f), prev: Math.round(2_90_000 * f), delta_pct: 10.3 },
      game: { value: Math.round(3_40_000 * f), prev: Math.round(3_40_000 * f), delta_pct: 0 },
      gross: { value: net + Math.round(44_60_000 * f), prev: Math.round(net * 0.91) + Math.round(47_30_000 * f), delta_pct: 5.8 },
    },
    series,
    subscribers: {
      with_access: 1_842,
      paid: 1_391,
      by_kind: { paid: 1_391, gift: 42, granted: 118, trial: 291, bypass_only: 377 },
      granted_sources: { admin: 71, referral: 29, promo_link: 18 },
      expiring_7d: { total: 214, auto_renew_on: 74, by_kind: { paid: 160, gift: 4, granted: 10, trial: 40, bypass_only: 0 } },
      auto_renew_share: 38.2,
      renewal_rate: 64.3,
      churn_rate: 35.7,
      pipeline: mockPipeline(),
    },
    health: {
      status: "degraded",
      reasons: [{ level: "degraded", key: "worker_traffic_monitor", text: "«Мониторинг трафика» давно не завершал цикл." }],
      checked_at: new Date(now - 20_000).toISOString(),
    },
    payments: {
      status: "warning",
      reasons: [
        { level: "warning", key: "payment_errors", text: "Ошибки платежей за 24 ч: 3 (amount_mismatch ×2, telegram_payment_rejected ×1)." },
        { level: "warning", key: "silent_cryptobot", text: "CryptoBot: последняя оплата 19 ч назад, обычно не дольше 16 ч." },
      ],
      errors_24h: 3,
      invoices_24h: 612,
      paid_24h: 488,
      conversion_24h: 79.7,
      silent: ["cryptobot"],
    },
    delivery: { status: "ok", reasons: [], queue_open: 2, dead: 0, activations_pending: 1 },
    panel: {
      checked: true, available: true, online_now: 486, nodes_online: 5, nodes_total: 7,
      nodes_enabled: 6, nodes_offline: 1, nodes_disabled: 1,
    },
    alerts: [
      { level: "warning", key: "nodes_offline", title: "Ноды не в сети: 1 из 6", detail: "Клиенты переключатся на другие ноды. Проверьте ноду в панели.", link: "/panel" },
      { level: "warning", key: "health_worker_traffic_monitor", title: "Система: деградация", detail: "«Мониторинг трафика» давно не завершал цикл.", link: "/health" },
      { level: "warning", key: "payment_errors", title: "Ошибки платежей за 24 ч: 3", detail: "amount_mismatch ×2, telegram_payment_rejected ×1", link: "/health" },
      { level: "warning", key: "silent_cryptobot", title: "Провайдер молчит", detail: "CryptoBot: последняя оплата 19 ч назад, обычно не дольше 16 ч.", link: "/health" },
      { level: "info", key: "expiring", title: "Истекает за 7 дней: 214", detail: "Без автопродления: 140.", link: "/subscribers" },
    ],
  };
}

function mockSubscribers(days: number) {
  const o = mockOverview(days);
  return {
    window_days: days,
    active: {
      total: 2_219,
      with_access: o.subscribers.with_access,
      paid: o.subscribers.paid,
      by_kind: o.subscribers.by_kind,
      granted_sources: o.subscribers.granted_sources,
      by_tariff: { basic: 1_104, plus: 738 },
      auto_renew_paid: 531,
      auto_renew_share: 38.2,
      expiring_7d: o.subscribers.expiring_7d,
    },
    renewals: { grace_days: 3, ending: 402, renewed: 231, churned: 128, open: 43, renewal_rate: 64.3, churn_rate: 35.7 },
    renewals_prev: { grace_days: 3, ending: 380, renewed: 205, churned: 140, open: 0, renewal_rate: 59.4, churn_rate: 40.6 },
    trial: {
      trials: 1_140, matured_7d: 860, matured_30d: 310, paid_7d: 96, paid_7d_matured: 88,
      paid_30d: 141, paid_30d_matured: 47, paid_any: 150, rate_7d: 10.2, rate_30d: 15.2, rate_any: 13.2,
    },
    motion: {
      new: { count: 188, kopecks: 42_10_000, users: 186 },
      renewal: { count: 424, kopecks: 1_18_40_000 },
      auto_renew_balance: { count: 96, kopecks: 32_40_000 },
      new_share: 30.7,
    },
    growth: { new_paying: 186, churned: 128, net: 58, undecided: 43 },
    pipeline: mockPipeline(),
    funnel: {
      steps: [
        { key: "started", label: "Запустили бота", users: 3_950, of_start: 100, of_prev: null, recorded: true, note: null },
        { key: "trial", label: "Взяли пробный период", users: 1_140, of_start: 28.9, of_prev: 28.9, recorded: true, note: null },
        { key: "tariff_view", label: "Открыли тарифы", users: null, of_start: null, of_prev: null, recorded: false, note: "Не записывается: бот не логирует открытие экрана тарифов." },
        { key: "invoiced", label: "Создали счёт", users: 610, of_start: 15.4, of_prev: 15.4, recorded: true, note: null },
        { key: "paid", label: "Оплатили", users: 402, of_start: 10.2, of_prev: 65.9, recorded: true, note: null },
      ],
    },
    auto_renew: { balance: { count: 96, kopecks: 32_40_000 }, platega_recurring: { Active: 18, PastDue: 3, Canceled: 5 } },
  };
}

function mockCohorts(months: number) {
  const cohorts = Array.from({ length: months }, (_, i) => {
    const age = months - 1 - i;
    let cum = 0;
    const ltv = Array.from({ length: months }, (_, m) => {
      if (m > age) return null;
      cum += Math.round((m === 0 ? 52_000 : 21_000) * Math.pow(0.86, m));
      return cum;
    });
    return {
      cohort: new Date(Date.UTC(2025, 9 + i, 1)).toISOString().slice(0, 10),
      payers: 380 + i * 22,
      revenue_kopecks: (380 + i * 22) * (ltv.filter((v) => v != null).pop() ?? 0),
      ltv_kopecks: ltv,
    };
  });
  return { months, cohorts, avg_ltv_kopecks: 1_31_000 };
}

function mockOperations(hours: number) {
  const now = Date.now();
  return {
    hours,
    errors: {
      total: 7,
      by_stage: [
        { stage: "amount_mismatch", count: 3 },
        { stage: "signature_invalid", count: 2 },
        { stage: "provision_failed", count: 2 },
      ],
      by_provider: [
        { provider: "wata", count: 4 },
        { provider: "platega", count: 3 },
      ],
    },
    errors_recent: Array.from({ length: 6 }, (_, i) => ({
      id: 100 + i,
      telegram_id: 100_000 + i * 137,
      username: `user${i}`,
      purchase_id: `purchase_${i}`,
      payment_provider: i % 2 ? "wata" : "platega",
      amount_rubles: 299,
      stage: ["amount_mismatch", "signature_invalid", "provision_failed"][i % 3],
      error_code: null,
      error_message: "Сумма в вебхуке не совпала с ценой покупки",
      created_at: new Date(now - (i + 1) * 3_600_000).toISOString(),
    })),
    errors_daily: Array.from({ length: 14 }, (_, i) => ({
      date: new Date(now - (13 - i) * DAY).toISOString().slice(0, 10),
      stage: "amount_mismatch",
      provider: "wata",
      count: (i * 7) % 5,
    })),
    provisioning: { available: false },
    queues: { pending_invoices: 12, stuck_activations: 1 },
  };
}

function mockPanelOverview() {
  const bs = (cur: string, prev: string, cb: number, pb: number) => ({
    current: cur, previous: prev, current_bytes: cb, previous_bytes: pb,
  });
  const TiB = 1024 ** 4;
  return {
    system: {
      available: true,
      users_total: 4_210,
      status_counts: { ACTIVE: 2_164, LIMITED: 402, EXPIRED: 1_530, DISABLED: 114 },
      online_now: 486,
      online_day: 1_630,
      online_week: 2_310,
      never_online: 520,
      nodes_online: 5,
      traffic_lifetime_bytes: 412 * TiB,
      uptime_seconds: 1_209_600,
    },
    bandwidth: {
      available: true,
      day: bs("2.1 TiB", "1.9 TiB", 2.1 * TiB, 1.9 * TiB),
      week: bs("13.4 TiB", "12.8 TiB", 13.4 * TiB, 12.8 * TiB),
      days30: bs("55.2 TiB", "49.9 TiB", 55.2 * TiB, 49.9 * TiB),
      month: bs("24.8 TiB", "51.3 TiB", 24.8 * TiB, 51.3 * TiB),
      year: bs("380 TiB", "0", 380 * TiB, 0),
    },
    hwid: {
      available: true,
      unique_devices: 3_020,
      devices: 3_411,
      avg_per_user: 1.6,
      by_platform: [
        { platform: "android", count: 1_620 },
        { platform: "ios", count: 1_104 },
        { platform: "windows", count: 410 },
        { platform: "macos", count: 277 },
      ],
    },
    db: { premium_active: 1_842, bypass_entities: 790 },
    discrepancy: {
      available: true,
      panel_active: 2_164,
      panel_active_or_limited: 2_566,
      db_expected: 2_632,
      db_premium_active: 1_842,
      db_bypass_entities: 790,
      difference: -66,
    },
    cache_ttl_seconds: 45,
  };
}

function mockNodes() {
  const names = ["NL-Amsterdam-1", "DE-Frankfurt-1", "FI-Helsinki-1", "RU-Moscow-bypass", "KZ-Almaty-1", "US-NY-1", "TR-Istanbul-old"];
  const nodes = names.map((name, i) => ({
    uuid: `node-${i}`,
    name,
    country: name.slice(0, 2),
    state: i === 4 ? "offline" : i === 6 ? "disabled" : "online",
    status_message: i === 4 ? "Connection timeout" : null,
    users_online: i === 4 ? 0 : 60 + i * 23,
    traffic_used_bytes: (3 + i) * 1024 ** 4,
    traffic_limit_bytes: i === 1 ? 20 * 1024 ** 4 : null,
    xray_uptime_seconds: 86_400 * (i + 1),
    provider: ["Hetzner", "OVH", "Aeza"][i % 3],
  }));
  return {
    available: true,
    nodes: nodes.sort((a, b) => (a.state === "offline" ? -1 : b.state === "offline" ? 1 : 0)),
    total: nodes.length,
    disabled: 1,
    enabled: nodes.length - 1,
    online: 5,
    offline: 1,
    users_online: nodes.reduce((s, n) => s + n.users_online, 0),
  };
}

function mockBandwidth(days: number) {
  const cats = Array.from({ length: days }, (_, i) => new Date(Date.now() - (days - 1 - i) * DAY).toISOString().slice(0, 10));
  const series = ["NL-Amsterdam-1", "DE-Frankfurt-1", "FI-Helsinki-1", "US-NY-1"].map((name, k) => ({
    name,
    country: name.slice(0, 2),
    total_bytes: 0,
    data: cats.map((_, i) => Math.round((0.4 + 0.1 * k + 0.08 * Math.sin(i + k)) * 1024 ** 4)),
  }));
  series.forEach((s) => (s.total_bytes = s.data.reduce((a, b) => a + b, 0)));
  return {
    available: true,
    categories: cats,
    total: cats.map((_, i) => series.reduce((s, x) => s + x.data[i], 0)),
    series,
  };
}

function ago(ms: number) {
  return new Date(Date.now() - ms).toISOString();
}

/** Shape of GET /metrics/health (app/services/system_health.py). */
function mockHealth() {
  const H = 3_600_000;
  const w = (name: string, label: string, state: string, interval_s: number, lastOk: number | null, fails = 0, err: string | null = null) => ({
    name, label, state, interval_s,
    registered_at: ago(2 * DAY),
    last_ok_at: lastOk == null ? null : ago(lastOk * 1000),
    last_ok_age_s: lastOk,
    last_fail_at: fails ? ago(15 * 60_000) : null,
    last_error: err,
    ok_count: 120, fail_count: fails, skip_count: state === "paused" ? 40 : 0, consecutive_fails: fails,
  });
  return {
    checked_at: ago(20_000),
    db: { ready: true, ok: true, latency_ms: 1.8, pool: { size: 12, idle: 9, in_use: 3, min: 5, max: 50 } },
    remnawave: { enabled: true, ok: true, latency_ms: 214 },
    redis: { configured: true, ok: true, latency_ms: 0.9 },
    webhook: {
      ok: true, latency_ms: 88, url_set: true, url_host: "bot.atlassecure.example",
      pending_update_count: 0, pending_prev: 0, pending_growing: false, last_error_at: ago(3 * H), last_error_age_s: 10_800,
      last_error_message: "Read timeout expired", max_connections: 40,
    },
    workers: [
      w("activation_worker", "Отложенные активации", "ok", 420, 95),
      w("auto_renewal", "Автопродление", "ok", 720, 310),
      w("farm_notifications", "Игра: уведомления", "ok", 1920, 900),
      w("fast_expiry_cleanup", "Отключение истёкших", "ok", 180, 42),
      w("healthcheck", "Самопроверка БД", "ok", 630, 200),
      w("provisioning_worker", "Очередь выдачи доступа", "ok", 125, 3),
      w("reminders", "Напоминания о продлении", "ok", 2820, 1500),
      w("scheduled_broadcasts", "Запланированные рассылки", "ok", 960, 30),
      w("traffic_monitor", "Мониторинг трафика", "stale", 2400, 9_300),
      w("trial_notifications", "Уведомления пробного периода", "ok", 420, 120),
      w("wata_reconciler", "Сверка WATA", "failing", 600, 1_900, 3, "ReadTimeout"),
    ],
    flags: { background_workers: true, auto_renewal: true },
    alerts: { total: 7, by_category: { payment: 4, "push:payment": 2, worker: 1 }, window_s: 86_400, covered_s: 86_400 },
    uptime: { started_at: ago(2 * DAY + 5 * H), uptime_s: 190_800 },
    version: { git_sha: "a38b0b61c2d4", git_branch: "main", environment: "prod", dashboard_built_at: ago(2 * DAY + 5.2 * H) },
    overall: {
      status: "degraded",
      reasons: [
        { level: "degraded", key: "worker_traffic_monitor", text: "«Мониторинг трафика» давно не завершал цикл." },
        { level: "degraded", key: "worker_wata_reconciler", text: "«Сверка WATA» падает 3 раз подряд (ReadTimeout)." },
      ],
    },
  };
}

/** Shape of GET /metrics/payments-health (routes/metrics.py::_payments_health). */
function mockPaymentsHealth() {
  const H = 3_600_000;
  const p24 = mockProviders(1 / 30);
  return {
    now: new Date().toISOString(),
    providers_24h: p24,
    providers_7d: mockProviders(7 / 30),
    stuck: [
      { provider: "platega", count: 6, still_valid: 0, oldest_at: ago(23 * H), created_24h: p24[0].created },
      { provider: "wata", count: 4, still_valid: 0, oldest_at: ago(20 * H), created_24h: p24[1].created },
      { provider: "cryptobot", count: 1, still_valid: 0, oldest_at: ago(9 * H), created_24h: p24[3].created },
    ],
    time_to_pay: [
      { provider: null, count: 488, p50_s: 96, p90_s: 412 },
      { provider: "platega", count: 290, p50_s: 104, p90_s: 380 },
      { provider: "wata", count: 102, p50_s: 131, p90_s: 610 },
      { provider: "telegram_payment", count: 62, p50_s: 21, p90_s: 58 },
      { provider: "cryptobot", count: 34, p50_s: 540, p90_s: 1_720 },
    ],
    errors_24h: {
      total: 3,
      by_stage: [{ stage: "amount_mismatch", count: 2 }, { stage: "telegram_payment_rejected", count: 1 }],
      by_provider: [{ provider: "wata", count: 2 }, { provider: "telegram_payment", count: 1 }],
      matrix: [
        { stage: "amount_mismatch", provider: "wata", count: 2, last_at: ago(2 * H) },
        { stage: "telegram_payment_rejected", provider: "telegram_payment", count: 1, last_at: ago(6 * H) },
      ],
      telegram_money: 1,
    },
    last_paid: {
      platega: ago(4 * 60_000), wata: ago(26 * 60_000), telegram_payment: ago(50 * 60_000),
      telegram_stars: ago(3 * H), cryptobot: ago(19 * H),
    },
    paid_7d: { platega: 96, wata: 35, telegram_payment: 21, telegram_stars: 7, cryptobot: 10 },
    providers: [
      { provider: "cryptobot", enabled: true, last_paid_at: ago(19 * H), paid_7d: 10, state: "silent", age_h: 19, threshold_h: 16.8 },
      { provider: "platega", enabled: true, last_paid_at: ago(4 * 60_000), paid_7d: 96, state: "ok", age_h: 0.1, threshold_h: 7 },
      { provider: "telegram_payment", enabled: true, last_paid_at: ago(50 * 60_000), paid_7d: 21, state: "ok", age_h: 0.8, threshold_h: 32 },
      { provider: "telegram_stars", enabled: true, last_paid_at: ago(3 * H), paid_7d: 7, state: "ok", age_h: 3, threshold_h: 96 },
      { provider: "wata", enabled: true, last_paid_at: ago(26 * 60_000), paid_7d: 35, state: "ok", age_h: 0.4, threshold_h: 19.2 },
    ],
    verdict: {
      status: "warning",
      reasons: [
        { level: "warning", key: "payment_errors", text: "Ошибки платежей за 24 ч: 3 (amount_mismatch ×2, telegram_payment_rejected ×1)." },
        { level: "warning", key: "silent_cryptobot", text: "CryptoBot: последняя оплата 19 ч назад, обычно не дольше 17 ч." },
      ],
    },
  };
}

/** Shape of GET /metrics/delivery (database/metrics.py::delivery_health). */
function mockDelivery() {
  return {
    now: new Date().toISOString(),
    queue: {
      available: true, pending_new: 1, retrying: 1, running: 0, dead: 2, shadow: 0,
      done_24h: 604, dead_24h: 1, max_attempts_open: 2, oldest_open_at: ago(4 * 60_000), oldest_open_age_s: 240,
    },
    dead_jobs: [
      { id: 91_442, telegram_id: 100_411, source: "purchase", tariff_key: "plus", attempts: 8, last_error: "panel 502: bad gateway", created_at: ago(5 * 3_600_000), updated_at: ago(3 * 3_600_000) },
      { id: 90_118, telegram_id: 100_958, source: "autorenew", tariff_key: "combo_basic", attempts: 8, last_error: "conflict: bypass limit changed during apply", created_at: ago(2 * DAY), updated_at: ago(2 * DAY - 3_600_000) },
    ],
    activations: { pending: 1, failed: 4, max_attempts: 2 },
    errors_24h: { provisioning_dead: 1, provisioning_retry: 5, mismatch: 0, renewal_sync: 0, bypass_topup: 1 },
    verdict: {
      status: "critical",
      reasons: [
        { level: "critical", key: "dead_jobs", text: "Выдача не удалась: 2 заданий в статусе dead. Оплачено, доступа нет." },
        { level: "warning", key: "activations_pending", text: "Подписок ждут активации: 1." },
        { level: "info", key: "retrying", text: "Повторяются после ошибки: 1." },
      ],
    },
  };
}

/** Shape of GET /metrics/engagement (database/metrics.py::engagement). */
function mockEngagement(days: number) {
  const f = days / 7;
  return {
    window_days: days,
    reminders: { users: Math.round(640 * f) },
    automations: {
      available: true,
      sent: Math.round(2_310 * f), failed: Math.round(41 * f), blocked: Math.round(96 * f), skipped: Math.round(12 * f),
      by_key: [
        { key: "trial.reminder_24h", title: "Триал: за 24 ч до конца", category: "trial", enabled: true, sent: Math.round(820 * f), failed: 9, blocked: 31, skipped: 0 },
        { key: "subscription.expiry_3d", title: "Подписка: за 3 дня", category: "subscription", enabled: true, sent: Math.round(610 * f), failed: 12, blocked: 22, skipped: 0 },
        { key: "welcome.day1", title: "Приветствие: день 1", category: "welcome", enabled: true, sent: Math.round(540 * f), failed: 14, blocked: 30, skipped: 0 },
        { key: "referral.reward", title: "Реферал: начислен кэшбэк", category: "referral", enabled: false, sent: 0, failed: 0, blocked: 0, skipped: Math.round(12 * f) },
      ],
    },
    broadcasts: {
      count: 2, delivered: 18_420, failed: 1_310, delivery_rate: 93.4,
      recent: [
        { id: 88, title: "Скидка 20% на Plus", segment: "active_no_plus", created_at: ago(2 * DAY), delivered: 9_880, failed: 520, delivery_rate: 95 },
        { id: 87, title: "Новые ноды в Финляндии", segment: "all", created_at: ago(5 * DAY), delivered: 8_540, failed: 790, delivery_rate: 91.5 },
      ],
    },
    reach: { users: 12_480, unreachable: 1_040, unreachable_share: 8.3 },
    referrals: { invited: Math.round(57 * f), converted: Math.round(19 * f), cashback: { count: Math.round(22 * f), kopecks: Math.round(3_40_000 * f), referrers: Math.round(14 * f) } },
  };
}

/** Shape of GET /pricing/tariffs (app/api/dashboard/routes/pricing.py). */
function mockTariffs() {
  const base: Record<string, number> = { basic: 199, plus: 349, combo_basic: 299, combo_plus: 449 };
  return Object.entries(base).flatMap(([tariff, p]) =>
    [30, 90, 180, 365].map((period_days) => {
      const config_price = Math.round((p * period_days) / 30 * (period_days >= 180 ? 0.8 : 1));
      return {
        tariff,
        period_days,
        base_price: config_price,
        config_price,
        effective_price: config_price,
        discount_percent: 0,
        is_overridden: tariff === "plus" && period_days === 30,
        has_discount: false,
      };
    }),
  );
}

export function mockApi(): Plugin {
  return {
    name: "atlas-mock-api",
    apply: "serve",
    configureServer(server) {
      if (process.env.VITE_MOCK_API !== "1") return;

      server.config.logger.warn(
        "\n  ⚠  MOCK API ENABLED — dashboard serves fabricated data, auth is bypassed.\n" +
          "     Dev server only. Unset VITE_MOCK_API to talk to the real backend.\n"
      );

      server.middlewares.use((req, res, next) => {
        const url = req.url || "";
        if (!url.startsWith("/dashboard/api/")) return next();

        const path = url.split("?")[0].replace("/dashboard/api", "");
        const q = new URLSearchParams(url.split("?")[1] || "");
        const send = (body: unknown, status = 200) => {
          res.statusCode = status;
          res.setHeader("Content-Type", "application/json");
          res.end(JSON.stringify(body));
        };

        // Auth: report a live session so the SPA renders the dashboard
        // instead of the login form.
        if (path === "/auth/status")
          return send({ has_password: true, has_session: true, has_passkey: false });
        if (path === "/auth/me") return send({ telegram_id: 1 });
        if (path.startsWith("/auth/")) return send({ ok: true });

        // ── Users ──────────────────────────────────────────────────
        // Filtering and sorting are modelled rather than stubbed: the
        // listing's whole purpose is the filters, and a fixture that
        // ignores them cannot show whether they work.
        if (path === "/users/list") {
          let rows = MOCK_USERS.slice();
          const qq = (q.get("q") || "").toLowerCase();
          if (qq) {
            rows = rows.filter(
              (r) =>
                String(r.telegram_id).includes(qq) ||
                (r.username || "").toLowerCase().includes(qq),
            );
          }
          if (q.has("has_sub")) rows = rows.filter((r) => r.has_active_sub === (q.get("has_sub") === "true"));
          if (q.get("source")) rows = rows.filter((r) => r.source === q.get("source"));
          if (q.get("created_after"))
            rows = rows.filter((r) => r.created_at >= (q.get("created_after") as string));
          if (q.get("created_before"))
            rows = rows.filter((r) => r.created_at <= (q.get("created_before") as string));
          if (q.get("expires_before"))
            rows = rows.filter(
              (r) => !!r.expires_at && r.expires_at <= (q.get("expires_before") as string),
            );

          const sort = q.get("sort") || "created_at";
          const dir = q.get("order") === "asc" ? 1 : -1;
          const pick = (r: (typeof MOCK_USERS)[number]) =>
            sort === "balance"
              ? r.balance_kopecks
              : sort === "expires_at"
                ? Date.parse(r.expires_at || "") || 0
                : sort === "last_seen_at"
                  ? Date.parse(r.last_seen_at || "") || 0
                  : Date.parse(r.created_at) || 0;
          rows.sort((a, b) => (pick(a) < pick(b) ? -dir : pick(a) > pick(b) ? dir : 0));

          const total = rows.length;
          const limit = Number(q.get("limit")) || 50;
          const offset = Number(q.get("offset")) || 0;
          return send({ rows: rows.slice(offset, offset + limit), total, limit, offset });
        }

        if (path === "/users/search") {
          const qq = (q.get("q") || "").toLowerCase();
          const all = MOCK_USERS.filter(
            (r) =>
              String(r.telegram_id).includes(qq) ||
              (r.username || "").toLowerCase().includes(qq),
          );
          const limit = Number(q.get("limit")) || 25;
          return send({
            query: qq,
            // A real COUNT(*), so the UI can honestly say there are more.
            total: all.length,
            matches: all.slice(0, limit).map((r) => ({
              telegram_id: r.telegram_id,
              username: r.username,
              language: r.language,
              created_at: r.created_at,
              has_active_sub: r.has_active_sub,
            })),
          });
        }

        const userMatch = path.match(/^\/users\/(\d+)(\/[a-z-]+)?$/);
        if (userMatch) {
          const tg = Number(userMatch[1]);
          const sub = userMatch[2];
          if (!sub) return send(mockUserDetail(tg));
          if (sub === "/extended-stats")
            return send({
              renewals_count: 4,
              reissues_count: 1,
              total_spent_rubles: 3_480,
              total_payments_count: 9,
              first_paid_at: new Date(Date.now() - 300 * DAY).toISOString(),
              last_paid_at: new Date(Date.now() - 12 * DAY).toISOString(),
              referrer_telegram_id: MOCK_USERS[2].telegram_id,
              referrer_username: MOCK_USERS[2].username,
              referrals_invited_count: 6,
              referrals_rewarded_count: 4,
              traffic_gb_purchased_total: 45,
              traffic_purchases_count: 3,
            });
          if (sub === "/payments")
            return send(
              Array.from({ length: 9 }, (_, i) => ({
                id: i + 1,
                purchase_id: `pur_${tg}_${i}`,
                tariff: i % 3 === 0 ? "plus" : "basic",
                purchase_type: i === 4 ? "traffic_pack" : i === 6 ? "balance_topup" : "subscription",
                period_days: 30,
                price_kopecks: 39_000 + i * 1_000,
                price_rubles: 390 + i * 10,
                // Mixed paid/approved on purpose: the totals used to count
                // only one of the two and silently understated spend.
                status: i % 4 === 0 ? "approved" : i === 5 ? "expired" : "paid",
                created_at: new Date(Date.now() - (i + 1) * 21 * DAY).toISOString(),
                expires_at: new Date(Date.now() - (i + 1) * 21 * DAY + 3600_000).toISOString(),
                promo_code: i === 2 ? "SUMMER20" : null,
                is_combo: false,
                country: "nl",
                payment_provider: ["platega", "cryptobot", "telegram_stars", "wata"][i % 4],
                provider_invoice_id: `inv_${tg}_${i}`,
              })),
            );
          if (sub === "/history")
            return send(
              Array.from({ length: 6 }, (_, i) => ({
                id: i + 1,
                action_type: ["purchase", "renewal", "renewal", "reissue", "manual_reissue", "renewal"][i],
                start_date: new Date(Date.now() - (6 - i) * 30 * DAY).toISOString(),
                end_date: new Date(Date.now() - (5 - i) * 30 * DAY).toISOString(),
                created_at: new Date(Date.now() - (6 - i) * 30 * DAY).toISOString(),
              })),
            );
          return send({ ok: true });
        }

        if (path === "/audit/recent")
          return send(
            Array.from({ length: 5 }, (_, i) => ({
              id: i + 1,
              action: ["grant_subscription", "balance_change", "discount_create", "discount_delete", "revoke"][i],
              by: "admin",
              target_user: Number(q.get("telegram_id")) || null,
              details: i === 1 ? "+500 ₽ · компенсация" : null,
              created_at: new Date(Date.now() - (i + 1) * 3 * DAY).toISOString(),
            })),
          );

        // ── v3 metrics / panel / branding ──────────────────────────
        if (path === "/branding")
          return send({
            name: "Atlas Secure",
            short: "Atlas",
            admin_title: "Atlas Admin",
            logo_url: null,
            primary_color: "#F2E8C9",
            bot_username: "atlassecure_bot",
            support_url: "https://t.me/atlas_suppbot",
          });
        if (path === "/metrics/money") return send(mockMoney(Number(q.get("days")) || 30));
        if (path === "/metrics/overview") return send(mockOverview(Number(q.get("days")) || 7));
        if (path === "/metrics/subscribers") return send(mockSubscribers(Number(q.get("days")) || 30));
        if (path === "/metrics/cohorts") return send(mockCohorts(Number(q.get("months")) || 12));
        if (path === "/metrics/operations") return send(mockOperations(Number(q.get("hours")) || 24));
        if (path === "/metrics/health") return send(mockHealth());
        if (path === "/metrics/payments-health") return send(mockPaymentsHealth());
        if (path === "/metrics/delivery") return send(mockDelivery());
        if (path === "/metrics/pipeline") return send(mockPipeline());
        if (path === "/metrics/engagement") return send(mockEngagement(Number(q.get("days")) || 7));
        if (path === "/panel/overview") return send(mockPanelOverview());
        if (path === "/panel/nodes") return send(mockNodes());
        if (path === "/panel/bandwidth") return send(mockBandwidth(Number(q.get("days")) || 14));
        if (path === "/pricing/tariffs") return send(mockTariffs());
        // Shape of GET /payments/breakdown (app/api/dashboard/routes/payments.py).
        if (path === "/payments/breakdown")
          return send({
            hours: Number(q.get("hours")) || 168,
            total: { count: 820, revenue_rubles: 344_084 },
            by_provider: [
              { provider: "platega", count: 430, revenue_rubles: 181_200 },
              { provider: "wata", count: 190, revenue_rubles: 79_900 },
              { provider: "cryptobot", count: 120, revenue_rubles: 50_300 },
              { provider: "telegram_stars", count: 80, revenue_rubles: 32_684 },
            ],
            by_type: [
              { purchase_type: "subscription", count: 510, revenue_rubles: 213_300 },
              { purchase_type: "balance_topup", count: 180, revenue_rubles: 75_600 },
              { purchase_type: "traffic_pack", count: 130, revenue_rubles: 55_184 },
            ],
            by_tariff: [
              { tariff: "basic · 30 дн", count: 300, revenue_rubles: 59_700 },
              { tariff: "plus · 30 дн", count: 140, revenue_rubles: 48_860 },
            ],
            by_apple_nominal: [],
          });

        // Anything not modelled yet: empty but valid. Lists stay lists so
        // `.map()` on the client doesn't explode.
        //
        // The pattern is matched against the whole path, not just its last
        // segment: /links/stats returns a collection but says so nowhere in
        // its name, and defaulting it to {} white-screened the Ссылки page
        // on `list.data.map is not a function`.
        if (
          /(recent|list|top|pending|errors|feed|search|stats|links|codes|segments|log|candidates|by-provider|daily|hourly|scheduled|tariffs)/.test(
            path,
          ) || path === "/automated-notifications/"
        ) {
          return send([]);
        }
        return send({});
      });
    },
  };
}
