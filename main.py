import asyncio
import sys
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from database.session import init_db
from core.engine import BotCore
from api.routes import router as api_router
import api.routes

# Configuración del ciclo de vida de la aplicación
@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- INICIO (STARTUP) ---
    print("🚀 Inicializando Sistema AI Crypto Bot...")
    
    # 1. Base de Datos
    await init_db()
    
    # 2. Inicializar Core del Bot
    # CORRECCIÓN FASE 10: Ya no pasamos argumentos. 
    # El bot lee SYMBOL y TIMEFRAME desde el archivo .env
    bot = BotCore() 
    
    # Inyectar la instancia del bot en las rutas de la API
    api.routes.bot_instance = bot
    
    # 3. Arrancar el Bot como tarea de fondo
    bot_task = asyncio.create_task(bot.start())
    
    print("✅ API REST y Core Engine corriendo en paralelo.")
    yield
    
    # --- CIERRE (SHUTDOWN) ---
    print("🛑 Deteniendo sistema...")
    bot_task.cancel()
    await bot.client.close_connection()
    print("Sistema apagado correctamente.")

# Crear aplicación FastAPI
app = FastAPI(
    title="AI Crypto Bot API",
    version="2.5.0",
    lifespan=lifespan
)

# Configurar CORS (Para que el Frontend React funcione)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Registrar rutas
app.include_router(api_router, prefix="/api/v1")

@app.get("/")
async def root():
    return {"message": "AI Crypto Trading Bot v2.5 is RUNNING"}

if __name__ == "__main__":
    # Ajuste para Windows y WebSockets
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        
    # Iniciar servidor
    uvicorn.run("main:app", host="0.0.0.0", port=8006, reload=True)