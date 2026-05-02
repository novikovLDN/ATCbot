"""
FSM state groups for handlers. Shared across domains.
"""
from aiogram.fsm.state import State, StatesGroup


class AdminUserSearch(StatesGroup):
    waiting_for_user_id = State()


class AdminReferralSearch(StatesGroup):
    waiting_for_search_query = State()


class BroadcastCreate(StatesGroup):
    waiting_for_title = State()
    waiting_for_test_type = State()
    waiting_for_message = State()
    waiting_for_message_a = State()
    waiting_for_message_b = State()
    waiting_for_emoji = State()
    waiting_for_buttons = State()
    waiting_for_discount = State()
    waiting_for_discount_duration = State()
    waiting_for_segment = State()
    waiting_for_confirm = State()


class AdminBroadcastNoSubscription(StatesGroup):
    waiting_for_text = State()
    waiting_for_confirmation = State()


class IncidentEdit(StatesGroup):
    waiting_for_text = State()


class AdminGrantAccess(StatesGroup):
    waiting_for_days = State()
    waiting_for_unit = State()
    waiting_for_value = State()
    waiting_for_notify = State()
    confirming = State()


class AdminGrantState(StatesGroup):
    """Flexible duration flow for «Выдать Basic» / «Выдать Plus»: amount → unit → confirm → notify."""
    waiting_amount = State()
    waiting_unit = State()
    waiting_confirm = State()
    waiting_notify = State()


class AdminRevokeAccess(StatesGroup):
    waiting_for_notify_choice = State()
    confirming = State()


class AdminDiscountCreate(StatesGroup):
    waiting_for_percent = State()
    waiting_for_expires = State()


class AdminTrafficDiscountCreate(StatesGroup):
    """Per-user discount on bypass GB purchases (separate from sub discount)."""
    waiting_for_percent = State()
    waiting_for_expires = State()


class AdminTrafficEdit(StatesGroup):
    waiting_for_amount = State()
    waiting_for_confirm = State()


class CorporateAccessRequest(StatesGroup):
    waiting_for_confirmation = State()


class PromoCodeInput(StatesGroup):
    waiting_for_promo = State()


class TopUpStates(StatesGroup):
    waiting_for_amount = State()


class AdminCreditBalance(StatesGroup):
    waiting_for_user_search = State()
    waiting_for_amount = State()
    waiting_for_confirmation = State()


class AdminDebitBalance(StatesGroup):
    waiting_for_amount = State()
    waiting_for_confirmation = State()


class AdminBalanceManagement(StatesGroup):
    waiting_for_user_search = State()


class WithdrawStates(StatesGroup):
    withdraw_amount = State()
    withdraw_confirm = State()
    withdraw_requisites = State()
    withdraw_final_confirm = State()


class AdminCreatePromocode(StatesGroup):
    waiting_for_code_name = State()
    waiting_for_duration_unit = State()
    waiting_for_duration_value = State()
    waiting_for_max_uses = State()
    waiting_for_discount_percent = State()
    confirm_creation = State()


class AdminCreateBypassGiftLink(StatesGroup):
    """FSM for admin gift-link creation: validity → GB → max_uses → confirm."""
    waiting_for_validity = State()
    waiting_for_gb = State()
    waiting_for_gb_custom = State()
    waiting_for_max_uses = State()
    waiting_for_max_uses_custom = State()
    waiting_for_confirm = State()


class AdminChat(StatesGroup):
    waiting_for_user_id = State()
    chatting = State()


class PurchaseState(StatesGroup):
    choose_tariff = State()
    choose_biz_tier = State()
    choose_country = State()
    choose_period = State()
    choose_payment_method = State()
    processing_payment = State()


class GiftState(StatesGroup):
    choose_tariff = State()
    choose_period = State()
    choose_payment_method = State()
    processing_payment = State()


class TelegramPremiumState(StatesGroup):
    waiting_for_username = State()
    choose_period = State()
    choose_payment_method = State()
    processing_payment = State()


class TelegramStarsState(StatesGroup):
    choose_pack = State()
    choose_recipient = State()
    waiting_for_username = State()
    choose_payment_method = State()
    processing_payment = State()


class BomberState(StatesGroup):
    playing = State()
