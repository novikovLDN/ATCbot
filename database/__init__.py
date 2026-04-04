"""
Database package — backward-compatible re-export of all public symbols.

Split into submodules for maintainability:
- database.core         — Pool management, init, helpers, DB_READY (~1135 lines)
- database.users        — Users, balance, farm, withdrawals, referrals (~1680 lines)
- database.subscriptions — Subscriptions, payments, trials, access, promo (~4290 lines)
- database.admin        — Admin, analytics, broadcasts, exports, gifts, VIP (~2480 lines)

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
    safe_float,
    safe_get,
    mark_payment_notification_sent,
    is_payment_notification_sent,
    # Internal helpers exposed for submodules and tests
    _to_db_utc,
    _from_db_utc,
    _ensure_utc,
    _generate_subscription_uuid,
    _normalize_subscription_row,
    _get_pool_config,
    _get_pool_safe,
    _init_promo_codes,
)

# Users: user CRUD, balance, farm, withdrawals, referrals
from database.users import (  # noqa: F401
    get_user,
    get_user_balance,
    increase_balance,
    decrease_balance,
    log_balance_transaction,
    get_farm_data,
    save_farm_plots,
    update_farm_plot_count,
    get_users_with_active_farm,
    create_withdrawal_request,
    get_withdrawal_request,
    approve_withdrawal_request,
    reject_withdrawal_request,
    find_user_by_id_or_username,
    generate_referral_code,
    create_user,
    find_user_by_referral_code,
    get_user_referral_code,
    register_referral,
    mark_referral_active,
    _mark_referral_active_internal,
    get_referral_stats,
    get_referral_cashback_percent,
    calculate_referral_percent,
    get_referral_level_info,
    get_total_cashback_earned,
    get_referral_metrics,
    calculate_referral_level,
    get_referral_statistics,
    process_referral_reward,
    update_user_language,
    update_username,
)

# Subscriptions: payments, subscriptions, trials, access, finalize, promo, reminders
from database.subscriptions import (  # noqa: F401
    get_pending_payment_by_user,
    create_payment,
    get_payment,
    get_last_approved_payment,
    update_payment_status,
    check_and_disable_expired_subscription,
    get_subscription,
    get_subscription_any,
    admin_switch_tariff,
    has_any_subscription,
    has_any_payment,
    has_trial_used,
    get_trial_info,
    get_active_paid_subscription,
    mark_trial_used,
    is_eligible_for_trial,
    is_trial_available,
    get_active_subscription,
    update_subscription_uuid,
    get_all_active_subscriptions,
    reissue_subscription_key,
    _log_audit_event_atomic,
    _log_vpn_lifecycle_audit_async,
    _log_vpn_lifecycle_audit_fire_and_forget,
    _log_subscription_history_atomic,
    _log_audit_event_atomic_standalone,
    reissue_vpn_key_atomic,
    grant_access,
    _calculate_subscription_days,
    approve_payment_atomic,
    get_pending_payments,
    get_subscriptions_needing_reminder,
    mark_reminder_sent,
    mark_reminder_flag_sent,
    mark_user_unreachable,
    update_last_reminder_at,
    get_promo_code,
    get_active_promo_by_code,
    has_active_promo,
    check_promo_code_valid,
    log_promo_code_usage,
    get_promo_stats,
    generate_promo_code,
    create_promocode_atomic,
    deactivate_promocode,
    _consume_promo_in_transaction,
    validate_promocode_atomic,
    consume_promocode_atomic,
    is_user_first_purchase,
    get_subscriptions_for_reminders,
    get_admin_stats,
    get_admin_referral_stats,
    get_admin_referral_detail,
    get_referral_overall_stats,
    get_referral_rewards_history,
    get_referral_rewards_history_count,
    calculate_final_price,
    set_special_offer,
    get_special_offer_info,
    has_active_special_offer,
    create_pending_balance_topup_purchase,
    create_pending_purchase,
    get_pending_purchase,
    get_pending_purchase_by_id,
    cancel_pending_purchases,
    update_pending_purchase_invoice_id,
    mark_pending_purchase_paid,
    finalize_purchase,
)

# Traffic: Remnawave integration, notifications, purchases
from database.traffic import (  # noqa: F401
    get_remnawave_uuid,
    set_remnawave_uuid,
    set_remnawave_short_uuid,
    get_remnawave_short_uuid,
    clear_remnawave_uuid,
    get_traffic_notification_flags,
    set_traffic_notification_flag,
    reset_traffic_notification_flags,
    record_traffic_purchase,
    get_active_remnawave_users,
)

# Admin: stats, broadcasts, analytics, exports, gifts, VIP, discounts
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
    get_broadcast_discount,
    get_analytics_by_period,
    get_extended_bot_stats,
    get_all_users_telegram_ids,
    get_eligible_no_subscription_broadcast_users,
    check_user_still_eligible_for_no_sub_broadcast,
    insert_admin_broadcast_record,
    update_admin_broadcast_record,
    get_users_by_segment,
    log_broadcast_send,
    get_broadcast_stats,
    get_ab_test_broadcasts,
    get_incident_settings,
    set_incident_mode,
    get_ab_test_stats,
    admin_grant_access_atomic,
    finalize_balance_purchase,
    finalize_balance_topup,
    admin_grant_access_minutes_atomic,
    admin_revoke_access_atomic,
    get_user_discount,
    create_user_discount,
    delete_user_discount,
    is_vip_user,
    grant_vip_status,
    revoke_vip_status,
    get_total_revenue,
    get_paying_users_count,
    get_user_ltv,
    get_average_ltv,
    get_arpu,
    get_ltv,
    get_referral_analytics,
    get_daily_summary,
    get_monthly_summary,
    admin_delete_user_complete,
    generate_gift_code,
    create_gift_subscription,
    get_gift_subscription,
    activate_gift_subscription,
    get_user_gifts,
)
