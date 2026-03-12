import os
import asyncio
import ccxt.async_support as ccxt
from dotenv import load_dotenv

load_dotenv()

class MarketScanner:
    def __init__(self):
        self.environment = os.getenv("ENVIRONMENT", "testnet").lower()
        # Convertimos 0.05 a 0.0005 (formato decimal matemático)
        self.funding_tolerance = float(os.getenv("MAX_FUNDING_RATE_TOLERANCE", "0.05")) / 100.0
        
        self.exchange = ccxt.binance({
            'enableRateLimit': True,
            'options': {'defaultType': 'future'}
        })
        if self.environment == 'testnet':
            self.exchange.enable_demo_trading(True) # <--- NUEVA FORMA DE BINANCE DEMO

    async def get_top_assets(self, limit: int = 10) -> list:
        print(f"🌍 Escaneando mercado global de Binance Futuros (TOP {limit})...")
        try:
            await self.exchange.load_markets()
            # fetch_tickers trae el volumen y también el bid/ask actual
            tickers = await self.exchange.fetch_tickers()
            
            try:
                funding_rates = await self.exchange.fetch_funding_rates()
            except:
                funding_rates = {}
            
            valid_pairs =[]
            
            for symbol, data in tickers.items():
                if symbol.endswith(':USDT'):
                    market = self.exchange.markets.get(symbol, {})
                    info = market.get('info', {})
                    status = info.get('status', '')
                    is_active = market.get('active', False)
                    
                    if is_active and status == 'TRADING':
                        volume = float(data.get('quoteVolume', 0.0))
                        
                        # --- NUEVO ESCUDO: EL DETECTOR DE MENTIRAS DEL TESTNET ---
                        # Además de volumen, verificamos que el libro de órdenes tenga vida
                        bid = float(data.get('bid', 0.0) or 0.0)
                        ask = float(data.get('ask', 0.0) or 0.0)
                        
                        if volume > 0 and bid > 0 and ask > 0:
                            # Filtro Anti-Funding
                            f_rate_data = funding_rates.get(symbol, {})
                            f_rate = abs(float(f_rate_data.get('fundingRate', 0.0)))
                            
                            if f_rate > self.funding_tolerance:
                                continue 
                                
                            clean_symbol = symbol.split(':')[0]
                            valid_pairs.append({'symbol': clean_symbol, 'volume': volume})
            
            valid_pairs.sort(key=lambda x: x['volume'], reverse=True)
            top_assets = [pair['symbol'] for pair in valid_pairs[:limit]]
            
            print(f"✅ Top {limit} Activos (Limpios y 100% Líquidos): {', '.join(top_assets)}")
            return top_assets
            
        except Exception as e:
            print(f"❌ Error escaneando el mercado: {e}")
            return['BTC/USDT', 'ETH/USDT'] 
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