from pydantic import BaseModel
from datetime import datetime
from typing import Optional

class TradeResponse(BaseModel):
    id: int
    symbol: str
    side: str
    entry_price: float
    exit_price: Optional[float] = None
    quantity: float
    pnl: Optional[float] = None
    roe: Optional[float] = None
    status: str
    entry_time: datetime
    exit_time: Optional[datetime] = None

    class Config:
        from_attributes = True

class BalanceResponse(BaseModel):
    total_balance: float
    available_balance: float
    unrealized_pnl: float

# --- NUEVA CLASE (Debe ir ANTES de BotStatus) ---
class OpenTradeInfo(BaseModel):
    entry_price: float
    stop_loss: float
    take_profit: float
    pnl: float
    roe: float
    atr: float
    is_trailing: bool

class AssetStatus(BaseModel):
    symbol: str
    current_price: float
    is_in_position: bool
    position_type: Optional[str] = None
    ai_prediction: str
    ai_confidence: float
    open_trade: Optional[OpenTradeInfo] = None

# --- CLASE GLOBAL ACTUALIZADA ---
class BotStatus(BaseModel):
    is_running: bool
    global_open_trades: int
    max_open_trades: int
    ai_threshold: float
    assets: list[AssetStatus] # Ahora es una lista de todos los activos escaneados