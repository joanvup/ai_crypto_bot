import os
import asyncio
import ccxt.async_support as ccxt
from dotenv import load_dotenv

load_dotenv()

class MarketScanner:
    def __init__(self):
        self.environment = os.getenv("ENVIRONMENT", "testnet").lower()
        
        # Inicializamos un cliente CCXT ligero solo para escanear mercados
        self.exchange = ccxt.binance({
            'enableRateLimit': True,
            'options': {
                'defaultType': 'future',
                'adjustForTimeDifference': True, # <--- EL ESCUDO FALTANTE AQUÍ
            }
        })
        
        if self.environment == 'testnet':
            self.exchange.set_sandbox_mode(True)

    async def get_top_assets(self, limit: int = 10) -> list:
        """
        Descarga todos los tickers de Binance Futuros, los filtra y 
        devuelve los 'limit' activos con mayor volumen en USDT.
        """
        print(f"🌍 Escaneando mercado global de Binance Futuros para obtener el TOP {limit}...")
        try:
            # fetch_tickers devuelve el estado de 24h de TODOS los pares
            tickers = await self.exchange.fetch_tickers()
            
            valid_pairs =[]
            
            for symbol, data in tickers.items():
                # En Futuros, CCXT devuelve los símbolos como 'BTC/USDT:USDT'
                # Filtramos para asegurarnos de que sean contratos perpetuos de USDT válidos
                if symbol.endswith(':USDT') and data.get('quoteVolume') is not None:
                    # --- NUEVO: FILTROS ANTI-ZOMBIES ---
                    # Si Binance reporta la moneda como inactiva o su precio es 0, la ignoramos.
                    if data.get('active') is False: continue
                    if data.get('last', 0) <= 0: continue
                    # Limpiamos el nombre para que quede como 'BTC/USDT'
                    clean_symbol = symbol.split(':')[0]
                    
                    valid_pairs.append({
                        'symbol': clean_symbol,
                        'volume': float(data['quoteVolume'])
                    })
            
            # Ordenar de mayor a menor según el volumen en USDT (Liquidez/MarketCap proxy)
            valid_pairs.sort(key=lambda x: x['volume'], reverse=True)
            
            # Extraer solo los nombres de los primeros 'limit' activos
            top_assets = [pair['symbol'] for pair in valid_pairs[:limit]]
            
            print(f"✅ Top {limit} Activos seleccionados por liquidez: {', '.join(top_assets)}")
            return top_assets
            
        except Exception as e:
            print(f"❌ Error escaneando el mercado: {e}")
            # Fallback de emergencia si Binance falla
            return ['BTC/USDT', 'ETH/USDT'] 
        finally:
            await self.exchange.close()

# Bloque de prueba local rápida
if __name__ == "__main__":
    async def test_scanner():
        scanner = MarketScanner()
        top_10 = await scanner.get_top_assets(limit=10)
        print("\nLista final para el bot:", top_10)

    # Parche para Windows
    import sys
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        
    asyncio.run(test_scanner())