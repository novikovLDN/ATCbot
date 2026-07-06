import { auth } from "./auth";

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

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = auth.get();
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (init.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const res = await fetch(BASE + path, {
    ...init,
    headers,
    // Send the HttpOnly session cookie set by /api/auth/login.
    // Same-origin requests honour this by default in modern browsers,
    // but being explicit guards against quirks (Safari standalone PWA
    // sometimes drops cookies on cross-context navigations).
    credentials: "include",
  });
  if (res.status === 401) {
    auth.clear();
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
  post<T>(path: string, body?: unknown) {
    return request<T>(path, {
      method: "POST",
      body: body ? JSON.stringify(body) : undefined,
    });
  },
  put<T>(path: string, body?: unknown) {
    return request<T>(path, {
      method: "PUT",
      body: body ? JSON.stringify(body) : undefined,
    });
  },
  del<T>(path: string) {
    return request<T>(path, { method: "DELETE" });
  },
};

// ── Endpoint binders ────────────────────────────────────────────────
export interface StatsOverview {
  total_users?: number;
  active_subscriptions?: number;
  pending_payments?: number;
  business_metrics?: Record<string, number | string>;
  [k: string]: unknown;
}

export interface RevenueStats {
  total_revenue_rubles: number;
  paying_users: number;
  arpu_rubles: number;
  avg_ltv_rubles: number;
}

export interface UserDetail {
  user: Record<string, unknown>;
  balance_rubles: number;
  subscription: Record<string, unknown> | null;
  trial: Record<string, unknown> | null;
  discount: Record<string, unknown> | null;
  traffic_discount: Record<string, unknown> | null;
  is_vip: boolean;
}

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
  authMe: () => api.get<{ telegram_id: number }>("/auth/me"),
  authVerify: (token: string) =>
    api.get<{ telegram_id: number; role: string; expires_at: number }>(
      `/auth/verify?token=${encodeURIComponent(token)}`,
    ),
  statsOverview: () => api.get<StatsOverview>("/stats/overview"),
  statsBusiness: () => api.get<Record<string, number>>("/stats/business"),
  statsRevenue: () => api.get<RevenueStats>("/stats/revenue"),
  statsPeriod: (hours: number) =>
    api.get<Record<string, number>>(`/stats/period?hours=${hours}`),
  statsPeriodSince: (sinceIso: string) =>
    api.get<Record<string, number>>(
      `/stats/period?since=${encodeURIComponent(sinceIso)}`,
    ),
  statsBreakdown: () => api.get<Record<string, unknown>>("/stats/purchase-breakdown"),
  statsPromo: () => api.get<unknown[]>("/stats/promo"),

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
  statsDaily: (days = 30) =>
    api.get<{
      days: number;
      series: Array<{
        date: string;
        revenue_rubles: number;
        payments_count: number;
        new_users: number;
        new_subscriptions: number;
        new_paid_subscriptions: number;
      }>;
    }>(`/stats/daily?days=${days}`),
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

  userSearch: (q: string) =>
    api.get<{
      query: string;
      total: number;
      matches: Array<{
        telegram_id: number;
        username: string | null;
        language: string | null;
        created_at: string | null;
        has_active_sub: boolean;
      }>;
    }>(`/users/search?q=${encodeURIComponent(q)}`),
  userDetail: (tg: number) => api.get<UserDetail>(`/users/${tg}`),
  userHistory: (tg: number, limit = 20) =>
    api.get<unknown[]>(`/users/${tg}/history?limit=${limit}`),
  userExtended: (tg: number) => api.get<Record<string, unknown>>(`/users/${tg}/extended-stats`),

  userGrant: (tg: number, body: { days: number; tariff: string }) =>
    api.post<{ ok: boolean; expires_at: string; vpn_key: string }>(
      `/users/${tg}/grant`,
      body,
    ),
  userGrantMinutes: (tg: number, body: { minutes: number }) =>
    api.post<{ ok: boolean; expires_at: string; vpn_key: string }>(
      `/users/${tg}/grant-minutes`,
      body,
    ),
  userRevoke: (tg: number) => api.post<{ ok: boolean }>(`/users/${tg}/revoke`),
  userSwitchTariff: (tg: number, body: { tariff: string }) =>
    api.post<{ ok: boolean; subscription: unknown }>(`/users/${tg}/switch-tariff`, body),
  userDiscountCreate: (
    tg: number,
    body: { percent: number; expires_in_hours: number | null },
  ) => api.post<{ ok: boolean }>(`/users/${tg}/discount`, body),
  userDiscountDelete: (tg: number) => api.del<{ ok: boolean }>(`/users/${tg}/discount`),
  userTrafficDiscountCreate: (
    tg: number,
    body: { percent: number; expires_in_hours: number | null },
  ) =>
    api.post<{ ok: boolean; percent: number; expires_at: string | null }>(
      `/users/${tg}/traffic-discount`,
      body,
    ),
  userTrafficDiscountDelete: (tg: number) =>
    api.del<{ ok: boolean }>(`/users/${tg}/traffic-discount`),
  userVipGrant: (tg: number) => api.post<{ ok: boolean }>(`/users/${tg}/vip`),
  userVipRevoke: (tg: number) => api.del<{ ok: boolean }>(`/users/${tg}/vip`),
  userBalanceChange: (
    tg: number,
    body: { delta_rubles: number; reason?: string },
  ) =>
    api.post<{ ok: boolean; new_balance_rubles: number }>(
      `/users/${tg}/balance`,
      body,
    ),
  userPayments: (tg: number, limit = 20) =>
    api.get<Array<Record<string, unknown>>>(`/users/${tg}/payments?limit=${limit}`),

  auditRecent: (limit = 50) =>
    api.get<Array<Record<string, unknown>>>(`/audit/recent?limit=${limit}`),

  broadcastsRecent: (limit = 20) =>
    api.get<Array<Record<string, unknown>>>(`/broadcasts/recent?limit=${limit}`),
  broadcastDetail: (id: number) =>
    api.get<Record<string, unknown>>(`/broadcasts/${id}`),
  broadcastStats: (id: number) =>
    api.get<Record<string, unknown>>(`/broadcasts/${id}/stats`),
  broadcastSegments: () =>
    api.get<Array<{ key: string; label: string; count: number }>>(
      "/broadcasts/segments",
    ),
  broadcastDeleteFromUsers: (id: number) =>
    api.post<{ ok: boolean; broadcast_id: number; total_messages: number }>(
      `/broadcasts/${id}/delete-from-users`,
    ),
  broadcastDeleteCancel: (id: number) =>
    api.post<{ ok: boolean }>(`/broadcasts/${id}/delete-from-users/cancel`),
  broadcastCreate: (body: {
    title: string;
    message: string;
    segment: string;
    photo_file_id?: string | null;
    buttons: string[];
    discount_percent?: number | null;
    discount_hours?: number | null;
    discount_label?: string | null;
    gift_reveal_percent?: number | null;
  }) =>
    api.post<{ ok: boolean; broadcast_id: number; audience: number }>(
      "/broadcasts",
      body,
    ),
  broadcastTestSelf: (body: {
    title: string;
    message: string;
    segment: string;
    photo_file_id?: string | null;
    buttons: string[];
    discount_percent?: number | null;
    discount_hours?: number | null;
    discount_label?: string | null;
    gift_reveal_percent?: number | null;
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
  }) => api.post<Record<string, unknown>>("/bgift", body),
  bgiftDelete: (id: number) => api.del<{ ok: boolean }>(`/bgift/${id}`),

  userDelete: (tg: number) => api.del<{ ok: boolean }>(`/users/${tg}`),

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
  }) =>
    api.post<{ ok: boolean; promo_id: number; code: string }>("/promo", body),
  promoDeactivate: (id: number) =>
    api.del<{ ok: boolean }>(`/promo/${id}`),
  promoReactivate: (id: number) =>
    api.post<{ ok: boolean }>(`/promo/${id}/activate`),

  paymentsPending: () =>
    api.get<Array<Record<string, unknown>>>("/payments/pending"),
  paymentsRevenue: (hours: number) =>
    api.get<{
      revenue_rubles: number;
      payments_count: number;
      avg_check_rubles: number;
      by_type: Record<string, { count: number; revenue_rubles: number }>;
    }>(`/payments/revenue?hours=${hours}`),
  paymentsRevenueSince: (sinceIso: string) =>
    api.get<{
      revenue_rubles: number;
      payments_count: number;
      avg_check_rubles: number;
      by_type: Record<string, { count: number; revenue_rubles: number }>;
    }>(`/payments/revenue?since=${encodeURIComponent(sinceIso)}`),
  paymentsByProvider: (hours: number) =>
    api.get<Array<{ provider: string; count: number; revenue_rubles: number }>>(
      `/payments/by-provider?hours=${hours}`,
    ),
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
  paymentsTraffic: (hours: number) =>
    api.get<{
      count: number;
      revenue_rubles: number;
      total_gb: number;
      by_method: Array<{
        method: string;
        count: number;
        revenue_rubles: number;
        total_gb: number;
      }>;
    }>(`/payments/traffic?hours=${hours}`),
  paymentsErrorsSummary: (hours: number) =>
    api.get<{
      total: number;
      by_stage: Array<{ stage: string; count: number }>;
      by_provider: Array<{ provider: string; count: number }>;
    }>(`/payments/errors/summary?hours=${hours}`),
  paymentsErrors: (params: { limit?: number; hours?: number; provider?: string; stage?: string } = {}) => {
    const u = new URLSearchParams();
    if (params.limit !== undefined) u.set("limit", String(params.limit));
    if (params.hours !== undefined) u.set("hours", String(params.hours));
    if (params.provider) u.set("provider", params.provider);
    if (params.stage) u.set("stage", params.stage);
    const qs = u.toString();
    return api.get<Array<Record<string, unknown>>>(
      "/payments/errors" + (qs ? `?${qs}` : ""),
    );
  },
  paymentDetail: (id: number) =>
    api.get<Record<string, unknown>>(`/payments/${id}`),

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
  reconciliationAuditLog: () =>
    api.get<
      Array<{
        id: number;
        telegram_id: number;
        old_expires_at: string;
        new_expires_at: string;
        old_days_from_now: number;
        new_days_from_now: number;
        days_removed: number;
        reason: string;
        proof_payment_ids: number[];
        total_paid_days: number;
        admin_grant_days_kept: number;
        admin_telegram_id: number | null;
        created_at: string;
      }>
    >("/reconciliation/audit-log"),
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
};

// Auth-aware CSV download via fetch + blob. Returns nothing; triggers
// a browser download. We can't use a plain <a href="..."> because the
// Authorization header is required and browsers won't attach it to
// raw link clicks.
export async function downloadCsv(path: string, filename: string) {
  const token = auth.get();
  const res = await fetch(`/dashboard/api${path}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
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
export async function uploadBroadcastPhoto(file: File): Promise<{ file_id: string }> {
  const token = auth.get();
  const fd = new FormData();
  fd.append("file", file);
  const res = await fetch("/dashboard/api/broadcasts/upload-photo", {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: fd,
  });
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
