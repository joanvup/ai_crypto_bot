import asyncio
from connectors.binance_futures import BinanceFuturesClient

async def nuke_positions():
    print("🚀 Iniciando Francotirador de API (Versión ReduceOnly)...")
    client = BinanceFuturesClient()
    
    positions = await client.get_open_positions()
    
    if not positions:
        print("✅ Binance dice que no tienes posiciones abiertas. La web está mintiendo.")
    else:
        for symbol, data in positions.items():
            amount = data['contracts'] # Extraemos la cantidad exacta abierta
            print(f"🎯 Apuntando a {symbol} ({data['side']} | Cantidad: {amount})...")
            
            # Pasamos el símbolo, el lado y la cantidad
            res = await client.panic_close_position(symbol, data['side'], amount)
            
            if res and 'orderId' in res:
                print(f"💥 ¡BINGO! {symbol} cerrado por API (ID: {res['orderId']})")
            else:
                print(f"❌ Falló el cierre de {symbol}. Binance respondió: {res}")
                
    await client.close_connection()

if __name__ == "__main__":
    asyncio.run(nuke_positions())