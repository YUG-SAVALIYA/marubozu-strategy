import time
import threading
import datetime
from flask import Flask, jsonify, send_from_directory
from flask_cors import CORS
import pandas as pd

from config import BacktestConfig
from data_loader import load_all_daily_data
from live_engine import run_live_scan, run_live_sell_scan
from database import init_db, get_portfolio, get_open_trades, get_all_paper_trades, get_latest_signals

app = Flask(__name__, static_folder='static')
CORS(app)

# Initialize database
init_db()

print("Initializing live engine and loading data into memory...")
DATA_DIR = r"D:\backtesting\backtesting\data"
ALL_DATA = load_all_daily_data(DATA_DIR)
print(f"Live engine data loading complete! Loaded {len(ALL_DATA)} symbols.")

def scheduler_loop():
    while True:
        now = datetime.datetime.now()
        # Run strictly on weekdays
        if now.weekday() < 5:
            # 9:15 AM Sell Scan
            if now.hour == 9 and now.minute == 15:
                print(f"[{now}] Triggering scheduled 9:15 AM SELL scan...")
                try:
                    run_live_sell_scan()
                except Exception as e:
                    import traceback
                    traceback.print_exc()
            
            # 3:20 PM Buy Scan
            if now.hour == 15 and now.minute == 20:
                print(f"[{now}] Triggering scheduled 3:20 PM BUY scan...")
                try:
                    run_live_scan(ALL_DATA, BacktestConfig())
                except Exception as e:
                    import traceback
                    traceback.print_exc()
        time.sleep(60)

# Start background scheduler thread
scheduler_thread = threading.Thread(target=scheduler_loop, daemon=True)
scheduler_thread.start()

@app.route('/')
def serve_index():
    return send_from_directory(app.static_folder, 'live_index.html')

@app.route('/<path:path>')
def serve_static(path):
    return send_from_directory(app.static_folder, path)

@app.route('/api/portfolio', methods=['GET'])
def api_portfolio():
    try:
        portfolio = get_portfolio()
        return jsonify({
            'status': 'success',
            'portfolio': portfolio
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/paper_trades', methods=['GET'])
def api_paper_trades():
    try:
        trades = get_all_paper_trades()
        return jsonify({
            'status': 'success',
            'count': len(trades),
            'trades': trades
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/live_signals', methods=['GET'])
def api_live_signals():
    try:
        signals = get_latest_signals()
        return jsonify({
            'status': 'success',
            'count': len(signals),
            'signals': signals
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/trigger_buy', methods=['POST'])
def api_trigger_buy():
    try:
        t0 = time.time()
        config = BacktestConfig()
        signals = run_live_scan(ALL_DATA, config)
        return jsonify({
            'status': 'success',
            'execution_time_seconds': round(time.time() - t0, 2),
            'signals_generated': len(signals)
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/trigger_sell', methods=['POST'])
def api_trigger_sell():
    try:
        t0 = time.time()
        closed = run_live_sell_scan()
        return jsonify({
            'status': 'success',
            'execution_time_seconds': round(time.time() - t0, 2),
            'closed_trades': len(closed)
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'status': 'error', 'message': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=False, port=5001)
