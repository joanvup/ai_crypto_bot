import { useState, useEffect } from 'react';
import { getBotStatus, getBalance, getTrades } from '../services/api';

const Dashboard = () => {
  const [status, setStatus] = useState(null);
  const [balance, setBalance] = useState(null);
  const [trades, setTrades] = useState([]);
  const[lastUpdated, setLastUpdated] = useState(new Date());

  const fetchData = async () => {
    try {
      const statusData = await getBotStatus();
      const balanceData = await getBalance();
      const tradesData = await getTrades();

      if (statusData) setStatus(statusData);
      if (balanceData) setBalance(balanceData);
      if (tradesData) setTrades(tradesData);
      
      setLastUpdated(new Date());
    } catch (error) {
      console.error("Error obteniendo datos del servidor", error);
    }
  };

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 3000); // Refresco cada 3 segundos
    return () => clearInterval(interval);
  },[]);

  if (!status || !status.assets || !balance) {
    return (
      <div className="flex flex-col items-center justify-center min-h-screen bg-gray-900 text-white">
        <div className="text-4xl animate-pulse mb-4">🤖</div>
        <div className="text-xl text-gray-400 font-mono">Cargando Terminal Multi-Activo...</div>
        <div className="text-sm text-gray-600 mt-2">Sincronizando IA con Binance Futuros</div>
      </div>
    );
  }

  // Separar los activos en dos grupos: Con posición abierta y Solo monitoreo
  const activeAssets = status.assets.filter(a => a.is_in_position);
  const scanningAssets = status.assets.filter(a => !a.is_in_position);

  return (
    <div className="p-6 max-w-[1400px] mx-auto min-h-screen">
      
      {/* HEADER & GLOBAL STATUS */}
      <div className="flex flex-col md:flex-row justify-between items-center mb-8 bg-gray-800 p-6 rounded-xl border border-gray-700 shadow-xl">
        <div>
          <h1 className="text-3xl font-bold text-blue-400 flex items-center gap-3">
            🤖 AI Quant Terminal <span className="text-xs bg-blue-900/50 text-blue-300 px-2 py-1 rounded border border-blue-700">v3.0 Multi</span>
          </h1>
          <div className="text-sm text-gray-400 mt-2 flex items-center gap-4">
            <span>Actualizado: {lastUpdated.toLocaleTimeString()}</span>
            <span className={`px-3 py-1 rounded-full text-xs font-bold ${status.is_running ? 'bg-green-900 text-green-300 border border-green-700' : 'bg-red-900 text-red-300'}`}>
              {status.is_running ? '● SYSTEM ONLINE' : '○ OFFLINE'}
            </span>
          </div>
        </div>

        <div className="flex gap-6 mt-4 md:mt-0">
          <div className="bg-gray-900 p-4 rounded-lg border border-gray-700">
            <div className="text-gray-400 text-xs uppercase">Balance USDT</div>
            <div className="text-2xl font-bold text-white">${balance.total_balance.toFixed(2)}</div>
          </div>
          <div className="bg-gray-900 p-4 rounded-lg border border-gray-700">
            <div className="text-gray-400 text-xs uppercase">Operaciones Globales</div>
            <div className="text-2xl font-bold text-white flex items-center gap-2">
              <span className={status.global_open_trades >= status.max_open_trades ? 'text-orange-400' : 'text-green-400'}>
                {status.global_open_trades}
              </span> 
              <span className="text-gray-500">/ {status.max_open_trades}</span>
            </div>
          </div>
        </div>
      </div>

      {/* ZONA DE COMBATE: POSICIONES ACTIVAS */}
      <div className="mb-10">
        <h2 className="text-xl font-bold text-white mb-4 flex items-center gap-2">
          ⚔️ Posiciones Activas ({activeAssets.length})
        </h2>
        {activeAssets.length === 0 ? (
          <div className="bg-gray-800/50 border border-gray-700 border-dashed rounded-xl p-8 text-center text-gray-500 font-mono">
            Esperando señales de alta probabilidad de la IA...
          </div>
        ) : (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {activeAssets.map((asset) => {
              const trade = asset.open_trade;
              const isLong = asset.position_type === 'LONG';
              const pnlColor = trade.pnl >= 0 ? 'text-green-400' : 'text-red-400';
              
              return (
                <div key={asset.symbol} className={`bg-gray-800 rounded-xl border-2 shadow-lg overflow-hidden ${isLong ? 'border-green-900/50' : 'border-red-900/50'}`}>
                  {/* Card Header */}
                  <div className={`px-6 py-4 flex justify-between items-center ${isLong ? 'bg-green-900/20' : 'bg-red-900/20'}`}>
                    <div className="flex items-center gap-3">
                      <span className={`px-3 py-1 rounded font-bold text-sm ${isLong ? 'bg-green-600 text-white' : 'bg-red-600 text-white'}`}>
                        {asset.position_type}
                      </span>
                      <span className="text-xl font-bold text-white">{asset.symbol}</span>
                    </div>
                    <div className={`text-2xl font-mono font-bold ${pnlColor}`}>
                      {trade.pnl >= 0 ? '+' : ''}{trade.pnl.toFixed(2)} USDT <span className="text-sm opacity-80">({trade.roe.toFixed(2)}%)</span>
                    </div>
                  </div>
                  
                  {/* Card Body */}
                  <div className="p-6 grid grid-cols-2 md:grid-cols-4 gap-4">
                    <div>
                      <div className="text-xs text-gray-500 uppercase">Precio Actual</div>
                      <div className="text-lg font-mono text-white">${asset.current_price}</div>
                    </div>
                    <div>
                      <div className="text-xs text-gray-500 uppercase">Precio Entrada</div>
                      <div className="text-lg font-mono text-gray-300">${trade.entry_price}</div>
                    </div>
                    <div>
                      <div className="text-xs text-gray-500 uppercase flex items-center gap-1">
                        Stop Loss 
                        {trade.is_trailing && <span title="Trailing Stop Activo" className="text-xs animate-bounce">🚀</span>}
                      </div>
                      <div className={`text-lg font-mono ${trade.is_trailing ? 'text-orange-400 font-bold' : 'text-red-400'}`}>
                        ${trade.stop_loss}
                      </div>
                    </div>
                    <div>
                      <div className="text-xs text-gray-500 uppercase">Take Profit</div>
                      <div className="text-lg font-mono text-green-400">
                        {trade.take_profit > 0 ? `$${trade.take_profit}` : 'Corriendo 📈'}
                      </div>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* RADAR DE INTELIGENCIA ARTIFICIAL */}
      <div className="mb-10">
        <h2 className="text-xl font-bold text-white mb-4 flex items-center gap-2">
          📡 Radar IA - Monitoreo en Tiempo Real (Umbral: {status.ai_threshold}%)
        </h2>
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-4">
          {scanningAssets.map((asset) => {
            const pred = asset.ai_prediction;
            const conf = asset.ai_confidence;
            // Estilos dinámicos según la predicción
            let bgColor = "bg-gray-800 border-gray-700";
            let textColor = "text-gray-400";
            if (pred === 'LONG') {
              textColor = "text-green-400";
              if (conf >= status.ai_threshold) bgColor = "bg-green-900/20 border-green-700 shadow-[0_0_15px_rgba(34,197,94,0.2)]";
            } else if (pred === 'SHORT') {
              textColor = "text-red-400";
              if (conf >= status.ai_threshold) bgColor = "bg-red-900/20 border-red-700 shadow-[0_0_15px_rgba(239,68,68,0.2)]";
            }

            return (
              <div key={asset.symbol} className={`p-4 rounded-xl border ${bgColor} transition-all duration-300`}>
                <div className="text-lg font-bold text-white mb-1">{asset.symbol}</div>
                <div className="text-sm font-mono text-gray-300 mb-3">${asset.current_price}</div>
                
                <div className="flex justify-between items-end">
                  <div className={`text-sm font-bold ${textColor}`}>
                    {pred}
                  </div>
                  <div className={`text-xs font-mono px-2 py-1 rounded bg-gray-900 ${conf >= status.ai_threshold ? 'text-white border border-gray-600' : 'text-gray-500'}`}>
                    {conf.toFixed(1)}%
                  </div>
                </div>
                
                {/* Barra de progreso de confianza */}
                <div className="w-full bg-gray-900 rounded-full h-1.5 mt-2 overflow-hidden">
                  <div 
                    className={`h-1.5 rounded-full ${pred === 'LONG' ? 'bg-green-500' : pred === 'SHORT' ? 'bg-red-500' : 'bg-gray-600'}`}
                    style={{ width: `${conf}%` }}
                  ></div>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* HISTORIAL DE TRADES */}
      <div className="bg-gray-800 rounded-xl border border-gray-700 overflow-hidden">
        <div className="px-6 py-4 border-b border-gray-700 bg-gray-900/50">
          <h3 className="text-lg font-bold text-white">📜 Historial de Operaciones</h3>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-gray-400">
            <thead className="bg-gray-900/80 uppercase text-xs font-semibold text-gray-500">
              <tr>
                <th className="px-6 py-4">Fecha/Hora</th>
                <th className="px-6 py-4">Símbolo</th>
                <th className="px-6 py-4">Tipo</th>
                <th className="px-6 py-4">Entrada</th>
                <th className="px-6 py-4">Salida</th>
                <th className="px-6 py-4 text-right">PNL (USDT)</th>
                <th className="px-6 py-4 text-right">ROE %</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800">
              {trades.length === 0 ? (
                <tr><td colSpan="7" className="text-center py-8 text-gray-500">No hay operaciones registradas aún.</td></tr>
              ) : (
                trades.map((trade) => (
                  <tr key={trade.id} className="hover:bg-gray-700/30 transition-colors">
                    <td className="px-6 py-4 text-sm">{new Date(trade.entry_time).toLocaleString()}</td>
                    <td className="px-6 py-4 font-bold text-white">{trade.symbol}</td>
                    <td className={`px-6 py-4 font-bold text-sm ${trade.side === 'LONG' ? 'text-green-400' : 'text-red-400'}`}>
                      {trade.side}
                    </td>
                    <td className="px-6 py-4 font-mono text-sm">${trade.entry_price}</td>
                    <td className="px-6 py-4 font-mono text-sm">{trade.exit_price ? `$${trade.exit_price}` : '-'}</td>
                    <td className={`px-6 py-4 font-mono text-right font-bold ${trade.realized_pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                      {trade.realized_pnl ? `${trade.realized_pnl >= 0 ? '+' : ''}${trade.realized_pnl.toFixed(2)}` : '...'}
                    </td>
                    <td className={`px-6 py-4 font-mono text-right ${trade.roe_percent >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                      {trade.roe_percent ? `${trade.roe_percent >= 0 ? '+' : ''}${trade.roe_percent.toFixed(2)}%` : '-'}
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