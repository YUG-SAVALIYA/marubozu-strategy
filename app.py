import os
import io
import base64
import time
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

# Import the existing backtester modules
from config import BacktestConfig
from data_loader import load_all_daily_data, select_universe, get_index_symbols
from backtester import run_backtest
from metrics import compute_all_metrics, format_summary_report
from run_backtest import trades_to_dataframe

app = Flask(__name__, static_folder='static')
CORS(app)

# In-memory storage for the raw data to avoid reloading from disk on every run
print("Initializing app and loading data into memory (this may take 20-30 seconds)...")
DATA_DIR = r"D:\backtesting\backtesting\data"
ALL_DATA = load_all_daily_data(DATA_DIR)
print(f"Data loading complete! Loaded {len(ALL_DATA)} symbols.")

def create_base64_plot(daily_equity: pd.DataFrame) -> str:
    """Generate the equity curve plot and return it as a base64 string."""
    if daily_equity.empty:
        return ""

    fig, ax = plt.subplots(figsize=(10, 5))
    dates = pd.to_datetime(daily_equity["date"])
    equity = daily_equity["equity"].values

    ax.plot(dates, equity, color="#00f2fe", linewidth=1.5, label="Equity")
    ax.fill_between(dates, equity, alpha=0.2, color="#00f2fe")

    # Format - use a dark theme for the plot to match the UI
    fig.patch.set_facecolor('#1a1a2e')
    ax.set_facecolor('#1a1a2e')
    ax.tick_params(colors='white')
    ax.xaxis.label.set_color('white')
    ax.yaxis.label.set_color('white')
    ax.title.set_color('white')
    for spine in ax.spines.values():
        spine.set_edgecolor('#ffffff33')

    ax.set_title("Daily_ST_M — Equity Curve", fontsize=14, fontweight="bold")
    ax.set_xlabel("Date", fontsize=11)
    ax.set_ylabel("Equity (₹)", fontsize=11)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=6))
    plt.xticks(rotation=45, ha="right")
    ax.grid(True, alpha=0.1, color='white')
    ax.legend(loc="upper left", facecolor='#1a1a2e', labelcolor='white')

    plt.tight_layout()
    
    # Save to BytesIO
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=120, bbox_inches='tight', transparent=False)
    plt.close(fig)
    buf.seek(0)
    
    # Encode to base64
    img_b64 = base64.b64encode(buf.getvalue()).decode('utf-8')
    return img_b64

@app.route('/')
def serve_index():
    return send_from_directory(app.static_folder, 'index.html')

@app.route('/<path:path>')
def serve_static(path):
    return send_from_directory(app.static_folder, path)

@app.route('/api/run_backtest', methods=['POST'])
def api_run_backtest():
    try:
        data = request.json
        
        # Parse parameters
        config = BacktestConfig(
            data_dir=DATA_DIR,
            start_date=data.get('start_date', '2021-01-01'),
            end_date=data.get('end_date', '2025-12-31'),
            universe_size=0, # Will be set below
            initial_capital=float(data.get('initial_capital', 100000)),
            allocation_per_trade=float(data.get('allocation_per_trade', 20000)),
            leverage=float(data.get('leverage', 1.0)),
            fee_pct=float(data.get('fee_pct', 0.0)),
            slippage_pct=float(data.get('slippage_pct', 0.0)),
            gap_target_pct=float(data.get('gap_target_pct', 1.0)),
        )

        t0 = time.time()
        
        universe_type = data.get('universe_type', 'top_484')
        
        # Select universe based on parameters using the in-memory data
        if universe_type == 'top_484':
            universe = select_universe(ALL_DATA, 484, config.start_date, config.end_date)
        elif universe_type == 'all':
            universe = ALL_DATA
        elif universe_type in ['nifty50', 'nifty100', 'nifty200', 'nifty500']:
            idx_symbols = get_index_symbols(universe_type)
            universe = {sym: df for sym, df in ALL_DATA.items() if sym in idx_symbols}
        else:
            universe = select_universe(ALL_DATA, 484, config.start_date, config.end_date)
            
        config.universe_size = len(universe)
        
        # Run backtest
        result = run_backtest(universe, config)
        
        # Compute metrics
        metrics = compute_all_metrics(result.trades, result.daily_equity, config.initial_capital)
        
        # Format summary
        report = format_summary_report(metrics, config)
        
        # Create equity curve image
        img_b64 = create_base64_plot(result.daily_equity)
        
        # Serialize trades log
        trades_list = []
        for t in result.trades:
            trades_list.append({
                'symbol': t.symbol,
                'signal_date': str(t.signal_date),
                'entry_price': round(t.entry_price, 2),
                'exit_date': str(t.exit_date),
                'exit_price': round(t.exit_price, 2),
                'gap_pct': round(t.gap_pct, 4),
                'shares': t.shares,
                'pnl': round(t.pnl, 2),
                'allocated_capital': round(t.allocated_capital, 2),
                'fees': round(t.fees, 2)
            })

        # Return response
        response = {
            'status': 'success',
            'execution_time_seconds': round(time.time() - t0, 2),
            'summary': report,
            'equity_curve_b64': img_b64,
            'total_trades': metrics.total_trades,
            'positive_trades': metrics.positive_trades,
            'negative_trades': metrics.negative_trades,
            'win_rate': round(metrics.positive_rate, 2),
            'target_hits': metrics.target_hit_count,
            'return_pct': round(metrics.return_pct, 2),
            'max_drawdown': round(metrics.max_drawdown_pct, 2),
            'gross_profit': round(metrics.gross_profit, 2),
            'gross_loss': round(metrics.gross_loss, 2),
            'profit_factor': round(metrics.profit_factor, 2) if metrics.profit_factor != float('inf') else 'Infinity',
            'avg_return_per_trade': round(metrics.avg_return_per_trade, 2),
            'ending_equity': round(metrics.ending_equity, 2),
            'best_gap_pct': round(metrics.best_gap_pct, 2),
            'worst_gap_pct': round(metrics.worst_gap_pct, 2),
            'best_day_pnl': round(metrics.best_day_pnl, 2),
            'worst_day_pnl': round(metrics.worst_day_pnl, 2),
            'trades_log': trades_list
        }
        
        return jsonify(response)

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'status': 'error', 'message': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=False, port=5000)
