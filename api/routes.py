from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from database.session import get_db
from database.models import Trade
from api.schemas import TradeResponse, BalanceResponse, BotStatus

router = APIRouter()

# Variable global para acceder al estado del bot desde la API
# Se inyectará desde main.py
bot_instance = None 

def get_bot_status():
    if bot_instance is None:
        raise HTTPException(status_code=503, detail="Bot no inicializado")
    return bot_instance

@router.get("/status", response_model=BotStatus)
async def get_status():
    bot = get_bot_status()
    
    # Contar operaciones globales activas
    active_count = sum(1 for asset in bot.assets.values() if asset.is_in_position)
    
    asset_list =[]
    
    # Recorrer todos los activos monitoreados
    for symbol, asset in bot.assets.items():
        open_trade_info = None
        position_type = None
        
        if asset.is_in_position and asset.current_trade:
            t = asset.current_trade
            position_type = t.side
            
            # Cálculo de PNL en tiempo real
            if t.side == 'LONG': pnl = (asset.current_price - t.entry_price) * t.quantity
            else: pnl = (t.entry_price - asset.current_price) * t.quantity
            
            roe = (pnl / (t.entry_price * t.quantity)) * 100 if t.entry_price > 0 else 0
            
            open_trade_info = {
                "entry_price": t.entry_price,
                "stop_loss": t.stop_loss,
                "take_profit": t.take_profit,
                "pnl": round(pnl, 2),
                "roe": round(roe, 2),
                "atr": t.atr_at_entry,
                "is_trailing": t.is_trailing
            }

        asset_list.append({
            "symbol": asset.symbol,
            "current_price": asset.current_price,
            "is_in_position": asset.is_in_position,
            "position_type": position_type,
            "ai_prediction": asset.latest_prediction,
            "ai_confidence": asset.latest_confidence,
            "open_trade": open_trade_info
        })

    return {
        "is_running": True,
        "global_open_trades": active_count,
        "max_open_trades": bot.max_open_trades,
        "ai_threshold": bot.ai_threshold,
        "assets": asset_list
    }

@router.get("/balance", response_model=BalanceResponse)
async def get_balance():
    """Consulta el balance directamente a Binance a través del cliente del bot."""
    bot = get_bot_status()
    balance = await bot.client.get_balance()
    
    if not balance:
        return {"total_balance": 0.0, "available_balance": 0.0, "unrealized_pnl": 0.0}
        
    return {
        "total_balance": balance.get('total', 0.0),
        "available_balance": balance.get('free', 0.0),
        "unrealized_pnl": 0.0 # En futuro real se calcula con posiciones abiertas
    }

@router.get("/trades", response_model=list[TradeResponse])
async def get_trades(limit: int = 10, db: AsyncSession = Depends(get_db)):
    """Obtiene el historial de trades cerrados desde la base de datos."""
    try:
        query = select(Trade).order_by(desc(Trade.entry_time)).limit(limit)
        result = await db.execute(query)
        trades = result.scalars().all()
        return trades
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))