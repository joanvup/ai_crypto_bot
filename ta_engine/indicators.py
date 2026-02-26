import pandas as pd
import pandas_ta as ta

class TAEngine:
    def __init__(self):
        """Inicializa el motor de análisis técnico."""
        pass

    def prepare_dataframe(self, raw_klines: list) -> pd.DataFrame:
        """
        Convierte la respuesta cruda de CCXT (OHLCV) a un Pandas DataFrame formateado.
        CCXT format: [timestamp, open, high, low, close, volume]
        """
        if not raw_klines:
            return pd.DataFrame()

        df = pd.DataFrame(raw_klines, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        
        # Convertir timestamp a DatetimeIndex
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)
        
        # Asegurar que los datos financieros sean flotantes (float64)
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = df[col].astype(float)
            
        return df

    def apply_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Aplica todos los indicadores técnicos necesarios para la IA y gestión de riesgo.
        """
        if df.empty or len(df) < 50:
            print("Advertencia: No hay suficientes datos para calcular indicadores.")
            return df

        # 1. Indicadores de Volatilidad (Crucial para Risk Management)
        df.ta.atr(length=14, append=True)           # Average True Range (Stop Loss Dinámico)
        df.ta.bbands(length=20, std=2, append=True) # Bollinger Bands

        # 2. Indicadores de Momentum
        df.ta.rsi(length=14, append=True)           # Relative Strength Index
        df.ta.macd(fast=12, slow=26, signal=9, append=True) # MACD

        # 3. Indicadores de Tendencia
        df.ta.ema(length=9, append=True)            # EMA Corta
        df.ta.ema(length=21, append=True)           # EMA Media
        df.ta.ema(length=50, append=True)           # EMA Larga (Dirección principal)
        df.ta.adx(length=14, append=True)           # Average Directional Index (Fuerza de la tendencia)

        # 4. Indicadores de Volumen
        df.ta.obv(append=True)                      # On-Balance Volume

        # Limpieza: Eliminar filas con valores NaN generados por el cálculo (ej. las primeras 50 velas)
        df.dropna(inplace=True)

        return df