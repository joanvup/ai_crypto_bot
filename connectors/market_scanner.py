import os
import asyncio
import ccxt.async_support as ccxt
from dotenv import load_dotenv

load_dotenv()

class MarketScanner:
    def __init__(self):
        self.environment = os.getenv("ENVIRONMENT", "testnet").lower()
        self.funding_tolerance = float(os.getenv("MAX_FUNDING_RATE_TOLERANCE", "0.05")) / 100.0
        
        self.exchange = ccxt.binance({
            'enableRateLimit': True,
            'options': {
                'defaultType': 'future',
            }
        })
        
        if self.environment == 'testnet':
            self.exchange.enable_demo_trading(True)

    async def get_top_assets(self, limit: int = 10) -> list:
        print(f"🌍 Escaneando mercado global de Binance Futuros (TOP {limit})...")
        try:
            await self.exchange.load_markets()
            tickers = await self.exchange.fetch_tickers()
            
            try:
                funding_rates = await self.exchange.fetch_funding_rates()
            except:
                funding_rates = {}
                
            # --- NUEVO: LEER LA LISTA NEGRA DEL .ENV ---
            blacklist_str = os.getenv("BLACKLISTED_ASSETS", "")
            blacklist =[sym.strip().upper() for sym in blacklist_str.split(',')] if blacklist_str else[]
            
            valid_pairs =[]
            
            for symbol, data in tickers.items():
                if symbol.endswith(':USDT'):
                    clean_symbol = symbol.split(':')[0]
                    
                    # 1. FILTRO DE LISTA NEGRA (El guardia de seguridad)
                    if clean_symbol in blacklist:
                        continue # Es una moneda prohibida, la saltamos
                    
                    market = self.exchange.markets.get(symbol, {})
                    info = market.get('info', {})
                    status = info.get('status', '')
                    is_active = market.get('active', False)
                    
                    if is_active and status == 'TRADING':
                        volume = float(data.get('quoteVolume', 0.0))
                        
                        if volume > 0:
                            # 2. FILTRO ANTI-FUNDING
                            f_rate_data = funding_rates.get(symbol, {})
                            f_rate = abs(float(f_rate_data.get('fundingRate', 0.0)))
                            
                            if f_rate <= self.funding_tolerance:
                                valid_pairs.append({'symbol': clean_symbol, 'volume': volume})
            
            # Ordenar de mayor a menor volumen y cortar el Top
            valid_pairs.sort(key=lambda x: x['volume'], reverse=True)
            top_assets = [pair['symbol'] for pair in valid_pairs[:limit]]
            
            print(f"✅ Top {limit} Activos (Aplicando Lista Negra) seleccionados: {', '.join(top_assets)}")
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
        top_10 = await scanner.get_top_assets(limit=20)
        print("\nLista final para el bot:", top_10)

    import sys
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        
    asyncio.run(test_scanner())