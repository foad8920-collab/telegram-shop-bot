"""FSM states for the local subscriptions purchase flow."""
from aiogram.fsm.state import State, StatesGroup


class LocalBuy(StatesGroup):
    """Purchase flow states."""
    choosing_quantity = State()       #  user picks quantity
    waiting_manual_quantity = State() #  user types custom quantity
    choosing_payment = State()        #  user picks payment method
    waiting_binance_code = State() #  user sends Binance reference
    waiting_wallet_proof = State() #  user sends wallet receipt photo
    waiting_customer_data = State() #  user sends required data (email/phone/etc.)
    confirming = State()           #  final confirmation (optional)

class AdminOrder(StatesGroup):
    """Admin-side FSM for sending free-form instructions to the buyer."""
    awaiting_instructions = State()

