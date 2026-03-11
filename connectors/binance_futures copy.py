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
            self.exchange.set_sandbox_mode(True)

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
        EJECUCIÓN RÁPIDA SECUENCIAL.
        Binance prohíbe enviar Stop Loss y Take Profit en el endpoint de Batch Orders.
        Esta función ejecuta la entrada y luego dispara las protecciones concurrentemente.
        """
        if self.environment == 'dry_run':
            return[{"orderId": "sim_entry"}, {"orderId": "sim_sl"}, {"orderId": "sim_tp"}]

        symbol_bin = symbol.replace('/', '')
        exit_side = 'SELL' if side == 'BUY' else 'BUY'
        
        # Formatear precisión
        q = self.exchange.amount_to_precision(symbol, amount)
        sl_trigger = self.exchange.price_to_precision(symbol, sl_price)
        tp_trigger = self.exchange.price_to_precision(symbol, tp_price)

        print(f"   📦 Ejecutando Entrada a Mercado ({side})...")
        try:
            # 1. EJECUTAR ENTRADA A MERCADO (Aislada para no romper la regla Batch)
            entry_res = await self.exchange.fapiprivate_post_order({
                'symbol': symbol_bin,
                'side': side,
                'type': 'MARKET',
                'quantity': q
            })
            
            # Si la entrada falló (ej. Saldo insuficiente), abortamos inmediatamente
            if 'code' in entry_res:
                return [entry_res]
            
            print(f"   🛡️ Entrada exitosa. Disparando Hard Stops...")
            
            # 2. DISPARAR STOPS AL MISMO TIEMPO (Concurrencia)
            # Usamos STOP_MARKET y closePosition='true' (El estándar oficial de Binance)
            sl_task = self.exchange.fapiprivate_post_order({
                'symbol': symbol_bin,
                'side': exit_side,
                'type': 'STOP_MARKET',
                'stopPrice': sl_trigger,
                'closePosition': 'true',
                'workingType': 'MARK_PRICE'
            })
            
            tp_task = self.exchange.fapiprivate_post_order({
                'symbol': symbol_bin,
                'side': exit_side,
                'type': 'TAKE_PROFIT_MARKET',
                'stopPrice': tp_trigger,
                'closePosition': 'true',
                'workingType': 'MARK_PRICE'
            })
            
            # Esperamos a que ambos respondan. Si uno da error de API, lo atrapamos sin romper Python.
            stops_res = await asyncio.gather(sl_task, tp_task, return_exceptions=True)
            
            # Formatear respuestas para que el engine.py las entienda
            sl_res = stops_res[0] if isinstance(stops_res[0], dict) else {'code': -1, 'msg': str(stops_res[0])}
            tp_res = stops_res[1] if isinstance(stops_res[1], dict) else {'code': -1, 'msg': str(stops_res[1])}
            
            # Devolver el formato exacto que espera el Motor
            return[entry_res, sl_res, tp_res]

        except Exception as e:
            print(f"❌ Error crítico en ejecución rápida: {e}")
            return[{'code': -1, 'msg': str(e)}]

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

    async def panic_close_position(self, symbol: str, side: str):
        """
        Cierra una posición abierta a mercado de forma bruta usando closePosition.
        Usado por el Orphan Sweeper para limpiar operaciones no registradas.
        """
        if self.environment == 'dry_run': return True
        try:
            symbol_bin = symbol.replace('/', '')
            exit_side = 'SELL' if side == 'LONG' else 'BUY'
            
            # CORRECCIÓN: Binance prohíbe usar 'reduceOnly' si ya se está usando 'closePosition'
            params = {
                'symbol': symbol_bin,
                'side': exit_side,
                'type': 'MARKET',
                'closePosition': 'true' # Esto es suficiente para cerrar el 100%
            }
            print(f"   🧹 Enviando Panic Close a Binance para {symbol} ({exit_side})...")
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