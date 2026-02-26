from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, ForeignKey
from datetime import datetime, timezone
from sqlalchemy.orm import relationship 
from database.session import Base

class BalanceHistory(Base):
    __tablename__ = "balance_history"
    
    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    total_balance = Column(Float, nullable=False)
    available_balance = Column(Float, nullable=False)
    unrealized_pnl = Column(Float, nullable=False)
    drawdown_percent = Column(Float, default=0.0)

class Trade(Base):
    __tablename__ = "trades"
    
    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String)
    side = Column(String)
    entry_price = Column(Float)
    exit_price = Column(Float, nullable=True)
    quantity = Column(Float)
    stop_loss = Column(Float)
    take_profit = Column(Float)
    atr_at_entry = Column(Float)
    binance_order_id = Column(String)
    binance_sl_id = Column(String, nullable=True)
    binance_tp_id = Column(String, nullable=True)
    is_break_even = Column(Boolean, default=False)
    is_trailing = Column(Boolean, default=False)
    status = Column(String)
    realized_pnl = Column(Float, nullable=True)
    roe_percent = Column(Float, nullable=True)
    
    # Usamos datetime.utcnow sin el (timezone.utc) para que sea naive
    entry_time = Column(DateTime, default=datetime.utcnow)
    exit_time = Column(DateTime, nullable=True)

class AIPrediction(Base):
    __tablename__ = "ai_predictions"
    
    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    symbol = Column(String, index=True)
    predicted_direction = Column(String) # 'UP', 'DOWN', 'NEUTRAL'
    confidence_score = Column(Float, nullable=False)
    actual_outcome = Column(String, nullable=True) # Usado para re-entrenamiento

class TradeSLHistory(Base):
    __tablename__ = "trade_sl_history"
    
    id = Column(Integer, primary_key=True, index=True)
    trade_id = Column(Integer, ForeignKey("trades.id"), nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow)
    
    event_type = Column(String) # 'BREAK_EVEN' o 'TRAILING_STOP'
    old_sl = Column(Float)
    new_sl = Column(Float)
    price_at_event = Column(Float) # A qué precio estaba el mercado cuando se movió el SL
    
    # Relación opcional para facilitar consultas
    trade = relationship("Trade", backref="sl_history")