import os
import pandas as pd
import numpy as np
import joblib
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score

class AIPredictor:
    def __init__(self, symbol: str, timeframe: str):
        self.symbol = symbol.replace('/', '').replace('-', '') # Ej: BTCUSDT
        self.timeframe = timeframe
        self.model_path = f"ai_engine/saved_models/rf_{self.symbol}_{self.timeframe}.joblib"
        self.model = None
        self.features = None # Inicializamos la variable en memoria

    def create_labels(self, df: pd.DataFrame, threshold: float = 0.0015) -> pd.DataFrame:
        df['future_return'] = df['close'].shift(-1) / df['close'] - 1
        
        df['target'] = 0 # Por defecto: Hold / Neutral
        df.loc[df['future_return'] > threshold, 'target'] = 1  # Long
        df.loc[df['future_return'] < -threshold, 'target'] = -1 # Short
        
        return df

    def train(self, df: pd.DataFrame):
        print(f"[{self.symbol}] Preparando datos para entrenamiento...")
        
        df = self.create_labels(df.copy())
        df.dropna(inplace=True)

        exclude_cols = ['open', 'high', 'low', 'close', 'volume', 'future_return', 'target']
        
        # CORRECCIÓN AQUÍ: Asignamos a self.features para que viva en la memoria del bot
        self.features = [col for col in df.columns if col not in exclude_cols]
        
        X = df[self.features]
        y = df['target']

        X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, shuffle=False)

        print(f"Entrenando Random Forest con {len(X_train)} muestras y {len(self.features)} características...")
        
        self.model = RandomForestClassifier(
            n_estimators=100, 
            max_depth=10, 
            random_state=42, 
            class_weight='balanced'
        )
        self.model.fit(X_train, y_train)

        y_pred = self.model.predict(X_test)
        acc = accuracy_score(y_test, y_pred)
        print(f"✅ Entrenamiento completado. Precisión en datos de prueba (Accuracy): {acc * 100:.2f}%")
        
        os.makedirs(os.path.dirname(self.model_path), exist_ok=True)
        # Guardamos en disco usando self.features
        joblib.dump((self.model, self.features), self.model_path)
        print(f"💾 Modelo guardado en {self.model_path}")

    def predict(self, current_state_df: pd.DataFrame):
        if self.model is None or self.features is None:
            if os.path.exists(self.model_path):
                self.model, self.features = joblib.load(self.model_path)
            else:
                raise Exception("El modelo no está entrenado. Llama a train() primero.")

        last_row = current_state_df.iloc[-1:]
        X_current = last_row[self.features]

        prediction = self.model.predict(X_current)[0]
        
        probabilities = self.model.predict_proba(X_current)[0]
        confidence = max(probabilities) * 100

        return prediction, confidence