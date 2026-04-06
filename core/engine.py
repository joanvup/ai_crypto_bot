from sqlalchemy import select, func
import os
import csv
import asyncio
import time 
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
        
        # Frenos de Seguridad
        self.cooldown_until = 0  
        self.consecutive_losses = 0
        self.last_trade_date = ""
        
        # Caja Negra para Análisis TTL
        self.price_trajectory =[]
        self.last_sample_time = 0

class BotCore:
    def __init__(self):
        self.timeframe = os.getenv("TIMEFRAME", "1h")
        self.strategy_interval = int(os.getenv("STRATEGY_INTERVAL_SECONDS", "5"))
        self.ai_threshold = float(os.getenv("AI_CONFIDENCE_THRESHOLD", "47.0"))
        self.training_limit = int(os.getenv("AI_TRAINING_LIMIT", "15000"))
        self.retrain_hours = int(os.getenv("AI_RETRAIN_INTERVAL_HOURS", "6"))
        
        self.max_monitored_assets = int(os.getenv("MAX_MONITORED_ASSETS", "10"))
        self.max_open_trades = int(os.getenv("MAX_OPEN_TRADES", "3"))

        risk_per_trade = float(os.getenv("RISK_PER_TRADE", "0.01"))
        risk_reward = float(os.getenv("RISK_REWARD_RATIO", "2.0"))
        sl_multi = float(os.getenv("ATR_MULTIPLIER_SL", "1.5"))
        
        self.be_trigger_r = float(os.getenv("BREAK_EVEN_TRIGGER_R", "1.0"))
        self.ts_trigger_r = float(os.getenv("TRAILING_STOP_TRIGGER_R", "1.5"))
        self.ts_distance_atr = float(os.getenv("TRAILING_STOP_DISTANCE_ATR", "1.0"))
        self.be_plus_percent = float(os.getenv("BE_PLUS_PERCENT", "0.05"))
        self.agg_ts_trigger_r = float(os.getenv("AGGRESSIVE_TS_TRIGGER_R", "3.0"))
        self.agg_ts_percent = float(os.getenv("AGGRESSIVE_TS_PERCENT", "0.20"))

        # --- NUEVO: PARÁMETRO TTL (ZOMBIE KILLER) ---
        self.time_to_live_minutes = int(os.getenv("TIME_TO_LIVE_MINUTES", "240"))
        
        self.ttl_sample_interval = int(os.getenv("TTL_SAMPLE_INTERVAL_SECONDS", "60"))
        self.ttl_csv_file = "analisis_ttl_perdidas.csv"

        self.scanner = MarketScanner()
        self.client = BinanceFuturesClient()
        self.ws = BinanceWebSocket()
        self.ta = TAEngine()
        self.risk = RiskManager(risk_per_trade=risk_per_trade, risk_reward_ratio=risk_reward, sl_multiplier=sl_multi)
        
        self.assets = {} 
        self.trade_lock = asyncio.Lock() 
        self.recovered_trades = {}
        self.is_ready = False
        
        if not os.path.exists(self.ttl_csv_file):
            with open(self.ttl_csv_file, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(["ID_Trade", "Symbol", "Side", "Hora_Entrada_UTC", "Hora_Muestra_UTC", "Minutos_Transcurridos", "Precio", "Distancia_SL_Pct"])

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
            print(f"\n🚀 Iniciando Core Engine MULTI-ACTIVO (Max {self.max_open_trades} trades)...")
            if hasattr(self.client, 'setup_account'):
                await self.client.setup_account()
            
            try:
                print("⏱️ Sincronizando reloj con Binance...")
                await self.client.exchange.load_time_difference()
                if hasattr(self.client.exchange, 'load_markets'):
                    await self.client.exchange.load_markets()
            except Exception as e:
                print(f"⚠️ Aviso de sincronización: {e}")

            await self._recover_state()
            
            top_symbols = await self.scanner.get_top_assets(limit=self.max_monitored_assets)
            if not top_symbols:
                print("🛑 No se pudieron obtener activos del escáner.")
                return

            for sym in self.recovered_trades.keys():
                if sym not in top_symbols:
                    top_symbols.append(sym)

            print("\n🧠 Iniciando fase de Entrenamiento Masivo...")
            for sym in top_symbols:
                self.assets[sym] = AssetState(sym, self.timeframe)
                if sym in self.recovered_trades:
                    self.assets[sym].is_in_position = True
                    self.assets[sym].current_trade = self.recovered_trades[sym]
                
                success = await self._prepare_ai(sym)
                if not success:
                    print(f"🧹 Purgando {sym} de la memoria por falta de datos.")
                    del self.assets[sym]
            
            print("\n🔓 Todo el estado ha sido validado. Quitanto candado.")
            self.is_ready = True
            
            tasks =[
                asyncio.create_task(self._balance_snapshot_loop()),
                asyncio.create_task(self._time_sync_loop()),
                asyncio.create_task(self._orphan_sweeper_loop())
            ]
            for sym in self.assets.keys():
                asset = self.assets[sym]
                tasks.append(asyncio.create_task(self.ws.subscribe_ticker(asset.ws_symbol, lambda s, p, sym=sym: self._on_price_update(sym, p))))
                tasks.append(asyncio.create_task(self._strategy_loop(sym)))
            
            await asyncio.gather(*tasks)
            
        except Exception as e:
            print(f"\n🚨 ERROR FATAL EN EL NÚCLEO DEL BOT 🚨")
            traceback.print_exc()

    async def _time_sync_loop(self):
        while True:
            await asyncio.sleep(3600)
            try:
                await self.client.exchange.load_time_difference()
            except Exception: pass

    async def _orphan_sweeper_loop(self):
        while True:
            await asyncio.sleep(600)
            if self.client.environment == 'dry_run': continue
            try:
                live_positions = await self.client.get_open_positions()
                if not live_positions: continue
                
                async with AsyncSessionLocal() as session:
                    query = select(Trade.symbol).where(Trade.status == 'OPEN')
                    result = await session.execute(query)
                    db_symbols = [row[0] for row in result.all()]
                    
                    for sym, data in live_positions.items():
                        if sym not in db_symbols:
                            print(f"\n🚨 SWEEPER: Posición Fantasma en Binance ({sym}). Ejecutando Panic Close.")
                            await self.client.panic_close_position(sym, data['side'], data['contracts'])
            except Exception as e:
                print(f"❌ Error Sweeper: {e}")

    async def _recover_state(self):
        async with AsyncSessionLocal() as session:
            query = select(Trade).where(Trade.status == 'OPEN')
            result = await session.execute(query)
            open_trades = result.scalars().all()

            if not open_trades: return
            
            trades_by_symbol = {}
            for t in open_trades:
                if t.symbol not in trades_by_symbol: trades_by_symbol[t.symbol] = []
                trades_by_symbol[t.symbol].append(t)

            live_positions = await self.client.get_open_positions()

            for sym, trades in trades_by_symbol.items():
                trades.sort(key=lambda x: x.entry_time)
                if len(trades) > 1:
                    for old_trade in trades[:-1]:
                        old_trade.status = 'CLOSED'
                        old_trade.exit_time = datetime.utcnow()
                        old_trade.exit_price = old_trade.entry_price 
                        old_trade.realized_pnl = 0.0
                        old_trade.roe_percent = 0.0
                
                active_trade = trades[-1]
                is_alive = False

                if self.client.environment == 'dry_run':
                    is_alive = True
                else:
                    if sym in live_positions: is_alive = True
                    else:
                        active_trade.status = 'CLOSED'
                        active_trade.exit_time = datetime.utcnow()
                        active_trade.exit_price = active_trade.stop_loss 
                        is_long = active_trade.side == 'LONG'
                        pnl = (active_trade.exit_price - active_trade.entry_price) * active_trade.quantity if is_long else (active_trade.entry_price - active_trade.exit_price) * active_trade.quantity
                        active_trade.realized_pnl = round(pnl, 2)
                        active_trade.roe_percent = round((pnl / (active_trade.entry_price * active_trade.quantity)) * 100, 2)

                if is_alive:
                    session.expunge(active_trade)
                    self.recovered_trades[sym] = active_trade

            await session.commit()

    async def _balance_snapshot_loop(self):
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
            except Exception: pass
            await asyncio.sleep(900)

    async def _prepare_ai(self, symbol: str) -> bool:
        klines = await self.client.get_historical_klines(symbol, self.timeframe, limit=self.training_limit)
        if not klines or len(klines) < 50: return False
        df = self.ta.apply_indicators(self.ta.prepare_dataframe(klines))
        if df.empty or len(df) < 50: return False

        try:
            self.assets[symbol].ai.predict(df)
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
                
                if time.time() < asset.cooldown_until:
                    await asyncio.sleep(self.strategy_interval)
                    continue
                
                if not asset.is_in_position:
                    klines = await self.client.get_historical_klines(symbol, self.timeframe, limit=200)
                    if klines:
                        df = self.ta.apply_indicators(self.ta.prepare_dataframe(klines))
                        if not df.empty:
                            if len(df) >= 10:
                                last_10 = df.tail(10)
                                max_high = float(last_10['high'].max())
                                min_low = float(last_10['low'].min())
                                if min_low > 0:
                                    price_range_pct = ((max_high - min_low) / min_low) * 100
                                    if price_range_pct < 0.05:
                                        print(f"\n💀 PING-PONG[{symbol}]: Rango 10h {price_range_pct:.4f}%. Baneo 24h.")
                                        asset.cooldown_until = time.time() + (24 * 3600)
                                        continue
                                        
                            if asset.current_price == 0.0: asset.current_price = float(df['close'].iloc[-1])
                                
                            pred, conf = asset.ai.predict(df)
                            direction = 'LONG' if pred == 1 else 'SHORT' if pred == -1 else 'NEUTRAL'
                            
                            asset.latest_prediction = direction
                            asset.latest_confidence = conf
                            asset.last_atr = float(df['ATRr_14'].iloc[-1])

                            import sys
                            hora_actual = datetime.now().strftime('%H:%M:%S')
                            sys.stdout.write(f"\r[{hora_actual}] 📡 Radar escaneando {len(self.assets)} activos... ")
                            sys.stdout.flush()

                            if direction in ['LONG', 'SHORT'] and conf >= self.ai_threshold:
                                print("\n")
                                await self._execute_trade(symbol, direction, df)
            except Exception as e:
                error_msg = str(e)
                if "-1021" in error_msg:
                    asyncio.create_task(self.client.exchange.load_time_difference())
            
            await asyncio.sleep(self.strategy_interval)

    async def _retraining_loop(self):
        while True:
            await asyncio.sleep(self.retrain_hours * 3600)
            for sym, asset in self.assets.items():
                try:
                    klines = await self.client.get_historical_klines(sym, self.timeframe, limit=self.training_limit)
                    if klines:
                        df = self.ta.apply_indicators(self.ta.prepare_dataframe(klines))
                        if not df.empty:
                            await asyncio.to_thread(asset.ai.train, df)
                except Exception: pass

    async def _execute_trade(self, symbol: str, direction: str, df):
        asset = self.assets[symbol]
        
        if hasattr(self.client, 'get_funding_rate'):
            funding_rate = await self.client.get_funding_rate(symbol)
            tolerance = float(os.getenv("MAX_FUNDING_RATE_TOLERANCE", "0.05")) / 100.0
            will_pay = False
            if direction == 'LONG' and funding_rate > 0: will_pay = True
            elif direction == 'SHORT' and funding_rate < 0: will_pay = True
                
            if will_pay and abs(funding_rate) > tolerance:
                print(f"🚨 ABORTADO[{symbol}]: Funding Rate abusivo ({funding_rate*100:.4f}%).")
                asset.cooldown_until = time.time() + 900 
                return

        async with self.trade_lock:
            current_open_trades = sum(1 for a in self.assets.values() if a.is_in_position or a.trade_in_progress)
            if current_open_trades >= self.max_open_trades: return
            asset.trade_in_progress = True

        try:
            balance_info = await self.get_current_balance()
            balance = float(balance_info['total']) if balance_info else 1000.0
            
            theoretical_price = float(df['close'].iloc[-1])
            atr = float(df['ATRr_14'].iloc[-1])
            sl_initial, tp_initial = self.risk.calculate_sl_tp(direction, theoretical_price, atr)
            
            if direction == 'LONG' and sl_initial >= theoretical_price: sl_initial = theoretical_price - (atr * 1.5)
            elif direction == 'SHORT' and sl_initial <= theoretical_price: sl_initial = theoretical_price + (atr * 1.5)
            
            size = float(self.risk.calculate_position_size(balance, theoretical_price, sl_initial))
            if size <= 0: return

            if hasattr(self.client, 'get_bid_ask'):
                bid, ask = await self.client.get_bid_ask(symbol)
                if bid > 0 and ask > 0:
                    spread = ask - bid
                    sl_dist = abs(theoretical_price - sl_initial)
                    if sl_dist > 0 and spread > (sl_dist * 0.30):
                        print(f"🛑 ABORTADO [{symbol}]: Spread abusivo. Freno anti-liquidación.")
                        asset.cooldown_until = time.time() + 300 
                        return

            try:
                market = self.client.exchange.markets.get(symbol)
                if market and 'limits' in market and 'amount' in market['limits']:
                    max_qty = float(market['limits']['amount']['max'])
                    if size > max_qty: size = max_qty
            except Exception: pass

            print(f"\n⚡ SEÑAL [{symbol}]: {direction} | Confianza: {asset.latest_confidence:.2f}%")
            side = 'BUY' if direction == 'LONG' else 'SELL'
            
            results = await self.client.place_atomic_trade(symbol, side, size, sl_initial, tp_initial)
            
            if results and isinstance(results, list) and len(results) >= 1:
                entry_res = results[0]
                
                if 'code' in entry_res:
                    error_msg = entry_res.get('msg', 'Error desconocido')
                    error_code = str(entry_res.get('code', ''))
                    
                    if "-4005" in error_code or "max quantity" in error_msg.lower():
                        print(f"💀 BANEO PERMANENTE: {symbol} límite Testnet. Pausa 24h.")
                        asset.cooldown_until = time.time() + (24 * 3600)
                    else:
                        print(f"🚨 Binance rechazó: {error_msg}")
                        asset.cooldown_until = time.time() + 300 
                    return

                if len(results) == 3:
                    if 'code' in results[1]: print(f"   ⚠️ RECHAZO SL HARD: {results[1].get('msg')}")
                    if 'code' in results[2]: print(f"   ⚠️ RECHAZO TP HARD: {results[2].get('msg')}")

                main_id = str(entry_res.get('orderId', entry_res.get('id')))
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
                    
                    asset.price_trajectory =[]
                    asset.last_sample_time = time.time()
                    
                    print(f"✅ TRADE [{symbol}]: Entrada a ${real_entry_price}")
        except Exception as e:
            error_msg = str(e)
            if "-1021" in error_msg or "Timestamp" in error_msg:
                asyncio.create_task(self.client.exchange.load_time_difference())
            asset.cooldown_until = time.time() + 300
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

            # ==========================================
            # 0. MUESTREO TTL (Caja Negra) - FASE 23
            # ==========================================
            if not db_trade.is_break_even:
                current_t = time.time()
                if current_t - asset.last_sample_time >= self.ttl_sample_interval:
                    mins_elapsed = round((datetime.utcnow() - db_trade.entry_time).total_seconds() / 60, 2)
                    sl_dist_pct = abs(p - db_trade.stop_loss) / p * 100
                    
                    asset.price_trajectory.append([
                        db_trade.id, symbol, db_trade.side, 
                        db_trade.entry_time.strftime("%Y-%m-%d %H:%M:%S"),
                        datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
                        mins_elapsed, p, round(sl_dist_pct, 4)
                    ])
                    asset.last_sample_time = current_t

            # ==========================================
            # 1. BREAK EVEN
            # ==========================================
            if not db_trade.is_break_even and r_multiple >= self.be_trigger_r:
                old_sl = db_trade.stop_loss
                plus = atr * self.be_plus_percent
                new_sl = float(entry + plus) if is_long else float(entry - plus)
                
                print(f"🛡️[{symbol}] Break Even (+Fees). SL: {old_sl} -> {new_sl}")
                if db_trade.binance_sl_id:
                    await self.client.cancel_order(symbol, db_trade.binance_sl_id)
                    sl_res, _ = await self.client.place_sl_tp(symbol, db_trade.side, db_trade.quantity, new_sl, db_trade.take_profit)
                    if sl_res: db_trade.binance_sl_id = str(sl_res.get('orderId'))
                
                session.add(TradeSLHistory(trade_id=db_trade.id, event_type='BREAK_EVEN', old_sl=old_sl, new_sl=new_sl, price_at_event=p))
                db_trade.stop_loss = new_sl
                db_trade.is_break_even = True
                
                asset.price_trajectory =[] 
                
                await session.commit()
                asset.current_trade = db_trade

            # ==========================================
            # 2. ACTIVAR TRAILING
            # ==========================================
            if not db_trade.is_trailing and r_multiple >= self.ts_trigger_r:
                print(f"🚀 [{symbol}] Trailing Stop Activado.")
                if db_trade.binance_tp_id: await self.client.cancel_order(symbol, db_trade.binance_tp_id)
                db_trade.is_trailing = True
                db_trade.take_profit = 0.0
                await session.commit()

            # ==========================================
            # 3. SEGUIMIENTO TRAILING
            # ==========================================
            if db_trade.is_trailing:
                if r_multiple >= self.agg_ts_trigger_r:
                    distancia = abs(p - entry) * self.agg_ts_percent
                    event_type = 'TRAILING_AGGRESSIVE'
                else:
                    distancia = atr * self.ts_distance_atr 
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

            # ==========================================
            # 4. CIERRE DE SEGURIDAD (SOFT STOP & TTL)
            # ==========================================
            close, reason = False, ""
            mins_elapsed = (datetime.utcnow() - db_trade.entry_time).total_seconds() / 60.0
            
            # --- NUEVO: ESCUDO ANTI-ZOMBIES (TTL) ---
            if not db_trade.is_break_even and mins_elapsed >= self.time_to_live_minutes:
                close, reason = True, f"TTL Expirado ({int(mins_elapsed)}m)"
            # ----------------------------------------
            elif is_long:
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
                
                if not db_trade.is_break_even and pnl < 0 and asset.price_trajectory:
                    try:
                        with open(self.ttl_csv_file, 'a', newline='') as f:
                            writer = csv.writer(f)
                            writer.writerows(asset.price_trajectory)
                        print(f"📁 [{symbol}] Trayectoria de pérdida guardada en {self.ttl_csv_file}.")
                    except Exception as e:
                        pass
                
                asset.price_trajectory =[] 
                
                await session.commit()
                asset.is_in_position = False
                asset.current_trade = None

                today_str = datetime.utcnow().strftime("%Y-%m-%d")
                if asset.last_trade_date != today_str:
                    asset.consecutive_losses = 0
                    asset.last_trade_date = today_str

                if pnl < 0:
                    asset.consecutive_losses += 1
                    if asset.consecutive_losses >= 2:
                        print(f"💀 KILL-SWITCH DIARIO [{symbol}]: 2 pérdidas consecutivas. Baneado por 12 horas.")
                        asset.cooldown_until = time.time() + (12 * 3600) 
                    else:
                        print(f"⏳ COOLDOWN[{symbol}]: Pérdida registrada. Pausa obligatoria de 1 Hora.")
                        asset.cooldown_until = time.time() + 3600 
                else:
                    asset.consecutive_losses = 0 
                    print(f"⏳ COOLDOWN [{symbol}]: Ganancia asegurada. Pausa de 1 Hora.")
                    asset.cooldown_until = time.time() + 3600