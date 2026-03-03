from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, func
from database.session import get_db
from database.models import Trade, BalanceHistory
from api.schemas import TradeResponse, BalanceResponse, BotStatus, PaginatedTradesResponse, BalanceHistoryResponse

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
    """Consulta el balance calculando el PNL en Dry Run o consultando a Binance en Real."""
    bot = get_bot_status()
    
    # --- CAMBIO: Llama a get_current_balance() en lugar de client.get_balance() ---
    balance = await bot.get_current_balance()
    
    if not balance:
        return {"total_balance": 0.0, "available_balance": 0.0, "unrealized_pnl": 0.0}
        
    return {
        "total_balance": balance.get('total', 0.0),
        "available_balance": balance.get('free', 0.0),
        "unrealized_pnl": 0.0 # Opcional: Futura implementación de PNL no realizado
    }

@router.get("/trades", response_model=PaginatedTradesResponse)
async def get_trades(
    page: int = Query(1, ge=1),
    per_page: int = Query(10, ge=1, le=100),
    filter_date: str = None, # Espera formato YYYY-MM-DD
    db: AsyncSession = Depends(get_db)
):
    """Obtiene el historial paginado y filtrado por fecha."""
    try:
        query = select(Trade).where(Trade.status == 'CLOSED') # Solo mostrar cerrados en el historial
        
        # 1. Aplicar filtro de fecha si el usuario seleccionó un día
        if filter_date:
            target_date = datetime.strptime(filter_date, "%Y-%m-%d").date()
            # Convertimos el timestamp de la BD a fecha para compararlo
            query = query.where(func.date(Trade.entry_time) == target_date)
            
        # 2. Contar el total de registros (para saber cuántas páginas hay)
        count_query = select(func.count()).select_from(query.subquery())
        total_result = await db.execute(count_query)
        total = total_result.scalar() or 0
        
        # 3. Aplicar paginación
        offset = (page - 1) * per_page
        query = query.order_by(desc(Trade.entry_time)).offset(offset).limit(per_page)
        
        result = await db.execute(query)
        trades = result.scalars().all()
        
        # 4. Calcular total de páginas
        total_pages = (total + per_page - 1) // per_page if total > 0 else 1
        
        return {
            "data": trades,
            "total": total,
            "page": page,
            "total_pages": total_pages
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/balance-history", response_model=list[BalanceHistoryResponse])
async def get_balance_history(db: AsyncSession = Depends(get_db)):
    """Obtiene los últimos 100 registros de balance para dibujar la gráfica."""
    try:
        # Obtenemos los más recientes primero para limitar a 100
        query = select(BalanceHistory).order_by(desc(BalanceHistory.timestamp)).limit(100)
        result = await db.execute(query)
        records = result.scalars().all()
        
        # Invertimos la lista para que la gráfica vaya de izquierda (viejo) a derecha (nuevo)
        return list(reversed(records))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))