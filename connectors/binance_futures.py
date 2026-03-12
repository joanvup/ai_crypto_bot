import os
import asyncio
import ccxt.async_support as ccxt
from dotenv import load_dotenv

load_dotenv()

class BinanceFuturesClient:
    def __init__(self):
        self.environment = os.getenv("ENVIRONMENT", "testnet").lower()
        self.api_key = os.getenv("BINANCE_API_KEY")
        self.secret_key = os.getenv("BINANCE_SECRET_KEY")
        
        self.exchange = ccxt.binance({
            'apiKey': self.api_key,
            'secret': self.secret_key,
            'enableRateLimit': True,
            'options': {'defaultType': 'future', 'adjustForTimeDifference': True}
        })
        if self.environment == 'testnet':
            self.exchange.enable_demo_trading(True) # <--- NUEVA FORMA DE BINANCE DEMO

    async def get_balance(self):
        """Obtiene el balance de la cuenta. En dry_run lee del .env"""
        if self.environment == 'dry_run':
            # Cero hardcoding: leemos el balance simulado del entorno
            simulated_balance = float(os.getenv("DRY_RUN_INITIAL_BALANCE", "1000.0"))
            return {"total": simulated_balance, "free": simulated_balance}
            
        try:
            balance = await self.exchange.fetch_balance()
            if 'USDT' in balance:
                return {"total": float(balance['USDT']['total']), "free": float(balance['USDT']['free'])}
            return {"total": 0.0, "free": 0.0}
        except Exception as e:
            # --- AUTO-SANACIÓN DEL RELOJ ---
            if "-1021" in str(e):
                print("⏱️ Desfase detectado en balance. Resincronizando reloj de CCXT...")
                asyncio.create_task(self.exchange.load_time_difference())
            else:
                print(f"⚠️ Error balance: {e}")
            return {"total": 0.0, "free": 0.0}

    async def get_funding_rate(self, symbol: str) -> float:
        """
        Consulta la tasa de financiación actual (Funding Rate) de la moneda.
        """
        try:
            # fetch_funding_rate nos da la tasa actual en tiempo real
            res = await self.exchange.fetch_funding_rate(symbol)
            return float(res.get('fundingRate', 0.0))
        except Exception as e:
            print(f"⚠️ Aviso: No se pudo obtener funding rate para {symbol}")
            return 0.0

    async def get_historical_klines(self, symbol: str, timeframe: str, limit: int):
        try:
            # MOVER EL PRINT AQUÍ ADENTRO
            if limit > 1000: 
                print(f"⏳ Descargando {limit} velas desde Binance ({self.environment})...")
                
            if limit <= 1000: 
                return await self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            
            all_klines =[]
            timeframe_ms = self.exchange.parse_timeframe(timeframe) * 1000
            since = self.exchange.milliseconds() - (limit * timeframe_ms)
            
            while len(all_klines) < limit:
                batch = await self.exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=1000)
                if not batch: break
                all_klines.extend(batch)
                since = batch[-1][0] + 1
                await asyncio.sleep(0.2) # Pausa para no saturar la API real
                
            print(f"   ✅ Descarga exitosa: {len(all_klines)} velas.")
            return all_klines[-limit:]
        except Exception as e:
            # AHORA SÍ VEREMOS POR QUÉ BINANCE RECHAZA LA CONEXIÓN
            print(f"❌ Error CRÍTICO descargando velas: {e}")
            return

    async def place_atomic_trade(self, symbol, side, amount, sl_price, tp_price):
        """
        EJECUCIÓN RÁPIDA SECUENCIAL CON LIMIT STOPS.
        Evita el error -4120 usando STOP y TAKE_PROFIT (Limit) en lugar de Market.
        """
        if self.environment == 'dry_run':
            return[{"orderId": "sim_entry"}, {"orderId": "sim_sl"}, {"orderId": "sim_tp"}]

        exit_side = 'SELL' if side == 'BUY' else 'BUY'
        
        # 1. Formatear precisión exacta de Binance
        q = float(self.exchange.amount_to_precision(symbol, amount))
        sl_trigger = float(self.exchange.price_to_precision(symbol, sl_price))
        tp_trigger = float(self.exchange.price_to_precision(symbol, tp_price))

        # 2. Calcular los Precios Límite (1% de Slippage para asegurar ejecución instantánea)
        if side == 'BUY':
            # LONG: SL está abajo (Límite peor que trigger), TP está arriba (Límite peor que trigger)
            sl_limit = float(self.exchange.price_to_precision(symbol, sl_trigger * 0.99))
            tp_limit = float(self.exchange.price_to_precision(symbol, tp_trigger * 0.99))
        else:
            # SHORT: SL está arriba (Límite peor), TP está abajo (Límite peor)
            sl_limit = float(self.exchange.price_to_precision(symbol, sl_trigger * 1.01))
            tp_limit = float(self.exchange.price_to_precision(symbol, tp_trigger * 1.01))

        print(f"   📦 Ejecutando Entrada a Mercado ({side})...")
        try:
            # --- 1. EJECUTAR ENTRADA (CCXT Nativo) ---
            entry_res = await self.exchange.create_order(
                symbol=symbol,
                type='MARKET',
                side=side,
                amount=q
            )
            
            # Si falla la entrada por saldo u otro error, abortamos
            if 'info' in entry_res and entry_res.get('info', {}).get('code'):
                return [{'code': entry_res['info']['code'], 'msg': entry_res['info'].get('msg')}]
            
            print(f"   🛡️ Entrada exitosa. Disparando Hard Stops (Limit)...")
            
            # --- 2. DISPARAR PROTECCIONES CONCURRENTES (CCXT Nativo) ---
            # Usar create_order de CCXT asegura que los endpoints se mapeen correctamente
            sl_task = self.exchange.create_order(
                symbol=symbol,
                type='STOP', # Es un Stop-Limit
                side=exit_side,
                amount=q,
                price=sl_limit, # Precio en el libro
                params={'stopPrice': sl_trigger, 'reduceOnly': True} # Disparador
            )
            
            tp_task = self.exchange.create_order(
                symbol=symbol,
                type='TAKE_PROFIT', # Es un Take-Profit-Limit
                side=exit_side,
                amount=q,
                price=tp_limit, # Precio en el libro
                params={'stopPrice': tp_trigger, 'reduceOnly': True} # Disparador
            )
            
            # Esperar a que Binance responda a ambos
            stops_res = await asyncio.gather(sl_task, tp_task, return_exceptions=True)
            
            # Formatear errores si los hay para el "Scanner de Rechazos"
            sl_res = stops_res[0] if isinstance(stops_res[0], dict) else {'code': -1, 'msg': str(stops_res[0])}
            tp_res = stops_res[1] if isinstance(stops_res[1], dict) else {'code': -1, 'msg': str(stops_res[1])}
            
            # Devolver el lote de respuestas para la Base de Datos
            # Mapeamos 'id' a 'orderId' para mantener compatibilidad con tu motor
            if 'id' in entry_res: entry_res['orderId'] = entry_res['id']
            if 'id' in sl_res: sl_res['orderId'] = sl_res['id']
            if 'id' in tp_res: tp_res['orderId'] = tp_res['id']

            return[entry_res, sl_res, tp_res]

        except Exception as e:
            print(f"❌ Error crítico en ejecución rápida: {e}")
            return [{'code': -1, 'msg': str(e)}]

    async def place_order(self, symbol, side, amount):
        try:
            amount_prec = self.exchange.amount_to_precision(symbol, amount)
            return await self.exchange.create_order(symbol, 'MARKET', side, float(amount_prec))
        except: return None

    async def cancel_order(self, symbol, order_id):
        try:
            return await self.exchange.fapiprivate_delete_order({
                'symbol': symbol.replace('/', ''), 
                'orderId': order_id
            })
        except: return False

    async def get_open_positions(self) -> dict:
        """
        Consulta las posiciones reales abiertas en Binance.
        Devuelve un diccionario: {'BTC/USDT': {'side': 'LONG', 'contracts': 0.5}}
        """
        if self.environment == 'dry_run':
            return {} 
            
        try:
            positions = await self.exchange.fetch_positions()
            active_positions = {}
            
            for p in positions:
                contracts = float(p.get('contracts', 0))
                if contracts > 0:
                    clean_symbol = p['symbol'].split(':')[0]
                    # CCXT devuelve 'long' o 'short'
                    side = p.get('side', 'long').upper()
                    active_positions[clean_symbol] = {
                        'side': side,
                        'contracts': contracts
                    }
            return active_positions
        except Exception as e:
            # --- AUTO-SANACIÓN DEL RELOJ ---
            if "-1021" in str(e):
                print("⏱️ Desfase detectado en Sweeper. Resincronizando reloj de CCXT...")
                asyncio.create_task(self.exchange.load_time_difference())
            else:
                print(f"⚠️ Error consultando posiciones vivas: {e}")
            return {}

    async def panic_close_position(self, symbol: str, side: str, amount: float):
        """
        Cierra una posición a mercado usando la cantidad exacta y reduceOnly.
        Esto evita el error -4136 de Binance.
        """
        if self.environment == 'dry_run': return True
        try:
            symbol_bin = symbol.replace('/', '')
            exit_side = 'SELL' if side == 'LONG' else 'BUY'
            
            # Formatear la cantidad a la precisión exacta de Binance
            q = self.exchange.amount_to_precision(symbol, amount)
            
            params = {
                'symbol': symbol_bin,
                'side': exit_side,
                'type': 'MARKET',
                'quantity': q,
                'reduceOnly': 'true' # La forma correcta para Market orders
            }
            print(f"   🧹 Enviando Panic Close a Binance para {symbol} ({exit_side} {q})...")
            return await self.exchange.fapiprivate_post_order(params)
        except Exception as e:
            print(f"❌ Error al ejecutar Panic Close en {symbol}: {e}")
            return None
    
    async def close_connection(self):
        await self.exchange.close()

    async def fetch_order_details(self, symbol, order_id):
        """
        Consulta el estado real de una orden en Binance.
        (Con soporte para Dry Run)
        """
        # --- NUEVA LÓGICA DRY RUN ---
        if self.environment == 'dry_run':
            print(f"   [DRY RUN] Simulando detalles de la orden {order_id}...")
            return {
                'id': order_id,
                'status': 'closed',
                'average': None # Al no haber precio real, el bot usará el teórico
            }
            
        try:
            order = await self.exchange.fetch_order(order_id, symbol)
            return order
        except Exception as e:
            print(f"❌ Error consultando detalles de orden {order_id}: {e}")
            return None

    async def get_bid_ask(self, symbol: str):
        """
        Consulta la punta del libro de órdenes para conocer el Bid (Comprador) y Ask (Vendedor).
        Se usa para calcular el Spread antes de abrir una operación.
        """
        try:
            ticker = await self.exchange.fetch_ticker(symbol)
            bid = float(ticker.get('bid', 0.0))
            ask = float(ticker.get('ask', 0.0))
            return bid, ask
        except Exception as e:
            print(f"⚠️ Aviso: No se pudo obtener Bid/Ask para {symbol}")
            return 0.0, 0.0