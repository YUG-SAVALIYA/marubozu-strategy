import sqlite3
from typing import List, Dict
import os
import json

DB_PATH = os.path.join(os.path.dirname(__file__), "live_signals.db")

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Existing live_signals table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS live_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            signal_date TEXT,
            symbol TEXT,
            entry_price REAL,
            st_triggered TEXT,
            filter_group TEXT,
            atr_pct REAL,
            avg20_turnover_cr REAL,
            upper_wick_pct REAL,
            signal_body_pct REAL,
            range_pct REAL,
            raw_data TEXT
        )
    ''')
    
    # Portfolio for paper trading
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS portfolio (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            available_capital REAL,
            total_equity REAL
        )
    ''')
    
    # Insert default portfolio if it doesn't exist
    cursor.execute('SELECT COUNT(*) FROM portfolio')
    if cursor.fetchone()[0] == 0:
        cursor.execute('INSERT INTO portfolio (id, available_capital, total_equity) VALUES (1, 100000.0, 100000.0)')
        
    # Paper trades table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS paper_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT,
            entry_date TEXT,
            entry_price REAL,
            quantity INTEGER,
            invested_amount REAL,
            exit_date TEXT,
            exit_price REAL,
            pnl REAL,
            status TEXT
        )
    ''')
    
    # Market Data Table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS market_data (
            symbol TEXT,
            date TEXT,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume INTEGER
        )
    ''')
    
    # Create composite index for faster grouping/selection by symbol
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_market_data_symbol_date ON market_data(symbol, date)
    ''')
    
    conn.commit()
    conn.close()

def get_portfolio() -> Dict:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM portfolio WHERE id = 1')
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else {'available_capital': 0.0, 'total_equity': 0.0}

def update_portfolio(available_capital: float, total_equity: float):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('UPDATE portfolio SET available_capital = ?, total_equity = ? WHERE id = 1', 
                   (available_capital, total_equity))
    conn.commit()
    conn.close()

def save_paper_trade(symbol: str, entry_date: str, entry_price: float, quantity: int, invested_amount: float):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO paper_trades (symbol, entry_date, entry_price, quantity, invested_amount, status)
        VALUES (?, ?, ?, ?, ?, 'OPEN')
    ''', (symbol, entry_date, entry_price, quantity, invested_amount))
    conn.commit()
    conn.close()

def update_paper_trade(trade_id: int, exit_date: str, exit_price: float, pnl: float):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE paper_trades
        SET exit_date = ?, exit_price = ?, pnl = ?, status = 'CLOSED'
        WHERE id = ?
    ''', (exit_date, exit_price, pnl, trade_id))
    conn.commit()
    conn.close()

def get_open_trades() -> List[Dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM paper_trades WHERE status = "OPEN"')
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_all_paper_trades() -> List[Dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM paper_trades ORDER BY id DESC')
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def save_signals(signals: List[Dict]):
    if not signals:
        return
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    for sig in signals:
        st_triggered_str = json.dumps(sig.get('st_triggered', []))
        raw_data_str = json.dumps(sig)
        
        cursor.execute('''
            INSERT INTO live_signals (
                signal_date, symbol, entry_price, st_triggered, filter_group,
                atr_pct, avg20_turnover_cr, upper_wick_pct, signal_body_pct, range_pct, raw_data
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            str(sig['signal_date']),
            sig['symbol'],
            sig['entry_price'],
            st_triggered_str,
            sig['filter_group'],
            sig.get('atr_pct'),
            sig.get('avg20_turnover_cr'),
            sig.get('upper_wick_pct'),
            sig.get('signal_body_pct'),
            sig.get('range_pct'),
            raw_data_str
        ))
    conn.commit()
    conn.close()

def get_latest_signals() -> List[Dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute('SELECT MAX(signal_date) as max_date FROM live_signals')
    row = cursor.fetchone()
    if not row or not row['max_date']:
        conn.close()
        return []
        
    max_date = row['max_date']
    
    cursor.execute('''
        SELECT * FROM live_signals
        WHERE signal_date = ?
        ORDER BY symbol ASC
    ''', (max_date,))
    
    rows = cursor.fetchall()
    conn.close()
    
    results = []
    for r in rows:
        results.append({
            'id': r['id'],
            'timestamp': r['timestamp'],
            'signal_date': r['signal_date'],
            'symbol': r['symbol'],
            'entry_price': r['entry_price'],
            'st_triggered': json.loads(r['st_triggered']) if r['st_triggered'] else [],
            'filter_group': r['filter_group'],
            'atr_pct': r['atr_pct'],
            'avg20_turnover_cr': r['avg20_turnover_cr'],
            'upper_wick_pct': r['upper_wick_pct'],
            'signal_body_pct': r['signal_body_pct'],
            'range_pct': r['range_pct'],
            'raw_data': json.loads(r['raw_data']) if r['raw_data'] else {}
        })
    return results

if __name__ == '__main__':
    init_db()
    print("Database initialized.")
