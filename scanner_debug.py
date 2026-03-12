import asyncio
from connectors.market_scanner import MarketScanner
from connectors.binance_futures import BinanceFuturesClient

async def debug_scanner():
    print("🔎 INICIANDO DIAGNÓSTICO DE MERCADO...")
    scanner = MarketScanner()
    client = BinanceFuturesClient()
    
    # 1. Obtener la lista de activos que el bot ve
    top_assets = await scanner.get_top_assets(limit=20)
    print(f"✅ Activos detectados: {top_assets}\n")
    
    # 2. Testear conectividad de Bid/Ask para cada uno
    print(f"{'SÍMBOLO':<15} | {'BID':<15} | {'ASK':<15} | {'ESTADO'}")
    print("-" * 65)
    
    for symbol in top_assets:
        bid, ask = await client.get_bid_ask(symbol)
        
        estado = "OK"
        if bid == 0 or ask == 0:
            estado = "⚠️ NO LIQUIDO / NO PERMITIDO"
        
        print(f"{symbol:<15} | {bid:<15} | {ask:<15} | {estado}")

    await client.close_connection()
    print("\n🏁 Diagnóstico finalizado.")

if __name__ == "__main__":
    import sys
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(debug_scanner())