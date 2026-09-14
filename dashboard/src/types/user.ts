/**
 * User domain types.
 *
 * These replace the `Record<string, unknown>` fields that `UserDetail`
 * used to carry. That shape was not a typing shortcut — it was the reason
 * the screen could not show most of its own data: the backend sends about
 * forty subscription fields and the UI read two, because reaching for a
 * third meant writing another cast and nobody could see what was in there.
 *
 * The optional markers are deliberate and not defensive. `SELECT *` over a
 * table that has grown through ~60 migrations returns columns that older
 * rows never had, so a field being absent is a real runtime state rather
 * than a hypothetical one.
 */

/** `SELECT * FROM users`. */
export interface UserRecord {
  id?: number;
  telegram_id: number;
  username?: string | null;
  language?: string | null;
  created_at?: string | null;
  /** Whether the bot can still message them — a blocked bot explains a
      failed notification that otherwise looks like our bug. */
  is_reachable?: boolean | null;
  last_seen_at?: string | null;

  referral_code?: string | null;
  /** Partner tier of the referral program — not the subscription VIP perk,
      which was removed on 2026-09-14. */
  referral_level?: "base" | "vip" | null;
  referrer_id?: number | null;

  /** Kopecks. `UserDetail.balance_rubles` is the same value converted. */
  balance?: number | null;

  trial_used_at?: string | null;
  trial_expires_at?: string | null;

  cashback_fixed_percent?: number | null;
  cashback_floor_percent?: number | null;

  /** Gamification. Invisible in the admin panel until now. */
  farm_plot_count?: number | null;
  dice_last_played?: string | null;
  game_last_played?: string | null;

  proxy_purchased_at?: string | null;
  smart_offer_sent?: boolean | null;
  special_offer_created_at?: string | null;

  [k: string]: unknown;
}

export type ActivationStatus =
  | "pending"
  | "active"
  | "failed"
  | "retrying"
  | string;

/** `SELECT * FROM subscriptions`. */
export interface UserSubscription {
  id?: number;
  telegram_id?: number;
  expires_at?: string | null;
  subscription_type?: string | null;
  status?: string | null;
  /** `payment` | `admin` | `trial` — separates a paying customer from one
      who was granted access by hand. */
  source?: string | null;

  vpn_key?: string | null;
  vpn_key_plus?: string | null;
  uuid?: string | null;
  remnawave_premium_uuid?: string | null;
  remnawave_premium_short_uuid?: string | null;
  remnawave_premium_sub_url?: string | null;
  remnawave_bypass_short_uuid?: string | null;
  remnawave_bypass_sub_url?: string | null;

  auto_renew?: boolean | null;
  last_auto_renewal_at?: string | null;

  /** The three fields that answer "they paid and got nothing". */
  activation_status?: ActivationStatus | null;
  activation_attempts?: number | null;
  last_activation_error?: string | null;
  activated_at?: string | null;

  /** Traffic actually used. Zero bytes long after activation is the
      clearest churn signal the system has. */
  last_bytes?: number | null;
  first_traffic_at?: string | null;

  admin_grant_days?: number | null;
  is_combo?: boolean | null;
  is_bypass_only?: boolean | null;
  country?: string | null;

  [k: string]: unknown;
}

export interface UserTrial {
  trial_used_at?: string | null;
  trial_expires_at?: string | null;
}

/** `user_discounts` and `user_traffic_discounts` share this shape. */
export interface UserDiscount {
  id?: number;
  telegram_id?: number;
  discount_percent: number;
  expires_at?: string | null;
  /** Which admin issued it. Already sent by the backend, never displayed —
      it is a ready-made audit trail. */
  created_by?: number | string | null;
  created_at?: string | null;
}

export interface UserDetail {
  user: UserRecord;
  balance_rubles: number;
  subscription: UserSubscription | null;
  /** False when `subscription` describes an expired one. Without this the
      screen showed nothing at all for lapsed users. */
  subscription_is_active?: boolean;
  trial: UserTrial | null;
  discount: UserDiscount | null;
  traffic_discount: UserDiscount | null;
  cashback_fixed_percent: number | null;
  cashback_effective_percent: number;
  referral_link?: string | null;
  /** Premium entity (remnawave_premium_uuid + expires_at, not bypass-only). */
  premium: PremiumState;
  /** Bypass traffic, pulled live from Remnawave. limit_bytes = 0 ⇒ unlimited. */
  bypass: BypassState;
}

/** `premium` block of `/users/{tg}`. */
export interface PremiumState {
  has_entity: boolean;
  is_active: boolean;
  expires_at: string | null;
  subscription_type?: string | null;
}

/** `bypass` block of `/users/{tg}`. */
export interface BypassState {
  has_entity: boolean;
  used_bytes: number;
  limit_bytes: number;
  remaining_bytes: number;
  status: string | null;
}

export interface UserExtendedStats {
  renewals_count: number;
  reissues_count: number;
  total_spent_rubles: number;
  total_payments_count: number;
  first_paid_at: string | null;
  last_paid_at: string | null;
  referrer_telegram_id: number | null;
  referrer_username: string | null;
  referrals_invited_count: number;
  referrals_rewarded_count: number;
  traffic_gb_purchased_total: number;
  traffic_purchases_count: number;
}

/** A row of `pending_purchases`. */
export interface PurchaseRow {
  id?: number;
  purchase_id?: string | null;
  tariff?: string | null;
  purchase_type?: string | null;
  period_days?: number | null;
  price_kopecks?: number | null;
  price_rubles?: number | null;
  status?: string | null;
  created_at?: string | null;
  /** When the basket went stale — distinguishes "abandoned" from "failed". */
  expires_at?: string | null;
  promo_code?: string | null;
  is_combo?: boolean | null;
  country?: string | null;
  payment_provider?: string | null;
  /** Needed to reconcile a disputed payment with the provider. */
  provider_invoice_id?: string | null;
}

/** One entry of `subscription_history`. */
export interface SubscriptionHistoryRow {
  id?: number;
  action_type?: string | null;
  start_date?: string | null;
  end_date?: string | null;
  created_at?: string | null;
  [k: string]: unknown;
}

/** A match from `/users/search`. */
export interface UserSearchMatch {
  telegram_id: number;
  username: string | null;
  language: string | null;
  created_at: string | null;
  has_active_sub: boolean;
}

/** A row of `/users/list` — the listing the screen never had. */
export interface UserListRow {
  telegram_id: number;
  username: string | null;
  language: string | null;
  created_at: string | null;
  last_seen_at: string | null;
  is_reachable: boolean | null;
  has_active_sub: boolean;
  subscription_type: string | null;
  expires_at: string | null;
  source: string | null;
  balance_kopecks: number;
  referral_level: string | null;
}

export interface UserListResponse {
  rows: UserListRow[];
  total: number;
  limit: number;
  offset: number;
}

export interface UserListFilters {
  q?: string;
  has_sub?: boolean;
  source?: string;
  created_after?: string;
  created_before?: string;
  expires_before?: string;
  sort?: "created_at" | "expires_at" | "last_seen_at" | "balance";
  order?: "asc" | "desc";
  limit?: number;
  offset?: number;
}
