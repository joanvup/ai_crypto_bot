class RiskManager:
    def __init__(self, risk_per_trade: float = 0.01, risk_reward_ratio: float = 2.0, max_drawdown: float = 0.10):
        """
        :param risk_per_trade: Porcentaje del capital a arriesgar por trade (0.01 = 1%).
        :param risk_reward_ratio: Ratio Riesgo/Beneficio (2.0 = Buscamos ganar 2 por cada 1 arriesgado).
        :param max_drawdown: Caída máxima permitida del capital antes de pausar el bot (0.10 = 10%).
        """
        self.risk_per_trade = risk_per_trade
        self.risk_reward_ratio = risk_reward_ratio
        self.max_drawdown = max_drawdown

    def check_drawdown(self, initial_balance: float, current_balance: float) -> bool:
        """
        Verifica si la cuenta ha superado el límite de pérdida (Drawdown).
        Devuelve True si es SEGURO operar. False si el bot DEBE PAUSARSE.
        """
        if initial_balance <= 0:
            return True
            
        drawdown = (initial_balance - current_balance) / initial_balance
        if drawdown >= self.max_drawdown:
            print(f"⚠️ ALERTA DE RIESGO: Drawdown de {drawdown*100:.2f}% superó el máximo de {self.max_drawdown*100:.2f}%.")
            return False
        return True

    def calculate_sl_tp(self, side: str, current_price: float, atr: float, sl_multiplier: float = 1.5):
        """
        Calcula el Stop Loss (SL) y Take Profit (TP) basado en la volatilidad (ATR).
        :param side: 'LONG' o 'SHORT'
        """
        if side == 'LONG':
            stop_loss = current_price - (atr * sl_multiplier)
            # TP = Entrada + (Distancia al SL * Risk/Reward)
            take_profit = current_price + ((current_price - stop_loss) * self.risk_reward_ratio)
            
        elif side == 'SHORT':
            stop_loss = current_price + (atr * sl_multiplier)
            take_profit = current_price - ((stop_loss - current_price) * self.risk_reward_ratio)
            
        else:
            return None, None

        # Redondear a 2 decimales para evitar problemas de precisión en Binance
        return round(stop_loss, 2), round(take_profit, 2)

    def calculate_position_size(self, balance: float, current_price: float, stop_loss: float) -> float:
        """
        Calcula la cantidad exacta de la criptomoneda (ej. BTC) a comprar,
        garantizando que si toca el SL, solo perdamos el % de riesgo definido.
        """
        if balance <= 0 or current_price == stop_loss:
            return 0.0

        # Cantidad de dólares que estamos dispuestos a perder en este trade
        capital_at_risk = balance * self.risk_per_trade
        
        # Distancia absoluta en dólares entre la entrada y el SL
        sl_distance = abs(current_price - stop_loss)
        
        # Tamaño de la posición en la criptomoneda (Fórmula de oro del trading cuantitativo)
        position_size = capital_at_risk / sl_distance
        
        # Redondeamos a 3 decimales (Binance requiere ciertos decimales mínimos según la moneda)
        return round(position_size, 3)