import os
import json
import asyncio
import websockets
from dotenv import load_dotenv

load_dotenv()

class BinanceWebSocket:
    def __init__(self):
        self.environment = os.getenv("ENVIRONMENT", "testnet").lower()
        
        # URL base de WebSockets (Testnet vs Real)
        # En Dry Run usamos la red Real para tener datos precisos
        if self.environment == 'testnet':
            self.base_url = "wss://stream.binancefuture.com/ws"
        else:
            self.base_url = "wss://fstream.binance.com/ws"
            
        self.active_connections = []

    async def subscribe_ticker(self, symbol: str, callback):
        """
        Se suscribe al flujo de precios en tiempo real.
        El callback es una función que procesará los datos al recibirlos.
        """
        stream_url = f"{self.base_url}/{symbol.lower()}@ticker"
        self.active_connections.append(stream_url)
        
        print(f"Iniciando WebSocket para {symbol} en {stream_url}")
        
        async for websocket in websockets.connect(stream_url):
            try:
                async for message in websocket:
                    data = json.loads(message)
                    # Extraer el último precio ('c' = current price en Binance)
                    current_price = float(data['c'])
                    # Llamar a la función del Core de manera asíncrona
                    await callback(symbol, current_price)
            except websockets.ConnectionClosed:
                print(f"Conexión WS cerrada para {symbol}. Reconectando...")
                await asyncio.sleep(1) # Backoff antes de reconectar
            except Exception as e:
                print(f"Error en WS {symbol}: {e}")
                await asyncio.sleep(1)