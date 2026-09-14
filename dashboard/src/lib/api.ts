import type {
  PurchaseRow,
  SubscriptionHistoryRow,
  UserDetail as UserDetailType,
  UserExtendedStats,
  UserListFilters,
  UserListResponse,
  UserSearchMatch,
} from "@/types/user";

const BASE = "/dashboard/api";

export class ApiError extends Error {
  status: number;
  detail: string;
  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
    this.detail = detail;
  }
}

export interface RequestOptions {
  /**
   * Idempotency-Key for a mutating call (app/api/dashboard/idempotency.py):
   * the server runs the action once per key and replays the stored answer
   * for repeats. Generate one per form submission and reuse it on retry.
   */
  idempotencyKey?: string;
}

/** A fresh key for one form submission. */
export function newIdempotencyKey(): string {
  try {
    return crypto.randomUUID();
  } catch {
    return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
  }
}

async function request<T>(path: string, init: RequestInit = {}, opts: RequestOptions = {}): Promise<T> {
  // Auth is the HttpOnly session cookie only. The magic-link token is a
  // bootstrap secret for /auth/setup (sent in the body there), never a
  // header: the server stops accepting it the moment a password exists.
  const headers = new Headers(init.headers);
  if (opts.idempotencyKey) headers.set("Idempotency-Key", opts.idempotencyKey);
  if (init.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const doFetch = () =>
    fetch(BASE + path, {
      ...init,
      headers,
      // Send the HttpOnly session cookie set by /api/auth/login.
      // Same-origin requests honour this by default in modern browsers,
      // but being explicit guards against quirks (Safari standalone PWA
      // sometimes drops cookies on cross-context navigations).
      credentials: "include",
    });
  let res: Response;
  try {
    res = await doFetch();
  } catch (e) {
    // A network failure on a keyed mutation is retried once with the SAME
    // key: if the first attempt did reach the server, the server replays
    // its answer instead of running the action a second time.
    if (!opts.idempotencyKey) throw e;
    await new Promise((r) => setTimeout(r, 800));
    res = await doFetch();
  }
  if (res.status === 401) {
    // Force a hard reload so route guards re-evaluate and show login.
    if (window.location.pathname !== "/dashboard/" && window.location.pathname !== "/dashboard") {
      window.location.assign("/dashboard/");
    }
    throw new ApiError(401, "Unauthorized");
  }

  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (body && typeof body.detail === "string") detail = body.detail;
    } catch {
      //
    }
    throw new ApiError(res.status, detail);
  }

  if (res.status === 204) return undefined as T;
  const ct = res.headers.get("content-type") || "";
  if (ct.includes("application/json")) return (await res.json()) as T;
  return (await res.text()) as unknown as T;
}

export const api = {
  get<T>(path: string) {
    return request<T>(path, { method: "GET" });
  },
  post<T>(path: string, body?: unknown, opts?: RequestOptions) {
    return request<T>(
      path,
      {
        method: "POST",
        body: body ? JSON.stringify(body) : undefined,
      },
      opts,
    );
  },
  put<T>(path: string, body?: unknown) {
    return request<T>(path, {
      method: "PUT",
      body: body ? JSON.stringify(body) : undefined,
    });
  },
  patch<T>(path: string, body?: unknown) {
    return request<T>(path, {
      method: "PATCH",
      body: body ? JSON.stringify(body) : undefined,
    });
  },
  del<T>(path: string, opts?: RequestOptions) {
    return request<T>(path, { method: "DELETE" }, opts);
  },
};

// ── Endpoint binders ────────────────────────────────────────────────
export interface PanelEntitySnapshot {
  source: "by_uuid" | "by_username" | "by_id";
  panel_id: number | null;
  vless_uuid: string | null;
  subscription_url: string | null;
  traffic_limit_bytes: number;
  used_traffic_bytes: number;
  status: string;
  telegram_id_field: number | null;
}

// The user-domain shapes live in @/types/user. They used to be declared
// here as Record<string, unknown>, which is why most of what the backend
// sends was never rendered: reading a field meant writing a cast, so the
// screen only ever read the four everybody already knew about.
export type {
  UserDetail,
  PremiumState,
  BypassState,
  UserExtendedStats,
  UserListFilters,
  UserListResponse,
  UserSearchMatch,
  PurchaseRow,
  SubscriptionHistoryRow,
} from "@/types/user";

export const endpoints = {
  authStatus: () =>
    api.get<{
      has_password: boolean;
      has_session: boolean;
      has_passkey?: boolean;
    }>("/auth/status"),
  authSetup: (body: { username: string; password: string; bootstrap_token: string }) =>
    api.post<{ ok: boolean }>("/auth/setup", body),
  authLogin: (body: { username: string; password: string }) =>
    api.post<{ ok: boolean }>("/auth/login", body),
  authLogout: () => api.post<{ ok: boolean }>("/auth/logout"),

  // Bypass-overwrite audit — список пострадавших + восстановление.
  bypassAuditList: () =>
    api.get<{
      total: number;
      can_fix: number;
      total_traffic_gb_purchased: number;
      victims: Array<{
        telegram_id: number;
        username: string | null;
        current_expires_at: string | null;
        current_is_bypass_only: boolean;
        current_subscription_type: string | null;
        current_source: string | null;
        current_is_combo: boolean;
        proposed_expires_at: string | null;
        history_end_date: string | null;
        grace_will_apply: boolean;
        last_paid_action_type: string | null;
        history: Array<{
          id: number;
          action_type: string;
          start_date: string | null;
          end_date: string | null;
          created_at: string | null;
        }>;
        payments: Array<{
          id: number;
          tariff: string;
          amount_rubles: number;
          paid_at: string | null;
          created_at: string | null;
          purchase_id: string | null;
        }>;
        traffic_purchases: Array<{
          id: number;
          gb_amount: number;
          price_rub: number;
          created_at: string | null;
        }>;
        traffic_total_gb: number;
        payments_count: number;
        premium_payments_count: number;
        can_fix: boolean;
      }>;
    }>("/bypass-audit"),
  bypassAuditFixOne: (telegram_id: number) =>
    api.post<{
      ok: boolean;
      telegram_id: number;
      before: Record<string, unknown> | null;
      after: Record<string, unknown> | null;
    }>(`/bypass-audit/fix/${telegram_id}`),
  bypassAuditFixAll: () =>
    api.post<{
      total: number;
      fixed: number;
      failed: number;
      results: Array<{
        telegram_id: number;
        ok: boolean;
        reason?: string;
      }>;
    }>("/bypass-audit/fix-all"),
  // ── Traffic audit: DB (subscription base + traffic_purchases) vs
  //    Remnawave panel (trafficLimitBytes). Найти юзеров у которых
  //    в панели меньше трафика чем оплачено.
  trafficAuditList: (opts?: { limit?: number; user?: number; concurrent?: number }) => {
    const p = new URLSearchParams();
    if (opts?.limit != null) p.set("limit", String(opts.limit));
    if (opts?.user != null) p.set("user", String(opts.user));
    if (opts?.concurrent != null) p.set("concurrent", String(opts.concurrent));
    const qs = p.toString() ? `?${p.toString()}` : "";
    return api.get<{
      summary: {
        total: number;
        match: number;
        mismatch: number;
        desync: number;
        no_entity: number;
        panel_error: number;
        shortfall_total_bytes: number;
        shortfall_total_gb: number;
      };
      results: Array<{
        tg: number;
        subscription_type: string;
        period_days: number | null;
        is_bypass_only: boolean;
        traffic_purchases_gb: number;
        traffic_purchases: Array<{
          id: number;
          gb_amount: number;
          price_rub: number;
          payment_method: string | null;
          created_at: string | null;
        }>;
        expected_bytes: number;
        actual_bytes: number;
        used_bytes: number;
        shortfall_bytes: number;
        panel_status: string;
        kind: "match" | "mismatch" | "desync" | "no_entity" | "panel_error";
        note: string;
        expected_gb: number;
        actual_gb: number;
        used_gb: number;
        shortfall_gb: number;
        db_uuid: string | null;
        db_id: number | null;
        db_sub_url: string | null;
        panel_by_our_ref: PanelEntitySnapshot | null;
        panel_by_username: PanelEntitySnapshot | null;
        desync: boolean;
      }>;
    }>(`/traffic-audit${qs}`);
  },
  trafficAuditResync: (telegram_id: number) =>
    api.post<{
      ok: boolean;
      new_id: number | null;
      new_uuid: string | null;
      new_sub_url: string | null;
      panel_limit_bytes: number;
      panel_used_bytes: number;
    }>(`/traffic-audit/resync/${telegram_id}`),
  trafficAuditFixOne: (telegram_id: number) =>
    api.post<{
      ok: boolean;
      before_bytes?: number;
      after_bytes?: number;
      used_bytes?: number;
      expected_bytes?: number;
      audit: Record<string, unknown>;
      reason?: string;
    }>(`/traffic-audit/fix/${telegram_id}`),
  trafficAuditFixAll: (opts?: { limit?: number; concurrent?: number }) => {
    const p = new URLSearchParams();
    if (opts?.limit != null) p.set("limit", String(opts.limit));
    if (opts?.concurrent != null) p.set("concurrent", String(opts.concurrent));
    const qs = p.toString() ? `?${p.toString()}` : "";
    return api.post<{
      audit_summary: {
        total: number;
        match: number;
        mismatch: number;
        shortfall_total_gb: number;
      };
      fixed: number;
      failed: number;
      results: Array<{
        telegram_id: number;
        ok: boolean;
        reason?: string;
        before_bytes: number;
        after_bytes: number | null;
        used_bytes: number;
        expected_bytes: number;
      }>;
    }>(`/traffic-audit/fix-all${qs}`);
  },
  statsHourly: (days = 7) =>
    api.get<{
      days: number;
      tz: string;
      series: Array<{
        hour: number;
        revenue_rubles: number;
        payments_count: number;
        new_users: number;
        new_subscriptions: number;
        new_paid_subscriptions: number;
      }>;
    }>(`/stats/hourly?days=${days}`),

  /**
   * The listing the Users screen never had. Until this existed the screen
   * could only answer "show me this one person", never "who signed up
   * today" or "whose subscription lapses this week".
   *
   * Undefined filters are dropped rather than sent empty, so the query
   * string stays a stable cache key across renders.
   */
  usersList: (f: UserListFilters = {}) => {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(f)) {
      if (v !== undefined && v !== null && v !== "") qs.set(k, String(v));
    }
    const s = qs.toString();
    return api.get<UserListResponse>(`/users/list${s ? `?${s}` : ""}`);
  },
  userSearch: (q: string, limit = 25) =>
    api.get<{
      query: string;
      /** Matches in the database, not the length of the returned slice —
          the old value made 300 hits look like 25 with no hint of more. */
      total: number;
      matches: UserSearchMatch[];
    }>(`/users/search?q=${encodeURIComponent(q)}&limit=${limit}`),
  userDetail: (tg: number) => api.get<UserDetailType>(`/users/${tg}`),
  userHistory: (tg: number, limit = 20) =>
    api.get<SubscriptionHistoryRow[]>(`/users/${tg}/history?limit=${limit}`),
  userExtended: (tg: number) =>
    api.get<UserExtendedStats>(`/users/${tg}/extended-stats`),

  userGrant: (tg: number, body: { days: number; tariff: string }, opts?: RequestOptions) =>
    api.post<{ ok: boolean; expires_at: string; vpn_key: string }>(
      `/users/${tg}/grant`,
      body,
      opts,
    ),
  userGrantMinutes: (tg: number, body: { minutes: number }, opts?: RequestOptions) =>
    api.post<{ ok: boolean; expires_at: string; vpn_key: string }>(
      `/users/${tg}/grant-minutes`,
      body,
      opts,
    ),
  userRevoke: (tg: number, opts?: RequestOptions) => api.post<{ ok: boolean }>(`/users/${tg}/revoke`, undefined, opts),
  userReissueAggregator: (tg: number, opts?: RequestOptions) =>
    api.post<{ ok: boolean; url: string }>(`/users/${tg}/reissue-aggregator`, undefined, opts),
  userSwitchTariff: (tg: number, body: { tariff: string }, opts?: RequestOptions) =>
    api.post<{ ok: boolean; subscription: unknown }>(`/users/${tg}/switch-tariff`, body, opts),
  userDiscountCreate: (
    tg: number,
    body: { percent: number; expires_in_hours: number | null },
    opts?: RequestOptions,
  ) => api.post<{ ok: boolean }>(`/users/${tg}/discount`, body, opts),
  userDiscountDelete: (tg: number, opts?: RequestOptions) => api.del<{ ok: boolean }>(`/users/${tg}/discount`, opts),
  userTrafficDiscountCreate: (
    tg: number,
    body: { percent: number; expires_in_hours: number | null },
    opts?: RequestOptions,
  ) =>
    api.post<{ ok: boolean; percent: number; expires_at: string | null }>(
      `/users/${tg}/traffic-discount`,
      body,
      opts,
    ),
  userTrafficDiscountDelete: (tg: number, opts?: RequestOptions) =>
    api.del<{ ok: boolean }>(`/users/${tg}/traffic-discount`, opts),
  userCashbackFixSet: (tg: number, body: { percent: number }, opts?: RequestOptions) =>
    api.post<{
      ok: boolean;
      percent: number;
      effective_percent: number;
      notify_sent: boolean;
    }>(`/users/${tg}/cashback-fix`, body, opts),
  userCashbackFixClear: (tg: number, opts?: RequestOptions) =>
    api.del<{ ok: boolean; effective_percent: number }>(
      `/users/${tg}/cashback-fix`,
      opts,
    ),
  userBalanceChange: (
    tg: number,
    body: { delta_rubles: number; reason?: string },
    opts?: RequestOptions,
  ) =>
    api.post<{ ok: boolean; new_balance_rubles: number }>(
      `/users/${tg}/balance`,
      body,
      opts,
    ),
  userPayments: (tg: number, limit = 20) =>
    api.get<PurchaseRow[]>(`/users/${tg}/payments?limit=${limit}`),

  /** `telegram_id` scopes the log to one user — that is what turns a
      global tail into "which admin did what to this person". */
  auditRecent: (limit = 50, telegramId?: number) =>
    api.get<Array<Record<string, unknown>>>(
      `/audit/recent?limit=${limit}${
        telegramId ? `&telegram_id=${telegramId}` : ""
      }`,
    ),

  broadcastsRecent: (limit = 20) =>
    api.get<Array<Record<string, unknown>>>(`/broadcasts/recent?limit=${limit}`),
  broadcastDetail: (id: number) =>
    api.get<Record<string, unknown>>(`/broadcasts/${id}`),
  broadcastStats: (id: number) =>
    api.get<Record<string, unknown>>(`/broadcasts/${id}/stats`),
  broadcastPatchTag: (id: number, tag: string | null, tagColor: string | null) =>
    api.patch<{
      ok: boolean;
      id: number;
      tag: string | null;
      tag_color: string | null;
    }>(`/broadcasts/${id}/tag`, { tag, tag_color: tagColor }),
  broadcastAnalytics: (id: number) =>
    api.get<{
      total_recipients: number;
      sent: number;
      failed: number;
      deleted: number;
      delivered: number;
      converted_1d: number;
      converted_3d: number;
      converted_7d: number;
      revenue_kop_1d: number;
      revenue_kop_3d: number;
      revenue_kop_7d: number;
      conversion_rate_7d: number;
      blocked_estimate: number;
    }>(`/broadcasts/${id}/analytics`),
  broadcastSegments: () =>
    api.get<
      Array<{
        key: string;
        label: string;
        description?: string;
        group?: string;
        count: number;
      }>
    >("/broadcasts/segments"),
  broadcastDeleteFromUsers: (id: number) =>
    api.post<{ ok: boolean; broadcast_id: number; total_messages: number }>(
      `/broadcasts/${id}/delete-from-users`,
    ),
  broadcastDeleteCancel: (id: number) =>
    api.post<{ ok: boolean }>(`/broadcasts/${id}/delete-from-users/cancel`),

  // ── Scheduled + recurring broadcasts (migration 067) ─────────────────
  broadcastScheduleCreate: (body: {
    source_broadcast_id: number;
    scheduled_at_msk: string; // "YYYY-MM-DD HH:MM"
    recurrence: "once" | "daily" | "weekdays" | "weekly";
    recurrence_end_at_msk?: string | null;
    segment?: string | null;
  }) =>
    api.post<{
      ok: boolean;
      sched_id: number;
      scheduled_at_utc: string;
      scheduled_at_msk: string;
      recurrence: string;
    }>(`/broadcasts/schedule`, body),
  broadcastScheduleList: (activeOnly = true, limit = 200) =>
    api.get<Array<Record<string, unknown>>>(
      `/broadcasts/scheduled?active_only=${activeOnly}&limit=${limit}`,
    ),
  broadcastScheduleCancel: (id: number) =>
    api.del<{ ok: boolean }>(`/broadcasts/scheduled/${id}`),
  broadcastCreate: (body: {
    title: string;
    message: string;
    segment: string;
    photo_file_id?: string | null;
    animation_file_id?: string | null;
    buttons: string[];
    discount_percent?: number | null;
    discount_hours?: number | null;
    discount_label?: string | null;
    gift_reveal_percent?: number | null;
    tag?: string | null;
    tag_color?: string | null;
  }, opts?: RequestOptions) =>
    api.post<{ ok: boolean; broadcast_id: number; audience: number }>(
      "/broadcasts",
      body,
      opts,
    ),
  broadcastTestSelf: (body: {
    title: string;
    message: string;
    segment: string;
    photo_file_id?: string | null;
    animation_file_id?: string | null;
    buttons: string[];
    discount_percent?: number | null;
    discount_hours?: number | null;
    discount_label?: string | null;
    gift_reveal_percent?: number | null;
    tag?: string | null;
    tag_color?: string | null;
  }) =>
    api.post<{
      ok: boolean;
      message_ids: number[];
      split: boolean;
      to: number;
    }>(
      "/broadcasts/test-self",
      body,
    ),

  referralsOverall: () =>
    api.get<Record<string, unknown>>("/referrals/overall"),
  referralsTop: (params: {
    sort_by?: "total_revenue" | "invited_count" | "cashback_paid";
    sort_order?: "ASC" | "DESC";
    limit?: number;
    offset?: number;
    q?: string;
  } = {}) => {
    const usp = new URLSearchParams();
    if (params.sort_by) usp.set("sort_by", params.sort_by);
    if (params.sort_order) usp.set("sort_order", params.sort_order);
    if (params.limit !== undefined) usp.set("limit", String(params.limit));
    if (params.offset !== undefined) usp.set("offset", String(params.offset));
    if (params.q) usp.set("q", params.q);
    const qs = usp.toString();
    return api.get<Array<Record<string, unknown>>>(
      "/referrals/top" + (qs ? `?${qs}` : ""),
    );
  },
  referrerDetail: (id: number) =>
    api.get<Record<string, unknown>>(`/referrals/${id}`),
  referrerHistory: (id: number, limit = 50) =>
    api.get<{ rows: Array<Record<string, unknown>>; total: number }>(
      `/referrals/${id}/history?limit=${limit}`,
    ),

  bgiftSummary: () =>
    api.get<Record<string, unknown>>("/bgift/summary"),
  bgiftList: (page = 0, page_size = 20, include_deleted = false) =>
    api.get<Array<Record<string, unknown>>>(
      `/bgift/list?page=${page}&page_size=${page_size}&include_deleted=${include_deleted}`,
    ),
  bgiftDetail: (id: number) =>
    api.get<Record<string, unknown>>(`/bgift/${id}`),
  bgiftRedemptions: (id: number, limit = 100) =>
    api.get<{ rows: Array<Record<string, unknown>>; total: number }>(
      `/bgift/${id}/redemptions?limit=${limit}`,
    ),
  bgiftCreate: (body: {
    gb_amount: number;
    validity_days: number;
    max_uses: number;
  }, opts?: RequestOptions) => api.post<Record<string, unknown>>("/bgift", body, opts),
  bgiftDelete: (id: number) => api.del<{ ok: boolean }>(`/bgift/${id}`),

  // ── Beta-testing applications ──────────────────────────────────────
  betaAppsSummary: (program = "vpn_innovator") =>
    api.get<{ program: string; total: number }>(
      `/beta-applications/summary?program=${encodeURIComponent(program)}`,
    ),
  betaAppsList: (page = 0, page_size = 50, program = "vpn_innovator") =>
    api.get<Array<Record<string, unknown>>>(
      `/beta-applications/list?program=${encodeURIComponent(program)}&page=${page}&page_size=${page_size}`,
    ),

  userDelete: (tg: number, opts?: RequestOptions) => api.del<{ ok: boolean }>(`/users/${tg}`, opts),

  // ── Marketing links: stats + promo ─────────────────────────────────
  statsLinksList: () =>
    api.get<Array<Record<string, unknown>>>("/links/stats"),
  statsLinkCreate: (body: { name: string }) =>
    api.post<Record<string, unknown>>("/links/stats", body),
  statsLinkDeactivate: (id: number) =>
    api.post<{ ok: boolean }>(`/links/stats/${id}/deactivate`),
  statsLinkReactivate: (id: number) =>
    api.post<{ ok: boolean }>(`/links/stats/${id}/reactivate`),
  statsLinkDelete: (id: number) =>
    api.del<{ ok: boolean }>(`/links/stats/${id}`),

  promoLinksList: () =>
    api.get<Array<Record<string, unknown>>>("/links/promo"),
  promoLinkCreate: (body: {
    name: string;
    reward_type: "subscription_days" | "tariff_discount" | "bypass_discount" | "bypass_gb";
    reward_value: number;
    max_uses_total?: number | null;
    max_uses_per_user?: number;
    reward_meta?: Record<string, unknown>;
    expires_in_hours?: number | null;
  }) =>
    api.post<Record<string, unknown>>("/links/promo", body),
  promoLinkDeactivate: (id: number) =>
    api.post<{ ok: boolean }>(`/links/promo/${id}/deactivate`),
  promoLinkReactivate: (id: number) =>
    api.post<{ ok: boolean }>(`/links/promo/${id}/reactivate`),
  promoLinkDelete: (id: number) =>
    api.del<{ ok: boolean }>(`/links/promo/${id}`),

  incidentGet: () =>
    api.get<{ is_active: boolean; incident_text: string | null }>("/incident"),
  incidentSet: (body: { is_active: boolean; incident_text?: string | null }) =>
    api.post<{ ok: boolean; is_active: boolean }>("/incident", body),

  promoList: () =>
    api.get<Array<Record<string, unknown>>>("/promo/list"),
  promoCreate: (body: {
    code: string;
    discount_percent: number;
    duration_seconds: number;
    max_uses: number;
  }, opts?: RequestOptions) =>
    api.post<{ ok: boolean; promo_id: number; code: string }>("/promo", body, opts),
  promoDeactivate: (id: number) =>
    api.del<{ ok: boolean }>(`/promo/${id}`),
  promoReactivate: (id: number) =>
    api.post<{ ok: boolean }>(`/promo/${id}/activate`),

  paymentsPending: () =>
    api.get<Array<Record<string, unknown>>>("/payments/pending"),
  paymentsBreakdown: (hours: number) =>
    api.get<{
      hours: number;
      total: { count: number; revenue_rubles: number };
      by_provider: Array<{
        provider: string;
        count: number;
        revenue_rubles: number;
      }>;
      by_type: Array<{
        purchase_type: string;
        count: number;
        revenue_rubles: number;
      }>;
      by_tariff: Array<{
        tariff: string;
        count: number;
        revenue_rubles: number;
      }>;
      by_apple_nominal: Array<{
        region: string;
        nominal: number;
        count: number;
        revenue_rubles: number;
      }>;
    }>(`/payments/breakdown?hours=${hours}`),
  paymentsRecent: (params: { limit?: number; hours?: number; status?: string } = {}) => {
    const u = new URLSearchParams();
    if (params.limit !== undefined) u.set("limit", String(params.limit));
    if (params.hours !== undefined) u.set("hours", String(params.hours));
    if (params.status) u.set("status", params.status);
    const qs = u.toString();
    return api.get<Array<Record<string, unknown>>>(
      "/payments/recent" + (qs ? `?${qs}` : ""),
    );
  },

  activationsPending: (limit = 100) =>
    api.get<{ total: number; rows: Array<Record<string, unknown>> }>(
      `/activations/pending?limit=${limit}`,
    ),
  activationRetry: (subscriptionId: number) =>
    api.post<{ ok: boolean; subscription_id: number; vpn_key?: string; error_message?: string }>(
      `/activations/${subscriptionId}/retry`,
    ),

  settingsNotificationsGet: () =>
    api.get<{
      payment_error: boolean;
      broadcast_done: boolean;
      revenue_milestone: boolean;
    }>("/settings/notifications"),
  settingsNotificationsPatch: (key: string, enabled: boolean) =>
    api.post<{
      payment_error: boolean;
      broadcast_done: boolean;
      revenue_milestone: boolean;
    }>("/settings/notifications", { key, enabled }),
  settingsTestNotifications: () =>
    api.post<{ ok: boolean; count: number; delay_seconds: number }>(
      "/settings/notifications/test",
    ),

  settingsSbpRouterGet: () =>
    api.get<{ mode: "platega" | "wata" | "split"; wata_percent: number }>(
      "/settings/sbp-router",
    ),
  settingsSbpRouterPatch: (mode: "platega" | "wata" | "split", wata_percent: number) =>
    api.post<{ mode: "platega" | "wata" | "split"; wata_percent: number }>(
      "/settings/sbp-router",
      { mode, wata_percent },
    ),

  // ── Reconciliation («Сверка») ─────────────────────────────────────
  reconciliationCandidates: () =>
    api.get<{
      total: number;
      items: Array<{
        telegram_id: number;
        username: string | null;
        subscription_type: string | null;
        source: string | null;
        status: string | null;
        admin_grant_days: number | null;
        is_bypass_only: boolean;
        expires_at: string | null;
        panel_expires_at: string | null;
        panel_available: boolean;
        panel_username?: string | null;
        activated_at: string | null;
        days_from_now: number;
        years_from_now: number;
        db_row_missing?: boolean;
        panel_unreachable?: boolean;
      }>;
    }>("/reconciliation/candidates"),
  reconciliationDetail: (telegram_id: number) =>
    api.get<{
      telegram_id: number;
      found: boolean;
      subscription: {
        expires_at: string | null;
        activated_at: string | null;
        subscription_type: string | null;
        source: string | null;
        status: string | null;
        is_bypass_only: boolean;
        admin_grant_days: number;
      };
      panel: {
        expires_at: string | null;
        days_from_now: number | null;
        available: boolean;
        matches_db: boolean;
      };
      payments: Array<{
        id: number;
        tariff: string;
        amount_rubles: number;
        status: string;
        paid_at: string | null;
        created_at: string | null;
        purchase_id: string | null;
        period_days: number | null;
        counted: boolean;
      }>;
      total_paid_days: number;
      actual_days_from_now: number;
      expected_days_from_now: number;
      expected_expires_at: string;
      delta_days: number;
      over_issuance_events: Array<{
        id: number;
        created_at: string | null;
        grant_action: string | null;
        source: string | null;
        tariff: string | null;
        old_expires_at: string | null;
        new_expires_at: string;
        duration_added_seconds: number | null;
        admin_telegram_id: number | null;
        admin_grant_days: number | null;
        caller_context: string | null;
      }>;
    }>(`/reconciliation/candidates/${telegram_id}`),
  reconciliationFix: (telegram_id: number, reason?: string) =>
    api.post<{
      success: boolean;
      log_id: number;
      old_expires_at: string | null;
      new_expires_at: string;
      days_removed: number;
      total_paid_days: number;
      admin_grant_days_kept: number;
      proof_payment_ids: number[];
      fallback_applied: "past_date" | "would_extend" | "no_payments" | null;
      panel_updated: boolean;
      panel_error: string | null;
      is_bypass_only: boolean;
    }>(
      `/reconciliation/fix/${telegram_id}${
        reason ? `?reason=${encodeURIComponent(reason)}` : ""
      }`,
    ),
  reconciliationOverIssuanceLog: () =>
    api.get<
      Array<{
        id: number;
        telegram_id: number;
        old_expires_at: string | null;
        new_expires_at: string;
        duration_added_seconds: number | null;
        grant_action: string | null;
        source: string | null;
        tariff: string | null;
        admin_telegram_id: number | null;
        admin_grant_days: number | null;
        caller_context: string | null;
        created_at: string;
      }>
    >("/reconciliation/over-issuance-log"),

  // ── Automated notifications (migration 068) ──────────────────────────
  automatedNotifications: () =>
    api.get<
      Array<{
        key: string;
        title: string;
        description: string | null;
        category: string;
        is_enabled: boolean;
        has_custom_text: boolean;
        default_text_ru: string;
        custom_text_ru: string | null;
        trigger_config: Record<string, unknown>;
        template_vars: string[];
        updated_at: string | null;
        last_edited_by: number | null;
        is_code_registered: boolean;
      }>
    >("/automated-notifications/"),
  automatedNotificationCreate: (body: {
    key: string;
    title: string;
    description?: string;
    category: string;
    default_text_ru: string;
    template_vars?: string[];
    trigger_config?: Record<string, unknown>;
  }) =>
    api.post<{ ok: boolean; key: string; created: boolean }>(
      "/automated-notifications/",
      body,
    ),
  automatedNotificationDelete: (key: string) =>
    api.del<{ ok: boolean; key: string; deleted: boolean }>(
      `/automated-notifications/${encodeURIComponent(key)}`,
    ),
  automatedNotificationPatch: (
    key: string,
    body: {
      custom_text_ru?: string | null;
      is_enabled?: boolean;
      trigger_config?: Record<string, unknown>;
    },
  ) =>
    api.patch<{ ok: boolean; key: string }>(
      `/automated-notifications/${encodeURIComponent(key)}`,
      body,
    ),
  automatedNotificationReset: (key: string) =>
    api.post<{ ok: boolean; key: string; reset: boolean }>(
      `/automated-notifications/${encodeURIComponent(key)}/reset`,
    ),
  automatedNotificationStats: (key: string, hours = 168) =>
    api.get<{
      key: string;
      hours: number;
      sent: number;
      failed: number;
      blocked: number;
      skipped: number;
    }>(
      `/automated-notifications/${encodeURIComponent(key)}/stats?hours=${hours}`,
    ),
  automatedNotificationTestSend: (key: string) =>
    api.post<{ ok: boolean; sent_to: number; key: string }>(
      `/automated-notifications/${encodeURIComponent(key)}/test-send`,
    ),

  // ── Pricing management (migration 069) ────────────────────────────
  pricingTariffs: () =>
    api.get<
      Array<{
        tariff: string;
        period_days: number;
        base_price: number;
        config_price: number;
        effective_price: number;
        discount_percent: number;
        is_overridden: boolean;
        has_discount: boolean;
      }>
    >("/pricing/tariffs"),
  pricingSetOverride: (tariff: string, periodDays: number, priceRub: number) =>
    api.patch<{
      ok: boolean;
      tariff: string;
      period_days: number;
      price_rub: number;
    }>(
      `/pricing/tariffs/${encodeURIComponent(tariff)}/${periodDays}`,
      { price_rub: priceRub },
    ),
  pricingClearOverride: (tariff: string, periodDays: number) =>
    api.del<{ ok: boolean; tariff: string; period_days: number; cleared: boolean }>(
      `/pricing/tariffs/${encodeURIComponent(tariff)}/${periodDays}`,
    ),
  pricingGetGlobalDiscount: () =>
    api.get<{
      global_discount_percent: number;
      discount_reason: string | null;
      discount_until_at: string | null;
      updated_at: string | null;
      updated_by: number | null;
    }>("/pricing/global-discount"),
  pricingSetGlobalDiscount: (body: {
    percent: number;
    reason?: string | null;
    until_at_iso?: string | null;
  }) => api.put<{ ok: boolean; percent: number }>("/pricing/global-discount", body),
  pricingClearGlobalDiscount: () =>
    api.del<{ ok: boolean; cleared: boolean }>("/pricing/global-discount"),

  remnawaveBackfillStart: (dry_run: boolean) =>
    api.post<{
      ok: boolean;
      error?: string;
      status: RemnawaveBackfillStatus;
    }>("/remnawave/backfill/start", { dry_run }),
  remnawaveBackfillStatus: () =>
    api.get<RemnawaveBackfillStatus>("/remnawave/backfill/status"),
  remnawaveResetPremiumUnlimited: (dry_run: boolean = true) => {
    const p = new URLSearchParams({ dry_run: String(dry_run) });
    return api.post<{
      total: number;
      checked: number;
      limited: number;
      reset: number;
      errors: number;
      dry_run: boolean;
      samples: Array<{
        telegram_id: number;
        premium_id: number;
        before_limit_bytes: number;
        before_status: string;
      }>;
    }>(`/remnawave/reset-premium-unlimited?${p.toString()}`);
  },

  // ── Remnawave user tags by tariff (Settings) ──────────────────────
  remnawaveTagsStatus: () => api.get<RemnawaveTagsStatus>("/remnawave-tags/status"),
  remnawaveTagsPreview: () => api.get<RemnawaveTagsPreview>("/remnawave-tags/preview"),
  remnawaveTagsStart: () =>
    api.post<RemnawaveTagsActionResult>("/remnawave-tags/start", {}, { idempotencyKey: newIdempotencyKey() }),
  remnawaveTagsPause: () =>
    api.post<RemnawaveTagsActionResult>("/remnawave-tags/pause", {}, { idempotencyKey: newIdempotencyKey() }),
  remnawaveTagsResume: () =>
    api.post<RemnawaveTagsActionResult>("/remnawave-tags/resume", {}, { idempotencyKey: newIdempotencyKey() }),
  remnawaveTagsStop: () =>
    api.post<RemnawaveTagsActionResult>("/remnawave-tags/stop", {}, { idempotencyKey: newIdempotencyKey() }),
};

export type RemnawaveTag = "TRIAL" | "BASIC" | "PLUS" | "COMBO_BASIC" | "COMBO_PLUS" | "BYPASS";

export type RemnawaveTagsState =
  | "idle"
  | "running"
  | "paused"
  | "interrupted"
  | "stopped"
  | "done"
  | "failed";

export interface RemnawaveTagsStatus {
  state: RemnawaveTagsState;
  running: boolean;
  total: number;
  done: number;
  patched: number;
  errors: number;
  /** Entities patched per tag. */
  per_tag: Record<string, number>;
  last_error: string | null;
  started_at: string | null;
  updated_at: string | null;
  finished_at: string | null;
  started_by: number | null;
  rate_per_sec: number;
}

export interface RemnawaveTagsPreview {
  generated_at: string;
  /** Users with an active subscription. */
  users: number;
  /** Their entities found in the panel. */
  entities: number;
  /** Entities whose tag differs — would be patched. */
  differ: number;
  already: number;
  /** Entity absent in the panel. */
  missing: number;
  tags: { tag: RemnawaveTag; total: number; differ: number }[];
  eta_seconds: number;
}

export interface RemnawaveTagsActionResult {
  ok: boolean;
  status: RemnawaveTagsStatus;
}

export interface RemnawaveBackfillStatus {
  running: boolean;
  started_at: number | null;
  finished_at: number | null;
  dry_run: boolean;
  total: number;
  processed: number;
  already_set: number;
  id_backfilled: number;
  tg_backfilled: number;
  missing: number;
  errors: number;
  last_error: string | null;
  elapsed_sec: number;
}

// CSV download via fetch + blob (session cookie). Triggers a browser
// download; errors surface as ApiError like every other call.
export async function downloadCsv(path: string, filename: string) {
  const res = await fetch(`/dashboard/api${path}`, { credentials: "include" });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      //
    }
    throw new ApiError(res.status, detail);
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

// Multipart upload — special case, can't use api.post (JSON-only).
async function _uploadMultipart(
  path: string, file: File,
): Promise<{ file_id: string }> {
  const fd = new FormData();
  fd.append("file", file);
  const res = await fetch(path, { method: "POST", credentials: "include", body: fd });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      //
    }
    throw new ApiError(res.status, detail);
  }
  return res.json();
}

export function uploadBroadcastPhoto(file: File): Promise<{ file_id: string }> {
  return _uploadMultipart("/dashboard/api/broadcasts/upload-photo", file);
}

export function uploadBroadcastAnimation(file: File): Promise<{ file_id: string }> {
  return _uploadMultipart("/dashboard/api/broadcasts/upload-animation", file);
}
