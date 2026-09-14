"""
FSM state groups for handlers. Shared across domains.
"""
from aiogram.fsm.state import State, StatesGroup


class PromoCodeInput(StatesGroup):
    waiting_for_promo = State()


class TopUpStates(StatesGroup):
    waiting_for_amount = State()


class AdminChat(StatesGroup):
    waiting_for_user_id = State()
    chatting = State()


class PurchaseState(StatesGroup):
    choose_tariff = State()
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


class SteamPurchaseState(StatesGroup):
    """Shop: «Пополнить Steam». Disclaimer → amount → login → payment."""
    waiting_for_disclaimer_ack = State()  # noop placeholder; ack is a button click
    choose_amount = State()
    waiting_for_login = State()
    choose_payment_method = State()
    processing_payment = State()


class SpotifyPurchaseState(StatesGroup):
    """Shop: Spotify Premium (EG-регион).

    Disclaimer → info → choose_plan → choose_duration →
    email input → email confirm → password input → password confirm →
    review → payment_method → processing.
    """
    waiting_for_email = State()
    confirming_email = State()
    waiting_for_password = State()
    confirming_password = State()
    reviewing = State()
    choose_payment_method = State()
    processing_payment = State()


class BomberState(StatesGroup):
    playing = State()
