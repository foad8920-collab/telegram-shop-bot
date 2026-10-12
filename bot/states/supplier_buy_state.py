"""FSM states for supplier product purchase."""
from aiogram.fsm.state import State, StatesGroup


class SupplierBuy(StatesGroup):
    """Purchase flow states for supplier products."""
    choosing_quantity = State()
    waiting_manual_quantity = State()
    choosing_payment = State()
    waiting_wallet_proof = State()
    # Admin states
    waiting_delivery_content = State()
    waiting_reject_reason = State()
