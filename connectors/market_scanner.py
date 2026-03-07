import os
import asyncio
import ccxt.async_support as ccxt
from dotenv import load_dotenv

load_dotenv()

class MarketScanner:
    def __init__(self):
        self.environment = os.getenv("ENVIRONMENT", "testnet").lower()
        
        self.exchange = ccxt.binance({
            'enableRateLimit': True,
            'options': {
                'defaultType': 'future',
            }
        })
        
        if self.environment == 'testnet':
            self.exchange.set_sandbox_mode(True)

    async def get_top_assets(self, limit: int = 10) -> list:
        """
        Descarga todos los tickers de Binance Futuros, filtra los inactivos/deslistados,
        y devuelve los 'limit' activos con mayor volumen en USDT.
        """
        print(f"🌍 Escaneando mercado global de Binance Futuros para obtener el TOP {limit}...")
        try:
            # 1. OBLIGATORIO: Cargar mercados primero para conocer el estado real de cada moneda
            await self.exchange.load_markets()
            
            # 2. Descargar todos los tickers
            tickers = await self.exchange.fetch_tickers()
            
            valid_pairs =[]
            
            for symbol, data in tickers.items():
                if symbol.endswith(':USDT'):
                    # Buscar las propiedades del mercado en la base de datos de CCXT
                    market = self.exchange.markets.get(symbol, {})
                    
                    # Extraer el estado original de Binance
                    info = market.get('info', {})
                    status = info.get('status', '')
                    is_active = market.get('active', False)
                    
                    # 3. FILTRO ESTRICTO: Solo monedas que están operando AHORA MISMO
                    if is_active and status == 'TRADING':
                        volume = float(data.get('quoteVolume', 0.0))
                        
                        # 4. Filtro de liquidez real
                        if volume > 0:
                            clean_symbol = symbol.split(':')[0]
                            valid_pairs.append({
                                'symbol': clean_symbol,
                                'volume': volume
                            })
            
            # Ordenar de mayor a menor según el volumen en USDT
            valid_pairs.sort(key=lambda x: x['volume'], reverse=True)
            
            # Extraer solo los nombres
            top_assets = [pair['symbol'] for pair in valid_pairs[:limit]]
            
            print(f"✅ Top {limit} Activos 100% TRADING seleccionados: {', '.join(top_assets)}")
            return top_assets
            
        except Exception as e:
            print(f"❌ Error escaneando el mercado: {e}")
            return ['BTC/USDT', 'ETH/USDT'] 
        finally:
            await self.exchange.close()

# Bloque de prueba local rápida
if __name__ == "__main__":
    async def test_scanner():
        scanner = MarketScanner()
        top_10 = await scanner.get_top_assets(limit=10)
        print("\nLista final para el bot:", top_10)

    import sys
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        
    asyncio.run(test_scanner())