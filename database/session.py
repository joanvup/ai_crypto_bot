import os
from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
import urllib.parse

# Cargar variables de entorno
load_dotenv()

DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT")
DB_NAME = os.getenv("DB_NAME")

# Codificamos la contraseña para que caracteres como @ o # no rompan la URL
safe_password = urllib.parse.quote_plus(DB_PASSWORD)

# Construimos la URL usando la contraseña codificada
DATABASE_URL = f"postgresql+asyncpg://{DB_USER}:{safe_password}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
# Crear el motor asíncrono
engine = create_async_engine(DATABASE_URL, echo=False, future=True)

# Configurar la fábrica de sesiones
AsyncSessionLocal = sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)

Base = declarative_base()

async def get_db():
    """Generador asíncrono para obtener la sesión de BD."""
    async with AsyncSessionLocal() as session:
        yield session

async def init_db():
    """Crea las tablas en la base de datos si no existen."""
    async with engine.begin() as conn:
        # Importar modelos aquí para asegurar que Base.metadata los reconozca
        import database.models
        await conn.run_sync(Base.metadata.create_all)