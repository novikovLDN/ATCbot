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

  const res = await fetch(BASE + path, { ...init, headers });
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
  is_vip: boolean;
}

export const endpoints = {
  authVerify: (token: string) =>
    api.get<{ telegram_id: number; role: string; expires_at: number }>(
      `/auth/verify?token=${encodeURIComponent(token)}`,
    ),
  statsOverview: () => api.get<StatsOverview>("/stats/overview"),
  statsBusiness: () => api.get<Record<string, number>>("/stats/business"),
  statsRevenue: () => api.get<RevenueStats>("/stats/revenue"),
  statsPeriod: (hours: number) =>
    api.get<Record<string, number>>(`/stats/period?hours=${hours}`),
  statsBreakdown: () => api.get<Record<string, unknown>>("/stats/purchase-breakdown"),
  statsPromo: () => api.get<unknown[]>("/stats/promo"),

  userSearch: (q: string) =>
    api.get<Record<string, unknown>>(`/users/search?q=${encodeURIComponent(q)}`),
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
  broadcastCreate: (body: {
    title: string;
    message: string;
    segment: string;
    photo_file_id?: string | null;
    buttons: string[];
    discount_percent?: number | null;
    discount_hours?: number | null;
    discount_label?: string | null;
  }) =>
    api.post<{ ok: boolean; broadcast_id: number; audience: number }>(
      "/broadcasts",
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

  paymentsPending: () =>
    api.get<Array<Record<string, unknown>>>("/payments/pending"),
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
