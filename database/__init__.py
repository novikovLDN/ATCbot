"""
Database package — backward-compatible re-export of all public symbols.

Split into submodules for maintainability:
- database.core         — Pool management, init, helpers, DB_READY (~1135 lines)
- database.users        — Users, balance, farm, referrals (~1680 lines)
- database.subscriptions — Subscriptions, payments, trials, access, promo (~4290 lines)
- database.admin        — Admin, analytics, broadcasts, exports, gifts, discounts (~2480 lines)

All existing code does `import database; database.get_user(...)` — this __init__.py
re-exports every public name so nothing breaks.
"""
import database.core as _core


def __getattr__(name):
    """Proxy mutable state reads to database.core."""
    if name == "DB_READY":
        return _core.DB_READY
    if name == "DATABASE_URL":
        return _core.DATABASE_URL
    raise AttributeError(f"module 'database' has no attribute {name!r}")


import sys as _sys


class _DatabaseModuleProxy(_sys.modules[__name__].__class__):
    """Allow `database.DB_READY = True` to propagate to database.core."""

    def __setattr__(self, name, value):
        if name == "DB_READY":
            _core.DB_READY = value
            return
        super().__setattr__(name, value)


_sys.modules[__name__].__class__ = _DatabaseModuleProxy


# Core: pool, helpers, init_db
# NOTE: DB_READY and DATABASE_URL are NOT imported here — they are proxied
# via __getattr__ above so that `database.DB_READY = True` in main.py works.
from database.core import (  # noqa: F401
    get_pool,
    close_pool,
    init_db,
    ensure_db_ready,
    check_critical_tables,
    safe_int,
    mark_payment_notification_sent,
    is_payment_notification_sent,
    # Internal helpers exposed for submodules and tests
    _to_db_utc,
    _from_db_utc,
    _ensure_utc,
    _generate_subscription_uuid,
    _normalize_subscription_row,
    _get_pool_config,
    _init_promo_codes,
)

# Users: user CRUD, balance, farm, referrals
from database.users import (  # noqa: F401
    get_user,
    mark_user_reachable,
    get_user_balance,
    increase_balance,
    decrease_balance,
    log_balance_transaction,
    get_farm_data,
    save_farm_plots,
    update_farm_plot_count,
    get_users_with_active_farm,
    search_users_dashboard,
    count_users_dashboard,
    list_users_dashboard,
    generate_referral_code,
    create_user,
    find_user_by_referral_code,
    get_user_referral_code,
    register_referral,
    mark_referral_active,
    _mark_referral_active_internal,
    get_referral_stats,
    get_referral_cashback_percent,
    get_cashback_fixed_percent,
    set_cashback_fixed_percent,
    clear_cashback_fixed_percent,
    get_effective_cashback_percent,
    get_referral_level_info,
    get_total_cashback_earned,
    get_referral_metrics,
    calculate_referral_level,
    get_referral_statistics,
    process_referral_reward,
    award_referral_cashback,
    update_user_language,
)

# Subscriptions: payments, subscriptions, trials, access, finalize, promo, reminders
from database.subscriptions import (  # noqa: F401
    get_pending_payment_by_user,
    get_payment,
    get_last_approved_payment,
    get_last_subscription_payment,
    check_and_disable_expired_subscription,
    get_subscription,
    get_subscription_any,
    admin_switch_tariff,
    has_trial_used,
    get_trial_info,
    get_active_paid_subscription,
    mark_trial_used,
    is_eligible_for_trial,
    is_trial_available,
    _log_audit_event_atomic,
    _log_vpn_lifecycle_audit_async,
    _log_subscription_history_atomic,
    _log_audit_event_atomic_standalone,
    grant_access,
    complete_activation,
    _calculate_subscription_days,
    approve_payment_atomic,
    get_pending_payments,
    get_subscriptions_needing_reminder,
    mark_reminder_sent,
    mark_reminder_flag_sent,
    mark_user_unreachable,
    remember_telegram_charge,
    telegram_charge_seen,
    update_last_reminder_at,
    get_promo_code,
    get_active_promo_by_code,
    check_promo_code_valid,
    log_promo_code_usage,
    get_promo_stats,
    create_promocode_atomic,
    deactivate_promocode,
    reactivate_promocode,
    _consume_promo_in_transaction,
    PurchaseAlreadyProcessed,
    validate_promocode_atomic,
    get_subscriptions_for_reminders,
    get_admin_stats,
    get_admin_referral_stats,
    get_admin_referral_detail,
    get_referral_overall_stats,
    get_referral_rewards_history,
    get_referral_rewards_history_count,
    calculate_final_price,
    set_special_offer,
    grant_expiry_special_offer,
    get_special_offer_info,
    create_pending_balance_topup_purchase,
    create_pending_purchase,
    has_purchased_proxy,
    mark_proxy_purchased,
    get_pending_purchase,
    get_pending_purchase_by_id,
    get_pending_purchase_any_status,
    cancel_pending_purchases,
    update_pending_purchase_invoice_id,
    mark_pending_purchase_paid,
    finalize_purchase,
    set_combo_flag,
    set_bypass_only_flag,
    ensure_bypass_only_subscription,
)

# Traffic: Remnawave integration, notifications, purchases
from database.traffic import (  # noqa: F401
    get_remnawave_uuid,
    set_remnawave_uuid,
    clear_remnawave_uuid,
    get_remnawave_id,
    set_remnawave_id,
    get_remnawave_premium_id,
    set_remnawave_premium_id,
    get_remnawave_premium_uuid,
    set_remnawave_premium_uuid,
    set_remnawave_premium_uuid_and_url,
    set_remnawave_premium_sub_url,
    set_remnawave_bypass_cache,
    replace_cached_sub_url,
    get_remnawave_bypass_cache,
    get_traffic_notification_flags,
    set_traffic_notification_flag,
    reset_traffic_notification_flags,
    get_traffic_notice_state,
    claim_traffic_notice_state,
    record_traffic_purchase,
    get_active_remnawave_users,
    get_user_traffic_discount,
    create_user_traffic_discount,
    delete_user_traffic_discount,
)

# Marketing links: stats-attribution + promo-redemption
from database.marketing_links import (  # noqa: F401
    VALID_PROMO_REWARD_TYPES,
    VALID_SUB_DAYS,
    VALID_DISCOUNT_PCTS,
    create_stats_link,
    list_stats_links,
    get_stats_link_by_slug,
    set_stats_link_active,
    delete_stats_link,
    record_stats_link_click,
    get_stats_link_summary,
    create_promo_link,
    list_promo_links,
    get_promo_link_by_slug,
    set_promo_link_active,
    delete_promo_link,
    try_redeem_promo_link,
    rollback_promo_link_redemption,
    get_promo_link_summary,
)

# Bypass gift links (admin-created GB redemption links)
from database.bypass_gift_links import (  # noqa: F401
    generate_bypass_gift_code,
    create_bypass_gift_link,
    get_bypass_gift_link_by_id,
    list_bypass_gift_links,
    get_bypass_gift_link_redemptions,
    count_bypass_gift_link_redemptions,
    soft_delete_bypass_gift_link,
    redeem_bypass_gift_link,
    rollback_bypass_gift_redemption,
    get_bypass_gift_links_summary,
)

# Farm storm: scheduling, shield, execution
from database.farm import (  # noqa: F401
    apply_farm_notification_flags,
    get_pending_storm,
    mark_storm_announced,
    mark_storm_executed,
    schedule_next_storm,
    list_users_with_growing_plots,
    apply_storm_shield_atomic,
    execute_storm_for_user,
    harvest_plot_atomic,
    buy_farm_plot_atomic,
    update_farm_plot_atomic,
    touch_last_seen,
    STORM_ANNOUNCE_BEFORE_HOURS,
    STORM_MIN_INTERVAL_DAYS,
    STORM_MAX_INTERVAL_DAYS,
)

# Admin: stats, broadcasts, analytics, exports, gifts, discounts
from database.admin import (  # noqa: F401
    expire_old_pending_purchases,
    get_all_users_for_export,
    get_active_subscriptions_for_export,
    get_subscription_history,
    get_user_extended_stats,
    get_business_metrics,
    get_last_audit_logs,
    create_broadcast,
    get_broadcast,
    save_broadcast_discount,
    save_broadcast_gift_reveal_percent,
    claim_broadcast_trial_key,
    release_broadcast_trial_key,
    get_broadcast_discount,
    get_analytics_by_period,
    get_active_paid_subscriptions_count,
    get_revenue_for_period,
    get_recent_payments_feed,
    get_user_purchases,
    get_traffic_stats,
    log_payment_error,
    get_recent_payment_errors,
    get_payment_errors_summary,
    get_purchase_breakdown,
    get_extended_bot_stats,
    get_eligible_no_subscription_broadcast_users,
    get_users_by_segment,
    count_users_by_segment,
    log_broadcast_send,
    get_broadcast_stats,
    get_recent_broadcasts,
    get_broadcast_message_ids,
    mark_broadcast_messages_deleted,
    get_incident_settings,
    set_incident_mode,
    admin_grant_access_atomic,
    finalize_balance_purchase,
    DuplicateBalancePurchase,
    finalize_balance_topup,
    admin_grant_access_minutes_atomic,
    admin_revoke_access_atomic,
    get_user_discount,
    create_user_discount,
    delete_user_discount,
    create_period_discount,
    get_period_discount,
    has_claimed_referral_share_discount,
    record_referral_share_discount_claim,
    get_hourly_timeseries,
    get_daily_summary,
    admin_delete_user_complete,
    generate_gift_code,
    create_gift_subscription,
    activate_gift_subscription,
    get_user_gifts,
    # Dashboard routes call these through the package (routes/broadcasts.py
    # tag + analytics, routes/payments.py /breakdown). Dropped by dead-code
    # round 2 because nothing inside the bot imports them; restored — see
    # tests/api/test_dashboard_db_refs.py.
    update_broadcast_tag,
    get_broadcast_analytics,
    get_payments_breakdown,
)

# Scheduled + recurring broadcasts (migration 067)
from database.scheduled_broadcasts import (  # noqa: F401
    VALID_RECURRENCES,
    create_scheduled_broadcast,
    list_scheduled_broadcasts,
    get_scheduled_broadcast,
    cancel_scheduled_broadcast,
    mark_ran_and_reschedule,
    claim_scheduled_run,
    record_scheduled_result,
    fetch_due_scheduled,
)


# Subscription reconciliation & over-issuance watchdog
from database.reconciliation import (  # noqa: F401
    find_over_issuance_candidates,
    get_reconciliation_detail,
    apply_reconciliation_fix,
    list_reconciliation_log,
    list_over_issuance_log,
    record_over_issuance,
)
