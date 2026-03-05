from sqlalchemy import select, func
import os
import asyncio
from datetime import datetime, timezone
from database.session import AsyncSessionLocal
from database.models import Trade, TradeSLHistory, BalanceHistory
from connectors.binance_futures import BinanceFuturesClient
from connectors.binance_ws import BinanceWebSocket
from connectors.market_scanner import MarketScanner
from ta_engine.indicators import TAEngine
from ai_engine.model import AIPredictor
from risk_manager.risk_engine import RiskManager
from dotenv import load_dotenv
import traceback

load_dotenv()

class AssetState:
    def __init__(self, symbol: str, timeframe: str):
        self.symbol = symbol
        self.ws_symbol = symbol.replace('/', '').lower()
        self.ai = AIPredictor(symbol, timeframe)
        self.is_in_position = False
        self.trade_in_progress = False
        self.current_trade = None
        self.current_price = 0.0
        self.latest_prediction = "ESPERANDO"
        self.latest_confidence = 0.0
        self.last_atr = 0.0

class BotCore:
    def __init__(self):
        # Parámetros Globales
        self.timeframe = os.getenv("TIMEFRAME", "1h")
        self.strategy_interval = int(os.getenv("STRATEGY_INTERVAL_SECONDS", "5"))
        self.ai_threshold = float(os.getenv("AI_CONFIDENCE_THRESHOLD", "47.0"))
        self.training_limit = int(os.getenv("AI_TRAINING_LIMIT", "15000"))
        self.retrain_hours = int(os.getenv("AI_RETRAIN_INTERVAL_HOURS", "6"))
        
        # Parámetros Multi-Activo
        self.max_monitored_assets = int(os.getenv("MAX_MONITORED_ASSETS", "10"))
        self.max_open_trades = int(os.getenv("MAX_OPEN_TRADES", "3"))

        # Riesgo y Trailing Stop Agresivo
        risk_per_trade = float(os.getenv("RISK_PER_TRADE", "0.01"))
        risk_reward = float(os.getenv("RISK_REWARD_RATIO", "2.0"))
        self.be_trigger_r = float(os.getenv("BREAK_EVEN_TRIGGER_R", "1.0"))
        self.ts_trigger_r = float(os.getenv("TRAILING_STOP_TRIGGER_R", "1.5"))
        self.be_plus_percent = float(os.getenv("BE_PLUS_PERCENT", "0.05"))
        self.agg_ts_trigger_r = float(os.getenv("AGGRESSIVE_TS_TRIGGER_R", "3.0"))
        self.agg_ts_percent = float(os.getenv("AGGRESSIVE_TS_PERCENT", "0.20"))

        # Módulos Compartidos
        self.scanner = MarketScanner()
        self.client = BinanceFuturesClient()
        self.ws = BinanceWebSocket()
        self.ta = TAEngine()
        self.risk = RiskManager(risk_per_trade=risk_per_trade, risk_reward_ratio=risk_reward)
        
        # Estado Global
        self.assets = {} 
        self.trade_lock = asyncio.Lock() 
        self.recovered_trades = {}
        self.is_ready = False

    async def get_current_balance(self):
        if self.client.environment == 'dry_run':
            initial_balance = float(os.getenv("DRY_RUN_INITIAL_BALANCE", "1000.0"))
            async with AsyncSessionLocal() as session:
                query = select(func.sum(Trade.realized_pnl)).where(Trade.status == 'CLOSED')
                result = await session.execute(query)
                accumulated_pnl = result.scalar() or 0.0
                current = initial_balance + accumulated_pnl
                return {"total": current, "free": current}
        else:
            return await self.client.get_balance()

    async def start(self):
        try:
            print(f"\n🚀 Iniciando Core Engine MULTI-ACTIVO (Max {self.max_open_trades} trades concurrentes)...")
            
            if hasattr(self.client, 'setup_account'):
                await self.client.setup_account()
            
            try:
                print("⏱️ Sincronizando reloj con Binance...")
                await self.client.exchange.load_time_difference()
            except Exception as e:
                print(f"⚠️ Aviso de sincronización: {e}")

            # 1. Recuperar y Limpiar Estado
            await self._recover_state()
            
            # 2. Escanear Mercado
            top_symbols = await self.scanner.get_top_assets(limit=self.max_monitored_assets)
            if not top_symbols:
                print("🛑 No se pudieron obtener activos del escáner.")
                return

            for sym in self.recovered_trades.keys():
                if sym not in top_symbols:
                    top_symbols.append(sym)
                    print(f"   ⚓ Anclando {sym} al escáner por recuperación de trade activo.")

            # 3. Entrenamiento y Purgado (SOLUCIÓN 1: Auto-Limpieza)
            print("\n🧠 Iniciando fase de Entrenamiento Masivo (Esto puede tomar varios minutos)...")
            for sym in top_symbols:
                self.assets[sym] = AssetState(sym, self.timeframe)
                
                if sym in self.recovered_trades:
                    self.assets[sym].is_in_position = True
                    self.assets[sym].current_trade = self.recovered_trades[sym]
                
                success = await self._prepare_ai(sym)
                
                # Si falla por falta de datos (Ej: Moneda nueva), se ELIMINA de la memoria
                if not success:
                    print(f"🧹 Purgando {sym} de la memoria por falta de datos históricos.")
                    del self.assets[sym]
            
            print("\n🔓 Todo el estado ha sido validado. Quitanto candado de seguridad.")
            self.is_ready = True
            print("✅ Todas las IAs listas. Desplegando redes de monitoreo...")

            # 4. Arrancar bucles
            tasks =[
                asyncio.create_task(self._balance_snapshot_loop())
            ]
            for sym in self.assets.keys():
                asset = self.assets[sym]
                tasks.append(asyncio.create_task(self.ws.subscribe_ticker(asset.ws_symbol, lambda s, p, sym=sym: self._on_price_update(sym, p))))
                tasks.append(asyncio.create_task(self._strategy_loop(sym)))
            
            await asyncio.gather(*tasks)
            
        except Exception as e:
            print(f"\n🚨 ERROR FATAL EN EL NÚCLEO DEL BOT 🚨")
            traceback.print_exc()

    async def _recover_state(self):
        """Lee la BD, ELIMINA DUPLICADOS, y concilia con Binance (SOLUCIÓN 3)."""
        print("\n🔄 Iniciando Módulo de Recuperación de Estado...")
        async with AsyncSessionLocal() as session:
            query = select(Trade).where(Trade.status == 'OPEN')
            result = await session.execute(query)
            open_trades = result.scalars().all()

            if not open_trades:
                print("   ✅ No hay operaciones huérfanas en BD. Estado limpio.")
                return

            print(f"   🔎 Encontradas {len(open_trades)} operaciones OPEN en Base de Datos.")

            # Agrupar trades por símbolo para detectar fantasmas/duplicados
            trades_by_symbol = {}
            for t in open_trades:
                if t.symbol not in trades_by_symbol:
                    trades_by_symbol[t.symbol] =[]
                trades_by_symbol[t.symbol].append(t)

            live_positions = await self.client.get_open_positions()

            for sym, trades in trades_by_symbol.items():
                # Ordenar cronológicamente (el más nuevo al final)
                trades.sort(key=lambda x: x.entry_time)
                
                # Si hay duplicados, cerrar los viejos (Ghost Trades)
                if len(trades) > 1:
                    print(f"   🧹 Detectados {len(trades)} trades para {sym}. Limpiando {len(trades)-1} duplicados...")
                    for old_trade in trades[:-1]:
                        old_trade.status = 'CLOSED'
                        old_trade.exit_time = datetime.utcnow()
                        old_trade.exit_price = old_trade.entry_price # Salida plana
                        old_trade.realized_pnl = 0.0
                        old_trade.roe_percent = 0.0
                
                # Quedarnos solo con el más reciente
                active_trade = trades[-1]
                is_alive = False

                if self.client.environment == 'dry_run':
                    is_alive = True
                else:
                    if sym in live_positions:
                        is_alive = True
                    else:
                        print(f"   ⚠️ Posición {sym} ya no existe en Binance. Cerrando...")
                        active_trade.status = 'CLOSED'
                        active_trade.exit_time = datetime.utcnow()
                        active_trade.exit_price = active_trade.stop_loss 
                        is_long = active_trade.side == 'LONG'
                        pnl = (active_trade.exit_price - active_trade.entry_price) * active_trade.quantity if is_long else (active_trade.entry_price - active_trade.exit_price) * active_trade.quantity
                        active_trade.realized_pnl = round(pnl, 2)
                        active_trade.roe_percent = round((pnl / (active_trade.entry_price * active_trade.quantity)) * 100, 2)

                if is_alive:
                    print(f"   ✅ Restaurando {sym} en memoria (Entrada: ${active_trade.entry_price})...")
                    session.expunge(active_trade)
                    self.recovered_trades[sym] = active_trade

            await session.commit()

    async def _balance_snapshot_loop(self):
        print("📸 Snapshot de balance iniciado (Guardando histórico cada 15 min).")
        while True:
            try:
                balance_info = await self.get_current_balance()
                if balance_info:
                    async with AsyncSessionLocal() as session:
                        snapshot = BalanceHistory(
                            total_balance=float(balance_info['total']),
                            available_balance=float(balance_info['free']),
                            unrealized_pnl=0.0,
                            timestamp=datetime.utcnow()
                        )
                        session.add(snapshot)
                        await session.commit()
            except Exception as e:
                pass
            await asyncio.sleep(900)

    async def _prepare_ai(self, symbol: str) -> bool:
        print(f"   ➤ Descargando historial para {symbol}...")
        klines = await self.client.get_historical_klines(symbol, self.timeframe, limit=self.training_limit)
        
        if not klines or len(klines) < 50: return False

        df = self.ta.apply_indicators(self.ta.prepare_dataframe(klines))
        if df.empty or len(df) < 50: return False

        try:
            self.assets[symbol].ai.predict(df)
            print(f"      [{symbol}] IA cargada desde disco.")
        except:
            print(f"      [{symbol}] Entrenando nuevo modelo...")
            await asyncio.to_thread(self.assets[symbol].ai.train, df)
            
        return True

    async def _on_price_update(self, symbol: str, price: float):
        asset = self.assets[symbol]
        asset.current_price = float(price)
        if asset.is_in_position and asset.current_trade:
            await self._monitor_advanced_position(symbol)

    async def _strategy_loop(self, symbol: str):
        asset = self.assets[symbol]
        while True:
            try:
                if not self.is_ready:
                    await asyncio.sleep(1)
                    continue
                
                if not asset.is_in_position:
                    klines = await self.client.get_historical_klines(symbol, self.timeframe, limit=200)
                    if klines:
                        df = self.ta.apply_indicators(self.ta.prepare_dataframe(klines))
                        
                        if not df.empty:
                            # --- SOLUCIÓN 2: PRECIO DE RESPALDO (Fallback) ---
                            # Si el WS está dormido, usamos el cierre de la vela reciente
                            if asset.current_price == 0.0:
                                asset.current_price = float(df['close'].iloc[-1])
                                
                            pred, conf = asset.ai.predict(df)
                            direction = 'LONG' if pred == 1 else 'SHORT' if pred == -1 else 'NEUTRAL'
                            
                            asset.latest_prediction = direction
                            asset.latest_confidence = conf
                            asset.last_atr = float(df['ATRr_14'].iloc[-1])

                            import sys
                            hora_actual = datetime.now().strftime('%H:%M:%S')
                            sys.stdout.write(f"\r[{hora_actual}] 📡 Radar escaneando {len(self.assets)} activos... ")
                            sys.stdout.flush()

                            if direction in['LONG', 'SHORT'] and conf >= self.ai_threshold:
                                print("\n")
                                await self._execute_trade(symbol, direction, df)
            except Exception as e:
                pass
            
            await asyncio.sleep(self.strategy_interval)

    async def _retraining_loop(self):
        while True:
            await asyncio.sleep(self.retrain_hours * 3600)
            print(f"\n♻️  INICIANDO REENTRENAMIENTO AUTOMÁTICO...")
            for sym, asset in self.assets.items():
                try:
                    klines = await self.client.get_historical_klines(sym, self.timeframe, limit=self.training_limit)
                    if klines:
                        df = self.ta.apply_indicators(self.ta.prepare_dataframe(klines))
                        if not df.empty:
                            await asyncio.to_thread(asset.ai.train, df)
                            print(f"   ✅ {sym} Reentrenado.")
                except Exception:
                    pass

    async def _execute_trade(self, symbol: str, direction: str, df):
        asset = self.assets[symbol]
        
        async with self.trade_lock:
            current_open_trades = sum(1 for a in self.assets.values() if a.is_in_position or a.trade_in_progress)
            if current_open_trades >= self.max_open_trades: return
            asset.trade_in_progress = True

        try:
            balance_info = await self.get_current_balance()
            balance = float(balance_info['total']) if balance_info else 1000.0
            
            theoretical_price = float(df['close'].iloc[-1])
            atr = float(df['ATRr_14'].iloc[-1])
            sl, tp = self.risk.calculate_sl_tp(direction, theoretical_price, atr)
            
            if direction == 'LONG' and sl >= theoretical_price: sl = theoretical_price - (atr * 1.5)
            elif direction == 'SHORT' and sl <= theoretical_price: sl = theoretical_price + (atr * 1.5)
            
            size = float(self.risk.calculate_position_size(balance, theoretical_price, sl))
            if size <= 0: return

            print(f"\n⚡ SEÑAL [{symbol}]: {direction} | Confianza: {asset.latest_confidence:.2f}%")
            side = 'BUY' if direction == 'LONG' else 'SELL'
            
            results = await self.client.place_atomic_trade(symbol, side, size, sl, tp)
            
            if results and isinstance(results, list) and len(results) >= 1:
                entry_res = results[0]
                if 'code' in entry_res:
                    print(f"🚨 Error de Binance en {symbol}: {entry_res}")
                    return

                main_id = str(entry_res.get('orderId'))
                await asyncio.sleep(0.6) 
                
                real_order = await self.client.fetch_order_details(symbol, main_id)
                if real_order and real_order.get('average'): real_entry_price = float(real_order['average'])
                else: real_entry_price = float(entry_res.get('avgPrice', theoretical_price))
                if real_entry_price <= 0: real_entry_price = theoretical_price

                sl_real, tp_real = self.risk.calculate_sl_tp(direction, real_entry_price, atr)

                sl_res = next((o for o in results if o.get('type') in ['STOP', 'STOP_MARKET']), None)
                tp_res = next((o for o in results if o.get('type') in['TAKE_PROFIT', 'TAKE_PROFIT_MARKET']), None)

                async with AsyncSessionLocal() as session:
                    new_trade = Trade(
                        symbol=symbol, side=direction, entry_price=real_entry_price,
                        quantity=size, stop_loss=float(sl_real), take_profit=float(tp_real), atr_at_entry=float(atr),
                        binance_order_id=main_id,
                        binance_sl_id=str(sl_res.get('orderId')) if sl_res else None,
                        binance_tp_id=str(tp_res.get('orderId')) if tp_res else None,
                        status='OPEN', entry_time=datetime.utcnow()
                    )
                    session.add(new_trade)
                    await session.commit()
                    await session.refresh(new_trade)
                    
                    asset.current_trade = new_trade
                    asset.is_in_position = True
                    print(f"✅ TRADE SINCRONIZADO [{symbol}]: Entrada a ${real_entry_price}")
        except Exception as e:
            print(f"❌ Error ejecutando trade para {symbol}: {e}")
        finally:
            asset.trade_in_progress = False

    async def _monitor_advanced_position(self, symbol: str):
        asset = self.assets[symbol]
        trade = asset.current_trade
        if not trade: return
        
        p = asset.current_price
        entry = trade.entry_price
        atr = trade.atr_at_entry
        sl_distance = abs(entry - trade.stop_loss)
        
        is_long = trade.side == 'LONG'
        current_profit = (p - entry) if is_long else (entry - p)
        r_multiple = current_profit / sl_distance if sl_distance > 0 else 0

        async with AsyncSessionLocal() as session:
            db_trade = await session.get(Trade, trade.id)
            if not db_trade or db_trade.status != 'OPEN': return

            # Nivel 1: BE + Fees
            if not db_trade.is_break_even and r_multiple >= self.be_trigger_r:
                old_sl = db_trade.stop_loss
                plus = atr * self.be_plus_percent
                new_sl = float(entry + plus) if is_long else float(entry - plus)
                
                print(f"🛡️ [{symbol}] Break Even (+Fees). SL: {old_sl} -> {new_sl}")
                if db_trade.binance_sl_id:
                    await self.client.cancel_order(symbol, db_trade.binance_sl_id)
                    sl_res, _ = await self.client.place_sl_tp(symbol, db_trade.side, db_trade.quantity, new_sl, db_trade.take_profit)
                    if sl_res: db_trade.binance_sl_id = str(sl_res.get('orderId'))
                
                session.add(TradeSLHistory(trade_id=db_trade.id, event_type='BREAK_EVEN', old_sl=old_sl, new_sl=new_sl, price_at_event=p))
                db_trade.stop_loss, db_trade.is_break_even = new_sl, True
                await session.commit()
                asset.current_trade = db_trade

            # Nivel 2: Activar Trailing
            if not db_trade.is_trailing and r_multiple >= self.ts_trigger_r:
                print(f"🚀 [{symbol}] Trailing Stop Activado.")
                if db_trade.binance_tp_id: await self.client.cancel_order(symbol, db_trade.binance_tp_id)
                db_trade.is_trailing, db_trade.take_profit = True, 0.0
                await session.commit()

            # Nivel 3: Trailing (ATR vs Agresivo)
            if db_trade.is_trailing:
                if r_multiple >= self.agg_ts_trigger_r:
                    distancia = abs(p - entry) * self.agg_ts_percent
                    event_type = 'TRAILING_AGGRESSIVE'
                else:
                    distancia = atr * 1.0 
                    event_type = 'TRAILING_ATR'

                potential_ts = p - distancia if is_long else p + distancia
                move_sl = False
                
                if is_long and potential_ts > db_trade.stop_loss: move_sl = True
                elif not is_long and potential_ts < db_trade.stop_loss: move_sl = True

                if move_sl:
                    old_sl, new_sl = db_trade.stop_loss, float(potential_ts)
                    print(f"📈[{symbol}] Ajuste {event_type}. SL: {old_sl} -> {new_sl}")

                    if db_trade.binance_sl_id:
                        await self.client.cancel_order(symbol, db_trade.binance_sl_id)
                        sl_res, _ = await self.client.place_sl_tp(symbol, db_trade.side, db_trade.quantity, new_sl, 0)
                        if sl_res: db_trade.binance_sl_id = str(sl_res.get('orderId'))

                    session.add(TradeSLHistory(trade_id=db_trade.id, event_type=event_type, old_sl=old_sl, new_sl=new_sl, price_at_event=p))
                    db_trade.stop_loss = new_sl
                    await session.commit()
                    asset.current_trade = db_trade

            # Soft Stop
            close, reason = False, ""
            if is_long:
                if p <= db_trade.stop_loss: close, reason = True, "Stop Loss / Trailing"
                elif db_trade.take_profit > 0 and p >= db_trade.take_profit: close, reason = True, "Take Profit"
            else:
                if p >= db_trade.stop_loss: close, reason = True, "Stop Loss / Trailing"
                elif db_trade.take_profit > 0 and p <= db_trade.take_profit: close, reason = True, "Take Profit"

            if close:
                print(f"🔔 [{symbol}] CIERRE ({reason}) a ${p}")
                exit_side = 'SELL' if is_long else 'BUY'
                await self.client.place_order(symbol, exit_side, db_trade.quantity)
                
                if db_trade.binance_sl_id: await self.client.cancel_order(symbol, db_trade.binance_sl_id)
                if db_trade.binance_tp_id: await self.client.cancel_order(symbol, db_trade.binance_tp_id)

                db_trade.status, db_trade.exit_price, db_trade.exit_time = 'CLOSED', p, datetime.utcnow()
                pnl = (p - entry) * db_trade.quantity if is_long else (entry - p) * db_trade.quantity
                db_trade.realized_pnl = round(pnl, 2)
                db_trade.roe_percent = round((pnl / (entry * db_trade.quantity)) * 100, 2)
                
                await session.commit()
                asset.is_in_position = False
                asset.current_trade = None