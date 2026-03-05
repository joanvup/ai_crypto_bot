
from sqlalchemy import select, func
import os
import asyncio
from datetime import datetime, timezone
from database.session import AsyncSessionLocal
from database.models import Trade, TradeSLHistory, BalanceHistory
from connectors.binance_futures import BinanceFuturesClient
from connectors.binance_ws import BinanceWebSocket
# from connectors.market_scanner import MarketScanner
from ta_engine.indicators import TAEngine
from ai_engine.model import AIPredictor
from risk_manager.risk_engine import RiskManager
from dotenv import load_dotenv
import traceback

load_dotenv()

# --- NUEVA CLASE: Contenedor de Estado por Activo ---
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
        # 1. Parámetros Globales
        self.timeframe = os.getenv("TIMEFRAME", "1h")
        self.strategy_interval = int(os.getenv("STRATEGY_INTERVAL_SECONDS", "5"))
        self.ai_threshold = float(os.getenv("AI_CONFIDENCE_THRESHOLD", "47.0"))
        self.training_limit = int(os.getenv("AI_TRAINING_LIMIT", "15000"))
        
        # Parámetros Multi-Activo
        self.max_monitored_assets = int(os.getenv("MAX_MONITORED_ASSETS", "20"))
        self.max_open_trades = int(os.getenv("MAX_OPEN_TRADES", "6"))

        # Riesgo
        risk_per_trade = float(os.getenv("RISK_PER_TRADE", "0.01"))
        risk_reward = float(os.getenv("RISK_REWARD_RATIO", "2.0"))
        self.be_trigger_r = float(os.getenv("BREAK_EVEN_TRIGGER_R", "1.0"))
        self.ts_trigger_r = float(os.getenv("TRAILING_STOP_TRIGGER_R", "1.5"))
        # --- NUEVO: PARÁMETROS AGRESIVOS ---
        self.be_plus_percent = float(os.getenv("BE_PLUS_PERCENT", "0.05"))
        self.agg_ts_trigger_r = float(os.getenv("AGGRESSIVE_TS_TRIGGER_R", "3.0"))
        self.agg_ts_percent = float(os.getenv("AGGRESSIVE_TS_PERCENT", "0.20"))

        # Módulos Compartidos
        #self.scanner = MarketScanner()
        self.client = BinanceFuturesClient()
        self.ws = BinanceWebSocket()
        self.ta = TAEngine()
        self.risk = RiskManager(risk_per_trade=risk_per_trade, risk_reward_ratio=risk_reward)
        
        # Estado Global Multi-Activo
        self.assets = {} # Diccionario: {'BTC/USDT': AssetState, 'ETH/USDT': AssetState...}
        self.trade_lock = asyncio.Lock() # Bloqueo global para no superar el límite de trades
        self.recovered_trades = {} # <--- NUEVO: Memoria temporal de rescate
        self.is_ready = False  # <--- NUEVO: Candado físico

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

            # --- 1. RECUPERAR ESTADO ANTES DE HACER NADA ---
            await self._recover_state()
            
            # --- 2. ESCANEAR MERCADO (EL BANQUILLO DE SUPLENTES) ---
            candidatos_limit = self.max_monitored_assets * 3
            # AHORA USA EL CLIENTE PRINCIPAL
            candidate_symbols = await self.client.get_top_assets(limit=candidatos_limit)
            
            if not candidate_symbols:
                print("🛑 No se pudieron obtener activos del escáner.")
                return

            # --- 3. ANCLAR SÍMBOLOS RECUPERADOS AL RADAR ---
            # Si recuperamos un trade en 'DOGE' pero hoy no está en el Top 10, lo obligamos a entrar
            # para que el bot siga monitoreando su Stop Loss y Trailing.
            for sym in self.recovered_trades.keys():
                if sym not in top_symbols:
                    top_symbols.append(sym)
                    print(f"   ⚓ Anclando {sym} al escáner por recuperación de trade activo.")

            # --- 4. ENTRENAR IA E INYECTAR MEMORIA ---
            print("\n🧠 Iniciando fase de Entrenamiento Masivo (Esto puede tomar varios minutos)...")
            for sym in top_symbols:
                self.assets[sym] = AssetState(sym, self.timeframe)
                
                # INYECTAR TRADE RECUPERADO
                if sym in self.recovered_trades:
                    self.assets[sym].is_in_position = True
                    self.assets[sym].current_trade = self.recovered_trades[sym]
                
                success = await self._prepare_ai(sym)
                if not success:
                    print(f"⚠️ {sym} omitido por falta de datos.")
            
            # --- NUEVO: LIBERACIÓN DEL CANDADO ---
            print("\n🔓 Todo el estado ha sido validado. Quitanto candado de seguridad.")
            self.is_ready = True
            print("\n✅ Todas las IAs listas. Desplegando redes de monitoreo...")

            # --- 5. ARRANCAR BUCLES CONCURRENTES (Escalonado) ---
            tasks = [asyncio.create_task(self._balance_snapshot_loop())]
            
            print("🔌 Conectando a los WebSockets de Binance de forma segura...")
            for sym in self.assets.keys():
                asset = self.assets[sym]
                
                # Pausa de 0.5s entre conexiones para no disparar el límite de 5/seg de Binance
                await asyncio.sleep(0.5) 
                
                tasks.append(asyncio.create_task(self.ws.subscribe_ticker(asset.ws_symbol, lambda s, p, sym=sym: self._on_price_update(sym, p))))
                tasks.append(asyncio.create_task(self._strategy_loop(sym)))
            
            await asyncio.gather(*tasks)
            
        except Exception as e:
            print(f"\n🚨 ERROR FATAL EN EL NÚCLEO DEL BOT 🚨")
            traceback.print_exc()

    async def _recover_state(self):
        """
        Lee la BD buscando trades 'OPEN' y los concilia con la realidad (Binance).
        """
        print("\n🔄 Iniciando Módulo de Recuperación de Estado...")
        async with AsyncSessionLocal() as session:
            # Buscar operaciones que quedaron marcadas como OPEN cuando el bot se apagó
            query = select(Trade).where(Trade.status == 'OPEN')
            result = await session.execute(query)
            open_trades = result.scalars().all()

            if not open_trades:
                print("   ✅ No hay operaciones huérfanas en BD. Estado limpio.")
                return

            print(f"   🔎 Encontradas {len(open_trades)} operaciones OPEN en Base de Datos.")

            # Obtener realidad de Binance (Si estamos en Live/Testnet)
            live_positions = await self.client.get_open_positions()

            for trade in open_trades:
                sym = trade.symbol
                is_alive = False

                if self.client.environment == 'dry_run':
                    # En Dry Run confiamos ciegamente en la BD. El Soft Stop se encargará 
                    # de cerrar si el precio ya pasó el SL al conectar el WebSocket.
                    is_alive = True
                else:
                    if sym in live_positions:
                        is_alive = True
                    else:
                        # Estaba en la BD, pero Binance ya la cerró (Tocó SL/TP offline)
                        print(f"   ⚠️ Posición {sym} ya no existe en Binance (Cerrada mientras estaba offline).")
                        trade.status = 'CLOSED'
                        trade.exit_time = datetime.utcnow()
                        # Usamos el Stop Loss como precio de salida aproximado para limpiar la BD
                        trade.exit_price = trade.stop_loss 
                        
                        is_long = trade.side == 'LONG'
                        pnl = (trade.exit_price - trade.entry_price) * trade.quantity if is_long else (trade.entry_price - trade.exit_price) * trade.quantity
                        trade.realized_pnl = round(pnl, 2)
                        trade.roe_percent = round((pnl / (trade.entry_price * trade.quantity)) * 100, 2)

                if is_alive:
                    print(f"   ✅ Restaurando {sym} en memoria (Entrada: ${trade.entry_price})...")
                    session.expunge(trade) # Desvincula el objeto de esta sesión para usarlo globalmente
                    self.recovered_trades[sym] = trade

            await session.commit()

    async def get_current_balance(self):
        """
        Obtiene el balance real de Binance, o calcula el balance virtual en Dry Run
        sumando el PNL histórico de la Base de Datos al balance inicial.
        """
        if self.client.environment == 'dry_run':
            initial_balance = float(os.getenv("DRY_RUN_INITIAL_BALANCE", "1000.0"))
            
            # Sumar todo el PNL de los trades cerrados
            async with AsyncSessionLocal() as session:
                query = select(func.sum(Trade.realized_pnl)).where(Trade.status == 'CLOSED')
                result = await session.execute(query)
                accumulated_pnl = result.scalar() or 0.0
                
                current = initial_balance + accumulated_pnl
                return {"total": current, "free": current}
        else:
            # En Testnet o Live, consultamos directamente al exchange
            return await self.client.get_balance()

    async def _prepare_ai(self, symbol: str) -> bool:
        asset = self.assets[symbol]
        print(f"   ➤ Descargando historial para {symbol}...")
        klines = await self.client.get_historical_klines(symbol, self.timeframe, limit=self.training_limit)
        
        if not klines or len(klines) < 50: return False

        df = self.ta.apply_indicators(self.ta.prepare_dataframe(klines))
        if df.empty or len(df) < 50: return False

        try:
            asset.ai.predict(df)
            print(f"      [{symbol}] IA cargada desde disco.")
        except:
            print(f"      [{symbol}] Entrenando nuevo modelo...")
            await asyncio.to_thread(asset.ai.train, df)
            
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
                # --- NUEVO: SI EL CANDADO ESTÁ PUESTO, NO HACER NADA ---
                if not self.is_ready:
                    await asyncio.sleep(1)
                    continue
                if not asset.is_in_position:
                    klines = await self.client.get_historical_klines(symbol, self.timeframe, limit=200)
                    if klines:
                        df = self.ta.apply_indicators(self.ta.prepare_dataframe(klines))
                        # --- NUEVO: SALVAVIDAS DE PRECIO REST ---
                        # Si el WS aún no ha enviado datos, usamos el precio de la vela cerrada
                        if asset.current_price == 0.0:
                            asset.current_price = float(df['close'].iloc[-1])
                        # ----------------------------------------
                        pred, conf = asset.ai.predict(df)
                        direction = 'LONG' if pred == 1 else 'SHORT' if pred == -1 else 'NEUTRAL'
                        
                        asset.latest_prediction = direction
                        asset.latest_confidence = conf
                        asset.last_atr = float(df['ATRr_14'].iloc[-1])

                        if direction in ['LONG', 'SHORT'] and conf >= self.ai_threshold:
                            await self._execute_trade(symbol, direction, df)
            except Exception as e:
                pass # Silenciado para no spamear por errores de red temporales
            
            await asyncio.sleep(self.strategy_interval)

    async def _execute_trade(self, symbol: str, direction: str, df):
        asset = self.assets[symbol]
        
        # --- CONTROL DE RIESGO GLOBAL BLINDADO ---
        async with self.trade_lock:
            # Contamos las que ya están abiertas + las que están viajando a Binance
            current_open_trades = sum(1 for a in self.assets.values() if a.is_in_position or a.trade_in_progress)
            
            if current_open_trades >= self.max_open_trades:
                return # Límite alcanzado, ignoramos en silencio
                
            # Reservamos el cupo INMEDIATAMENTE para que el siguiente hilo no pase
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
            # Pase lo que pase (éxito o error de red), liberamos el estado de reserva de cupo
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

            # ==========================================
            # NIVEL 1: BREAK EVEN + FEES
            # ==========================================
            if not db_trade.is_break_even and r_multiple >= self.be_trigger_r:
                old_sl = db_trade.stop_loss
                # En lugar de ser exactamente 0, le damos un "Plus" para pagar spread/fees
                plus_amount = atr * self.be_plus_percent
                new_sl = float(entry + plus_amount) if is_long else float(entry - plus_amount)
                
                print(f"🛡️ [{symbol}] Break Even (+Fees). SL: {old_sl} -> {new_sl}")
                if db_trade.binance_sl_id:
                    await self.client.cancel_order(symbol, db_trade.binance_sl_id)
                    sl_res, _ = await self.client.place_sl_tp(symbol, db_trade.side, db_trade.quantity, new_sl, db_trade.take_profit)
                    if sl_res: db_trade.binance_sl_id = str(sl_res.get('orderId'))
                
                session.add(TradeSLHistory(trade_id=db_trade.id, event_type='BREAK_EVEN', old_sl=old_sl, new_sl=new_sl, price_at_event=p))
                db_trade.stop_loss = new_sl
                db_trade.is_break_even = True
                await session.commit()
                asset.current_trade = db_trade

            # ==========================================
            # NIVEL 2: ACTIVACIÓN TRAILING STOP
            # ==========================================
            if not db_trade.is_trailing and r_multiple >= self.ts_trigger_r:
                print(f"🚀 [{symbol}] Trailing Stop Activado. Anulando Take Profit.")
                if db_trade.binance_tp_id: 
                    await self.client.cancel_order(symbol, db_trade.binance_tp_id)
                db_trade.is_trailing = True
                db_trade.take_profit = 0.0
                await session.commit()

            # ==========================================
            # NIVEL 3: SEGUIMIENTO (CLÁSICO VS AGRESIVO)
            # ==========================================
            if db_trade.is_trailing:
                # Decidir la fórmula matemática según la ganancia actual
                if r_multiple >= self.agg_ts_trigger_r:
                    # MODO AGRESIVO (Asegurar el recorrido)
                    recorrido = abs(p - entry)
                    distancia_ts = recorrido * self.agg_ts_percent
                    event_type = 'TRAILING_AGGRESSIVE'
                else:
                    # MODO CLÁSICO (1 ATR de distancia para dejar respirar)
                    distancia_ts = atr * 1.0 
                    event_type = 'TRAILING_ATR'

                potential_ts = p - distancia_ts if is_long else p + distancia_ts
                
                # Solo movemos el Stop Loss si la nueva posición asegura MÁS ganancia que la actual
                move_sl = False
                if is_long and potential_ts > db_trade.stop_loss: move_sl = True
                elif not is_long and potential_ts < db_trade.stop_loss: move_sl = True

                if move_sl:
                    old_sl = db_trade.stop_loss
                    new_sl = float(potential_ts)
                    
                    print(f"📈[{symbol}] Ajuste {event_type}. SL: {old_sl} -> {new_sl}")

                    if db_trade.binance_sl_id:
                        await self.client.cancel_order(symbol, db_trade.binance_sl_id)
                        sl_res, _ = await self.client.place_sl_tp(symbol, db_trade.side, db_trade.quantity, new_sl, 0)
                        if sl_res: db_trade.binance_sl_id = str(sl_res.get('orderId'))

                    session.add(TradeSLHistory(trade_id=db_trade.id, event_type=event_type, old_sl=old_sl, new_sl=new_sl, price_at_event=p))
                    db_trade.stop_loss = new_sl
                    await session.commit()
                    asset.current_trade = db_trade

            # ==========================================
            # CIERRE DE SEGURIDAD (SOFT STOP)
            # ==========================================
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

                db_trade.status = 'CLOSED'
                db_trade.exit_price = p
                db_trade.exit_time = datetime.utcnow()
                pnl = (p - entry) * db_trade.quantity if is_long else (entry - p) * db_trade.quantity
                db_trade.realized_pnl = round(pnl, 2)
                db_trade.roe_percent = round((pnl / (entry * db_trade.quantity)) * 100, 2)
                
                await session.commit()
                asset.is_in_position = False
                asset.current_trade = None
    
    async def _balance_snapshot_loop(self):
        """Toma una foto del balance cada 15 minutos para la gráfica del Dashboard."""
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
                print(f"❌ Error guardando snapshot de balance: {e}")
            
            await asyncio.sleep(900) # 900 segundos = 15 minutos