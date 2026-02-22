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
    waiting_for_type = State()
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


class PurchaseState(StatesGroup):
    choose_tariff = State()
    choose_period = State()
    choose_payment_method = State()
    processing_payment = State()


class BomberState(StatesGroup):
    playing = State()
