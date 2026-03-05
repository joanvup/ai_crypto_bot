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
            print(f"Error obteniendo balance: {e}")
            return None

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
        ENVÍA ENTRADA, SL Y TP EN UN SOLO LOTE (BATCH).
        CORRECCIÓN: Sin json.dumps manual para evitar doble serialización.
        """
        if self.environment == 'dry_run':
            return [{"orderId": "sim_entry"}, {"orderId": "sim_sl"}, {"orderId": "sim_tp"}]

        symbol_bin = symbol.replace('/', '')
        exit_side = 'SELL' if side == 'BUY' else 'BUY'
        
        # Formatear con precisión de Binance
        q = self.exchange.amount_to_precision(symbol, amount)
        sl_trigger = self.exchange.price_to_precision(symbol, sl_price)
        tp_trigger = self.exchange.price_to_precision(symbol, tp_price)

        # Precio Límite para el SL y TP (Margen del 1% para asegurar ejecución)
        if side == 'BUY':
            sl_limit = self.exchange.price_to_precision(symbol, float(sl_trigger) * 0.99)
            tp_limit = self.exchange.price_to_precision(symbol, float(tp_trigger) * 1.01)
        else:
            sl_limit = self.exchange.price_to_precision(symbol, float(sl_trigger) * 1.01)
            tp_limit = self.exchange.price_to_precision(symbol, float(tp_trigger) * 0.99)

        # EL PAYLOAD: Debe ser una LISTA de diccionarios (CCXT se encarga del resto)
        orders_list = [
            {
                'symbol': symbol_bin,
                'side': side,
                'type': 'MARKET',
                'quantity': q
            },
            {
                'symbol': symbol_bin,
                'side': exit_side,
                'type': 'STOP', # STOP_LIMIT es aceptado en lotes
                'quantity': q,
                'price': sl_limit,
                'stopPrice': sl_trigger,
                'reduceOnly': 'true'
            },
            {
                'symbol': symbol_bin,
                'side': exit_side,
                'type': 'TAKE_PROFIT', # TAKE_PROFIT_LIMIT es aceptado en lotes
                'quantity': q,
                'price': tp_limit,
                'stopPrice': tp_trigger,
                'reduceOnly': 'true'
            }
        ]

        try:
            print(f"   📦 Enviando BATCH ORDER Atómico ({side})...")
            # IMPORTANTE: Pasamos la lista directamente, NO un string JSON
            return await self.exchange.fapiprivate_post_batchorders({
                'batchOrders': orders_list 
            })
        except Exception as e:
            print(f"❌ Error crítico en Batch Order: {e}")
            return None

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

    async def get_open_positions(self) -> list:
        """
        Consulta directamente a Binance qué posiciones reales están abiertas.
        Devuelve una lista limpia de símbolos (Ej: ['BTC/USDT', 'ETH/USDT']).
        """
        if self.environment == 'dry_run':
            return[] # En dry_run, Binance no sabe nada de nuestras posiciones
            
        try:
            positions = await self.exchange.fetch_positions()
            active_symbols =[]
            
            for p in positions:
                # CCXT guarda el tamaño de la posición en 'contracts'
                if float(p.get('contracts', 0)) > 0:
                    # CCXT devuelve 'BTC/USDT:USDT', lo limpiamos a 'BTC/USDT'
                    clean_symbol = p['symbol'].split(':')[0]
                    active_symbols.append(clean_symbol)
                    
            return active_symbols
        except Exception as e:
            print(f"❌ Error consultando posiciones vivas en Binance: {e}")
            return[]
    
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

    async def get_top_assets(self, limit: int = 10) -> list:
        """
        Escanea el mercado usando la conexión autenticada principal para evitar bloqueos.
        """
        print(f"🌍 Escaneando mercado global de Binance Futuros para obtener el TOP {limit}...")
        try:
            tickers = await self.exchange.fetch_tickers()
            valid_pairs =[]
            
            for symbol, data in tickers.items():
                if symbol.endswith(':USDT') and data.get('quoteVolume') is not None:
                    # Filtros anti-zombies
                    if data.get('active') is False: continue
                    if data.get('last', 0) <= 0: continue
                    
                    clean_symbol = symbol.split(':')[0]
                    valid_pairs.append({
                        'symbol': clean_symbol,
                        'volume': float(data['quoteVolume'])
                    })
            
            valid_pairs.sort(key=lambda x: x['volume'], reverse=True)
            top_assets = [pair['symbol'] for pair in valid_pairs[:limit]]
            
            print(f"✅ Top {limit} Activos seleccionados: {', '.join(top_assets)}")
            return top_assets
            
        except Exception as e:
            # SI FALLA AQUÍ, IMPRIMIMOS EL ERROR GIGANTE PARA VER QUÉ DICE BINANCE
            print(f"\n❌ ERROR CRÍTICO EN ESCÁNER DE MERCADO: {e}\n")
            return['BTC/USDT', 'ETH/USDT']