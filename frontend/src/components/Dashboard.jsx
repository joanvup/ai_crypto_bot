import { useState, useEffect } from 'react';
import { getBotStatus, getBalance, getTrades } from '../services/api';

const Dashboard = () => {
  const [status, setStatus] = useState(null);
  const [balance, setBalance] = useState(null);
  const [trades, setTrades] = useState([]);
  const [lastUpdated, setLastUpdated] = useState(new Date());

  const fetchData = async () => {
    try {
      // Usamos Promise.all para cargar todo en paralelo
      const [statusData, balanceData, tradesData] = await Promise.all([
        getBotStatus(), 
        getBalance(), 
        getTrades()
      ]);

      if (statusData) setStatus(statusData);
      if (balanceData) setBalance(balanceData);
      if (tradesData) setTrades(tradesData);
      setLastUpdated(new Date());
    } catch (error) {
      console.error("Error actualizando dashboard:", error);
    }
  };

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 3000); // Actualizar cada 3 segundos
    return () => clearInterval(interval);
  }, []);

  if (!status || !balance) {
    return (
      <div className="min-h-screen bg-gray-900 flex flex-col items-center justify-center text-white">
        <div className="animate-spin rounded-full h-16 w-16 border-t-2 border-b-2 border-blue-500 mb-4"></div>
        <div className="text-xl font-mono animate-pulse">Conectando con Neural Core...</div>
      </div>
    );
  }

  // Cálculos auxiliares para estilos
  const isProfitable = status.open_trade?.pnl >= 0;
  const confidenceColor = status.ai_confidence >= status.ai_threshold ? 'bg-green-500' : 'bg-yellow-500';

  return (
    <div className="p-6 max-w-7xl mx-auto min-h-screen text-white font-sans">
      
      {/* --- HEADER --- */}
      <div className="flex flex-col md:flex-row justify-between items-center mb-8 border-b border-gray-700 pb-4">
        <div>
          <h1 className="text-3xl font-bold bg-clip-text text-transparent bg-gradient-to-r from-blue-400 to-purple-500">
            🧠 AI QUANT BOT v2.5
          </h1>
          <p className="text-xs text-gray-400 mt-1">Institutional Algorithmic Trading System</p>
        </div>
        <div className="flex items-center gap-4 mt-4 md:mt-0">
          <div className="text-right">
            <div className="text-xs text-gray-500">Última actualización</div>
            <div className="font-mono text-sm">{lastUpdated.toLocaleTimeString()}</div>
          </div>
          <span className={`px-4 py-2 rounded-lg text-sm font-bold shadow-lg ${status.is_running ? 'bg-green-900/50 text-green-400 border border-green-500' : 'bg-red-900/50 text-red-400 border border-red-500'}`}>
            {status.is_running ? '● SYSTEM ONLINE' : '● SYSTEM OFFLINE'}
          </span>
        </div>
      </div>

      {/* --- GRID SUPERIOR (KPIs + IA) --- */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6 mb-8">
        
        {/* KPI 1: Mercado */}
        <div className="bg-gray-800 p-5 rounded-xl border border-gray-700 shadow-lg relative overflow-hidden group">
            <div className="absolute top-0 right-0 p-2 opacity-10 group-hover:opacity-20 transition-opacity">
                <svg className="w-16 h-16 text-white" fill="currentColor" viewBox="0 0 20 20"><path d="M2 11a1 1 0 011-1h2a1 1 0 011 1v5a1 1 0 01-1 1H3a1 1 0 01-1-1v-5zM8 7a1 1 0 011-1h2a1 1 0 011 1v9a1 1 0 01-1 1H9a1 1 0 01-1-1V7zM14 4a1 1 0 011-1h2a1 1 0 011 1v12a1 1 0 01-1 1h-2a1 1 0 01-1-1V4z" /></svg>
            </div>
            <h2 className="text-gray-400 text-xs uppercase tracking-wider font-semibold">Mercado Activo</h2>
            <div className="text-2xl font-bold mt-1 text-white">{status.active_symbol}</div>
            <div className="text-xl mt-1 text-yellow-400 font-mono tracking-tight">${status.current_price.toFixed(2)}</div>
        </div>

        {/* KPI 2: Balance */}
        <div className="bg-gray-800 p-5 rounded-xl border border-gray-700 shadow-lg relative overflow-hidden">
            <h2 className="text-gray-400 text-xs uppercase tracking-wider font-semibold">Capital Total (USDT)</h2>
            <div className="text-3xl font-bold mt-2 text-white">${balance.total_balance.toFixed(2)}</div>
            <div className="flex items-center mt-2 text-xs text-gray-400">
                <span className="w-2 h-2 rounded-full bg-blue-500 mr-2"></span>
                Disponible: ${balance.available_balance.toFixed(2)}
            </div>
        </div>

        {/* KPI 3: CEREBRO IA (Visualización de Confianza) */}
        <div className="bg-gray-800 p-5 rounded-xl border border-gray-700 shadow-lg col-span-1 md:col-span-2 flex flex-col justify-between">
            <div className="flex justify-between items-start">
                <div>
                    <h2 className="text-gray-400 text-xs uppercase tracking-wider font-semibold">Predicción IA (Random Forest)</h2>
                    <div className="flex items-center gap-2 mt-2">
                        <span className={`text-2xl font-bold ${status.ai_prediction === 'LONG' ? 'text-green-400' : status.ai_prediction === 'SHORT' ? 'text-red-400' : 'text-gray-400'}`}>
                            {status.ai_prediction}
                        </span>
                        {status.ai_prediction !== 'ESPERANDO' && (
                            <span className="text-sm bg-gray-700 px-2 py-1 rounded text-white">
                                {status.ai_confidence.toFixed(2)}% Confianza
                            </span>
                        )}
                    </div>
                </div>
                <div className="text-right">
                    <div className="text-xs text-gray-500">Umbral para operar</div>
                    <div className="text-sm font-bold text-blue-300">{status.ai_threshold}%</div>
                </div>
            </div>
            
            {/* Barra de Progreso de Confianza */}
            <div className="mt-4">
                <div className="flex justify-between text-xs text-gray-500 mb-1">
                    <span>0%</span>
                    <span>Umbral {status.ai_threshold}%</span>
                    <span>100%</span>
                </div>
                <div className="w-full bg-gray-700 rounded-full h-2.5 relative">
                    <div 
                        className={`h-2.5 rounded-full transition-all duration-500 ${confidenceColor}`} 
                        style={{ width: `${status.ai_confidence}%` }}
                    ></div>
                    {/* Marcador del Umbral */}
                    <div 
                        className="absolute top-0 w-0.5 h-4 bg-white -mt-0.5 shadow-[0_0_10px_rgba(255,255,255,0.8)]"
                        style={{ left: `${status.ai_threshold}%` }}
                    ></div>
                </div>
            </div>
        </div>
      </div>

      {/* --- SECCIÓN CENTRAL: GESTIÓN DE ORDEN --- */}
      <div className="mb-8">
        {status.is_in_position && status.open_trade ? (
            // TARJETA DE ORDEN ABIERTA (Diseño Expandido)
            <div className={`rounded-xl border shadow-2xl overflow-hidden ${status.position_type === 'LONG' ? 'bg-gradient-to-br from-gray-800 to-green-900/20 border-green-500/50' : 'bg-gradient-to-br from-gray-800 to-red-900/20 border-red-500/50'}`}>
                {/* Header de la Orden */}
                <div className="bg-black/20 px-6 py-4 flex justify-between items-center border-b border-white/10">
                    <div className="flex items-center gap-4">
                        <span className={`text-xl font-bold px-3 py-1 rounded ${status.position_type === 'LONG' ? 'bg-green-600 text-white' : 'bg-red-600 text-white'}`}>
                            {status.position_type}
                        </span>
                        <span className="text-gray-300 font-mono">Entrada: ${status.open_trade.entry_price.toFixed(2)}</span>
                    </div>
                    <div className="flex items-center gap-4">
                        {status.open_trade.is_trailing && (
                            <div className="flex items-center gap-2 px-3 py-1 bg-purple-600/30 border border-purple-500 rounded text-purple-200 text-xs font-bold animate-pulse">
                                🚀 TRAILING STOP ACTIVO
                            </div>
                        )}
                        <div className="text-right">
                            <div className="text-xs text-gray-400">PNL No Realizado</div>
                            <div className={`text-2xl font-mono font-bold ${isProfitable ? 'text-green-400' : 'text-red-400'}`}>
                                {isProfitable ? '+' : ''}{status.open_trade.pnl.toFixed(2)} USDT
                                <span className="text-sm ml-2 opacity-80">({status.open_trade.roe.toFixed(2)}%)</span>
                            </div>
                        </div>
                    </div>
                </div>

                {/* Grid de Datos de la Orden */}
                <div className="grid grid-cols-2 md:grid-cols-4 gap-4 p-6">
                    <div className="bg-black/20 p-3 rounded border border-white/5">
                        <div className="text-xs text-gray-500 uppercase">Stop Loss (Dinámico)</div>
                        <div className="text-lg font-mono text-red-300 font-bold">${status.open_trade.stop_loss.toFixed(2)}</div>
                        <div className="text-xs text-red-500/50 mt-1">Protección Hard-Order</div>
                    </div>
                    <div className="bg-black/20 p-3 rounded border border-white/5">
                        <div className="text-xs text-gray-500 uppercase">Take Profit</div>
                        <div className={`text-lg font-mono font-bold ${status.open_trade.is_trailing ? 'text-gray-500 line-through' : 'text-green-300'}`}>
                            {status.open_trade.take_profit > 0 ? `$${status.open_trade.take_profit.toFixed(2)}` : 'INFINITO 🚀'}
                        </div>
                        <div className="text-xs text-gray-500 mt-1">{status.open_trade.is_trailing ? 'Cancelado por Trailing' : 'Objetivo Fijo'}</div>
                    </div>
                    <div className="bg-black/20 p-3 rounded border border-white/5">
                        <div className="text-xs text-gray-500 uppercase">Volatilidad (ATR)</div>
                        <div className="text-lg font-mono text-blue-300">${status.open_trade.atr.toFixed(2)}</div>
                        <div className="text-xs text-blue-500/50 mt-1">Riesgo calculado</div>
                    </div>
                    <div className="bg-black/20 p-3 rounded border border-white/5">
                        <div className="text-xs text-gray-500 uppercase">Precio Actual</div>
                        <div className="text-lg font-mono text-white">${status.current_price.toFixed(2)}</div>
                        <div className="text-xs text-gray-500 mt-1">Actualizado en tiempo real</div>
                    </div>
                </div>
            </div>
        ) : (
            // ESTADO DE ESCANEO (Cuando no hay orden)
            <div className="bg-gray-800 rounded-xl border border-gray-700 p-8 text-center shadow-inner">
                <div className="inline-block p-4 rounded-full bg-blue-900/20 mb-4 relative">
                    <div className="absolute inset-0 animate-ping rounded-full bg-blue-500/20"></div>
                    <svg className="w-8 h-8 text-blue-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"></path></svg>
                </div>
                <h3 className="text-xl font-bold text-white">Escaneando Oportunidades...</h3>
                <p className="text-gray-400 mt-2 max-w-md mx-auto">
                    La Inteligencia Artificial está analizando la volatilidad y el momentum. 
                    Se abrirá una posición automáticamente cuando la confianza supere el <strong>{status.ai_threshold}%</strong>.
                </p>
            </div>
        )}
      </div>

      {/* --- TABLA DE HISTORIAL --- */}
      <div className="bg-gray-800 rounded-xl border border-gray-700 overflow-hidden shadow-lg">
        <div className="px-6 py-4 border-b border-gray-700 bg-gray-800/50 flex justify-between items-center">
            <h3 className="text-lg font-semibold text-white flex items-center gap-2">
                📜 Historial de Operaciones
            </h3>
            <span className="text-xs text-gray-500 bg-gray-900 px-2 py-1 rounded">Últimos 10 trades</span>
        </div>
        <div className="overflow-x-auto">
            <table className="w-full text-left text-gray-400">
                <thead className="bg-gray-900 uppercase text-xs font-bold text-gray-500">
                    <tr>
                        <th className="px-6 py-4">Fecha</th>
                        <th className="px-6 py-4">Tipo</th>
                        <th className="px-6 py-4 text-right">Precio Ent.</th>
                        <th className="px-6 py-4 text-right">Precio Sal.</th>
                        <th className="px-6 py-4 text-right">PNL (USDT)</th>
                        <th className="px-6 py-4 text-center">ROE %</th>
                        <th className="px-6 py-4 text-center">Estado</th>
                    </tr>
                </thead>
                <tbody className="divide-y divide-gray-700">
                    {trades.length === 0 ? (
                        <tr><td colSpan="7" className="text-center py-10 text-gray-500 italic">No hay trades cerrados registrados aún.</td></tr>
                    ) : (
                        trades.map((trade) => (
                            <tr key={trade.id} className="hover:bg-gray-700/30 transition-colors">
                                <td className="px-6 py-4 text-sm">{new Date(trade.entry_time).toLocaleString()}</td>
                                <td className="px-6 py-4">
                                    <span className={`px-2 py-1 rounded text-xs font-bold ${trade.side === 'LONG' ? 'bg-green-900/30 text-green-400 border border-green-500/30' : 'bg-red-900/30 text-red-400 border border-red-500/30'}`}>
                                        {trade.side}
                                    </span>
                                </td>
                                <td className="px-6 py-4 text-right font-mono text-sm">${trade.entry_price.toFixed(2)}</td>
                                <td className="px-6 py-4 text-right font-mono text-sm">{trade.exit_price ? `$${trade.exit_price.toFixed(2)}` : '-'}</td>
                                <td className={`px-6 py-4 text-right font-mono font-bold ${trade.realized_pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                                    {trade.realized_pnl ? `$${trade.realized_pnl.toFixed(2)}` : '...'}
                                </td>
                                <td className="px-6 py-4 text-center font-mono text-sm">
                                    {trade.roe ? (
                                        <span className={trade.roe >= 0 ? 'text-green-400' : 'text-red-400'}>{trade.roe}%</span>
                                    ) : '-'}
                                </td>
                                <td className="px-6 py-4 text-center">
                                    <span className="text-xs text-gray-500">{trade.status}</span>
                                </td>
                            </tr>
                        ))
                    )}
                </tbody>
            </table>
        </div>
      </div>
    </div>
  );
};

export default Dashboard;