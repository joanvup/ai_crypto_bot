import os
import asyncio
from datetime import datetime, timezone
from database.session import AsyncSessionLocal
from database.models import Trade
from connectors.binance_futures import BinanceFuturesClient
from connectors.binance_ws import BinanceWebSocket
from ta_engine.indicators import TAEngine
from ai_engine.model import AIPredictor
from risk_manager.risk_engine import RiskManager
from dotenv import load_dotenv

load_dotenv()

class BotCore:
    def __init__(self):
        # 1. Carga desde .env
        self.symbol = os.getenv("SYMBOL", "BTC/USDT")
        self.timeframe = os.getenv("TIMEFRAME", "1h")
        self.ws_symbol = self.symbol.replace('/', '').lower()
        self.strategy_interval = int(os.getenv("STRATEGY_INTERVAL_SECONDS", "5"))
        self.ai_threshold = float(os.getenv("AI_CONFIDENCE_THRESHOLD", "47.0"))
        self.training_limit = int(os.getenv("AI_TRAINING_LIMIT", "15000"))
        self.retrain_hours = int(os.getenv("AI_RETRAIN_INTERVAL_HOURS", "6"))

        # Riesgo
        risk_per_trade = float(os.getenv("RISK_PER_TRADE", "0.01"))
        risk_reward = float(os.getenv("RISK_REWARD_RATIO", "2.0"))
        self.be_trigger_r = float(os.getenv("BREAK_EVEN_TRIGGER_R", "1.0"))
        self.ts_trigger_r = float(os.getenv("TRAILING_STOP_TRIGGER_R", "1.5"))

        # Módulos
        self.client = BinanceFuturesClient()
        self.ws = BinanceWebSocket()
        self.ta = TAEngine()
        self.ai = AIPredictor(self.symbol, self.timeframe)
        self.risk = RiskManager(risk_per_trade=risk_per_trade, risk_reward_ratio=risk_reward)
        
        # Estado
        self.is_in_position = False
        self.current_trade = None
        self.current_price = 0.0
        self.latest_prediction = "ESPERANDO"
        self.latest_confidence = 0.0
        self.last_atr = 0.0

    async def start(self):
        try:
            print(f"🚀 Iniciando Core Engine para {self.symbol}...")
            
            # 1. Configurar cuenta (solo si el método existe en el conector actual)
            if hasattr(self.client, 'setup_account'):
                await self.client.setup_account()
            
            # 2. Sincronizar tiempo con Binance
            try:
                print("⏱️ Sincronizando reloj del bot con los servidores de Binance...")
                await self.client.exchange.load_time_difference()
            except Exception as e:
                print(f"⚠️ Aviso en sincronización de tiempo: {e}")
            
            # 3. Preparar IA con seguro anti-cuelgues
            ai_ready = await self._prepare_ai()
            if not ai_ready:
                print("🛑 Bot detenido de forma segura debido a falta de datos.")
                return # Detiene la ejecución del bot (FastAPI seguirá vivo)
                
            # 4. Arrancar bucles
            await asyncio.gather(
                self.ws.subscribe_ticker(self.ws_symbol, self._on_price_update),
                self._strategy_loop(),
                self._retraining_loop()
            )
            
        except Exception as e:
            # ESTO EVITA LOS FALLOS SILENCIOSOS EN SEGUNDO PLANO
            import traceback
            print(f"\n🚨 ERROR FATAL EN EL NÚCLEO DEL BOT 🚨")
            traceback.print_exc()
            print("=========================================\n")

    async def _prepare_ai(self):
        print(f"🧠 Verificando modelo de IA (Descargando {self.training_limit} velas)...")
        klines = await self.client.get_historical_klines(self.symbol, self.timeframe, limit=self.training_limit)
        
        # SEGURO DE VIDA: Si Binance no manda datos, abortamos limpio
        if not klines or len(klines) < 50:
            print("🚨 ERROR CRÍTICO: Binance no devolvió suficientes datos históricos.")
            print("   👉 Solución 1: Verifica que tu reloj de Windows esté sincronizado.")
            print("   👉 Solución 2: Revisa tus API Keys (Asegúrate de que tengan permisos de lectura de Futuros).")
            return False

        df = self.ta.apply_indicators(self.ta.prepare_dataframe(klines))
        
        # Verificación doble por si Pandas-TA borró demasiadas filas por valores NaN
        if df.empty or len(df) < 50:
            print("🚨 ERROR CRÍTICO: El DataFrame quedó vacío tras calcular los indicadores.")
            return False

        try:
            self.ai.predict(df)
            print("✅ IA cargada correctamente desde disco.")
        except:
            print(f"⚠️ IA no entrenada. Iniciando entrenamiento con {len(df)} velas útiles...")
            await asyncio.to_thread(self.ai.train, df)
            
        return True

    async def _on_price_update(self, symbol: str, price: float):
        self.current_price = price
        if self.is_in_position and self.current_trade:
            await self._monitor_advanced_position()

    async def _strategy_loop(self):
        while True:
            try:
                if not self.is_in_position:
                    klines = await self.client.get_historical_klines(self.symbol, self.timeframe, limit=200)
                    if klines:
                        df = self.ta.apply_indicators(self.ta.prepare_dataframe(klines))
                        pred, conf = self.ai.predict(df)
                        direction = 'LONG' if pred == 1 else 'SHORT' if pred == -1 else 'NEUTRAL'
                        
                        self.latest_prediction = direction
                        self.latest_confidence = conf
                        self.last_atr = float(df['ATRr_14'].iloc[-1])

                        # --- NUEVO: RADAR EN CONSOLA ---
                        # \r hace que se sobreescriba la misma línea para no llenar la pantalla
                        import sys
                        hora_actual = datetime.now().strftime('%H:%M:%S')
                        sys.stdout.write(f"\r[{hora_actual}] 🔎 Radar IA: {direction} ({conf:.2f}%) | Umbral: {self.ai_threshold}% ")
                        sys.stdout.flush()

                        if direction in ['LONG', 'SHORT'] and conf >= self.ai_threshold:
                            print("\n") # Salto de línea para no pisar el radar
                            await self._execute_trade(direction, df)
            except Exception as e:
                print(f"\n❌ Error Strategy Loop: {e}")
            await asyncio.sleep(self.strategy_interval)

    async def _execute_trade(self, direction, df):
        """
        Ejecuta el trade y sincroniza el precio real de Binance antes de persistir en DB.
        """
        try:
            # 1. Cálculos Previos
            balance_info = await self.client.get_balance()
            
            # Cero hardcoding en el fallback
            if balance_info:
                balance = float(balance_info['total'])
            else:
                balance = float(os.getenv("DRY_RUN_INITIAL_BALANCE", "1000.0"))
                
            theoretical_price = float(df['close'].iloc[-1])
            atr = float(df['ATRr_14'].iloc[-1])
            
            # Calculamos SL/TP teóricos para el envío inicial
            sl_initial, tp_initial = self.risk.calculate_sl_tp(direction, theoretical_price, atr)
            size = float(self.risk.calculate_position_size(balance, theoretical_price, sl_initial))
            
            if size <= 0: return

            print(f"\n⚡ ENVIANDO ORDEN A BINANCE ({direction})...")
            side = 'BUY' if direction == 'LONG' else 'SELL'
            
            # 2. EJECUCIÓN ATÓMICA
            results = await self.client.place_atomic_trade(self.symbol, side, size, sl_initial, tp_initial)
            
            if results and isinstance(results, list) and len(results) >= 1:
                entry_res = results[0]
                if 'code' in entry_res:
                    print(f"🚨 Error de Binance: {entry_res}")
                    return

                main_id = str(entry_res.get('orderId'))
                
                # --- PASO CRÍTICO: SINCRONIZACIÓN REAL ---
                print(f"   ⏳ Sincronizando precio real de ejecución para orden {main_id}...")
                await asyncio.sleep(0.6) # Pequeña pausa para que el Matching Engine de Binance liquide
                
                real_order = await self.client.fetch_order_details(self.symbol, main_id)
                
                # Buscamos el precio real (average). Si falla, usamos el de la respuesta inicial.
                if real_order and real_order.get('average'):
                    real_entry_price = float(real_order['average'])
                else:
                    real_entry_price = float(entry_res.get('avgPrice', theoretical_price))

                # 3. RE-CALCULAR RIESGO BASADO EN LA REALIDAD
                # Ahora que sabemos el precio real, ajustamos el SL y TP exactos
                sl_real, tp_real = self.risk.calculate_sl_tp(direction, real_entry_price, atr)

                # 4. GUARDAR EN DB CON PRECIO REAL
                async with AsyncSessionLocal() as session:
                    new_trade = Trade(
                        symbol=self.symbol,
                        side=direction,
                        entry_price=real_entry_price, # <--- VERDAD ABSOLUTA
                        quantity=size,
                        stop_loss=sl_real,            # <--- AJUSTADO AL PRECIO REAL
                        take_profit=tp_real,          # <--- AJUSTADO AL PRECIO REAL
                        atr_at_entry=atr,
                        binance_order_id=main_id,
                        binance_sl_id=str(results[1].get('orderId')) if len(results) > 1 else None,
                        binance_tp_id=str(results[2].get('orderId')) if len(results) > 2 else None,
                        status='OPEN',
                        entry_time=datetime.utcnow()
                    )
                    session.add(new_trade)
                    await session.commit()
                    await session.refresh(new_trade)
                    
                    self.current_trade = new_trade
                    self.is_in_position = True
                    
                    print(f"✅ TRADE SINCRONIZADO")
                    print(f"   Precio Binance: ${real_entry_price}")
                    print(f"   SL Real: ${sl_real} | TP Real: ${tp_real}")
            else:
                print(f"🚨 El lote falló. Respuesta: {results}")

        except Exception as e:
            print(f"❌ Error crítico en ejecución y sincronización: {e}")

    async def _monitor_advanced_position(self):
        """
        Monitorea la posición activa y registra auditoría de movimientos de SL.
        """
        trade = self.current_trade
        if not trade: return
        
        p = self.current_price
        entry = trade.entry_price
        atr = trade.atr_at_entry
        sl_distance = abs(entry - trade.stop_loss)
        
        is_long = trade.side == 'LONG'
        current_profit = (p - entry) if is_long else (entry - p)
        r_multiple = current_profit / sl_distance if sl_distance > 0 else 0

        async with AsyncSessionLocal() as session:
            # 1. Recuperar trade de la DB con la sesión actual
            db_trade = await session.get(Trade, trade.id)
            if not db_trade or db_trade.status != 'OPEN': return

            # --- LÓGICA DE BREAK EVEN (BE) ---
            if not db_trade.is_break_even and r_multiple >= self.be_trigger_r:
                old_sl = db_trade.stop_loss
                new_sl = entry
                
                print(f"🛡️ AUDITORÍA: Moviendo a Break Even. SL: {old_sl} -> {new_sl}")
                
                # Intentar actualizar en Binance (Hard Stop) si existe ID
                if db_trade.binance_sl_id:
                    await self.client.cancel_order(self.symbol, db_trade.binance_sl_id)
                    # Colocamos el nuevo SL en Binance
                    sl_res, _ = await self.client.place_sl_tp(self.symbol, db_trade.side, db_trade.quantity, new_sl, db_trade.take_profit)
                    if sl_res: db_trade.binance_sl_id = str(sl_res.get('orderId'))

                # Registrar en Historial de Auditoría
                sl_log = TradeSLHistory(
                    trade_id=db_trade.id,
                    event_type='BREAK_EVEN',
                    old_sl=old_sl,
                    new_sl=new_sl,
                    price_at_event=p
                )
                session.add(sl_log)
                
                # Actualizar trade
                db_trade.stop_loss = new_sl
                db_trade.is_break_even = True
                await session.commit()
                self.current_trade = db_trade

            # --- LÓGICA DE TRAILING STOP (TS) ---
            # Activación inicial del modo Trailing
            if not db_trade.is_trailing and r_multiple >= self.ts_trigger_r:
                print(f"🚀 AUDITORÍA: Activando Trailing Stop. Anulando TP.")
                
                if db_trade.binance_tp_id:
                    await self.client.cancel_order(self.symbol, db_trade.binance_tp_id)
                
                db_trade.is_trailing = True
                db_trade.take_profit = 0.0 # El TP ya no existe, buscamos el máximo
                await session.commit()

            # Ejecución del seguimiento del Trailing Stop
            if db_trade.is_trailing:
                potential_ts = p - atr if is_long else p + atr
                
                # Solo movemos el SL si es a favor (sube en Long, baja en Short)
                if (is_long and potential_ts > db_trade.stop_loss) or (not is_long and potential_ts < db_trade.stop_loss):
                    old_sl = db_trade.stop_loss
                    new_sl = round(potential_ts, 2)
                    
                    print(f"📈 AUDITORÍA: Ajuste de Trailing Stop. SL: {old_sl} -> {new_sl}")

                    # Actualizar en Binance
                    if db_trade.binance_sl_id:
                        await self.client.cancel_order(self.symbol, db_trade.binance_sl_id)
                        # Nota: place_sl_tp maneja la creación del nuevo SL
                        sl_res, _ = await self.client.place_sl_tp(self.symbol, db_trade.side, db_trade.quantity, new_sl, 0)
                        if sl_res: db_trade.binance_sl_id = str(sl_res.get('orderId'))

                    # Registrar en Historial de Auditoría
                    sl_log = TradeSLHistory(
                        trade_id=db_trade.id,
                        event_type='TRAILING_STOP',
                        old_sl=old_sl,
                        new_sl=new_sl,
                        price_at_event=p
                    )
                    session.add(sl_log)
                    
                    db_trade.stop_loss = new_sl
                    await session.commit()
                    self.current_trade = db_trade

            # --- VERIFICACIÓN DE CIERRE (SOFT STOP) ---
            close_triggered = False
            reason = ""
            
            if is_long:
                if p <= db_trade.stop_loss: 
                    close_triggered, reason = True, "Stop Loss / Trailing"
                elif db_trade.take_profit > 0 and p >= db_trade.take_profit:
                    close_triggered, reason = True, "Take Profit"
            else:
                if p >= db_trade.stop_loss:
                    close_triggered, reason = True, "Stop Loss / Trailing"
                elif db_trade.take_profit > 0 and p <= db_trade.take_profit:
                    close_triggered, reason = True, "Take Profit"

            if close_triggered:
                print(f"🔔 EJECUTANDO CIERRE DE POSICIÓN ({reason}) a ${p}")
                
                # Orden de cierre a mercado
                exit_side = 'SELL' if is_long else 'BUY'
                
                await self.client.place_order(self.symbol, exit_side, db_trade.quantity)
                
                # Limpiar órdenes pendientes en Binance
                if db_trade.binance_sl_id: await self.client.cancel_order(self.symbol, db_trade.binance_sl_id)
                if db_trade.binance_tp_id: await self.client.cancel_order(self.symbol, db_trade.binance_tp_id)

                # Finalizar trade en DB
                db_trade.status = 'CLOSED'
                db_trade.exit_price = p
                db_trade.exit_time = datetime.utcnow()
                
                pnl = (p - entry) * db_trade.quantity if is_long else (entry - p) * db_trade.quantity
                db_trade.realized_pnl = round(pnl, 2)
                db_trade.roe_percent = round((pnl / (entry * db_trade.quantity)) * 100, 2)
                
                await session.commit()
                self.is_in_position = False
                self.current_trade = None

    async def _retraining_loop(self):
        while True:
            await asyncio.sleep(self.retrain_hours * 3600)
            try:
                klines = await self.client.get_historical_klines(self.symbol, self.timeframe, limit=self.training_limit)
                df = self.ta.apply_indicators(self.ta.prepare_dataframe(klines))
                await asyncio.to_thread(self.ai.train, df)
            except Exception as e:
                print(f"❌ Error Reentrenamiento: {e}")