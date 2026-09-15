import requests
import concurrent.futures
import pandas as pd
from datetime import datetime, date
import time
from typing import Dict, List

from config import BacktestConfig
from backtester import _prepare_symbol_data, _generate_raw_signals
from database import (
    save_signals, get_portfolio, update_portfolio, save_paper_trade, 
    get_open_trades, update_paper_trade
)

def fetch_groww_price(symbol: str) -> dict:
    """Fetch live price for a single symbol from Groww."""
    url = f"https://groww.in/v1/api/stocks_data/v1/tr_live_prices/exchange/NSE/segment/CASH/{symbol}/latest"
    try:
        # Groww might reject without user-agent
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        resp = requests.get(url, headers=headers, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            if 'ltp' in data:
                return {
                    'symbol': symbol,
                    'open': float(data.get('open', data['ltp'])),
                    'high': float(data.get('high', data['ltp'])),
                    'low': float(data.get('low', data['ltp'])),
                    'close': float(data['ltp']),
                    'volume': float(data.get('volume', 0)),
                    'success': True
                }
    except Exception as e:
        pass
    
    return {'symbol': symbol, 'success': False}

def process_symbol_logic(symbol: str, hist_df: pd.DataFrame, live_data: dict, config: BacktestConfig) -> List[dict]:
    """Appends live data to historical df and computes signals."""
    if not live_data['success'] or hist_df.empty:
        return []
        
    today = date.today()
    
    # Check if we already have today's date in hist_df
    # If so, we can replace it, otherwise append.
    if not hist_df.empty and hist_df.iloc[-1]['date'] == today:
        df = hist_df.copy()
        df.loc[df.index[-1], ['open', 'high', 'low', 'close', 'volume']] = [
            live_data['open'], live_data['high'], live_data['low'], live_data['close'], live_data['volume']
        ]
    else:
        new_row = pd.DataFrame([{
            'date': today,
            'open': live_data['open'],
            'high': live_data['high'],
            'low': live_data['low'],
            'close': live_data['close'],
            'volume': live_data['volume']
        }])
        df = pd.concat([hist_df, new_row], ignore_index=True)
    
    # Compute indicators using existing logic
    df = _prepare_symbol_data(df, config)
    
    # Generate signals
    # For live, we want to know if there's a signal TODAY.
    # We pass start_dt and end_dt as today.
    signals = _generate_raw_signals(symbol, df, config, today, today)
    return signals

def run_live_scan(all_data: Dict[str, pd.DataFrame], config: BacktestConfig):
    """
    1. Fetch live prices concurrently for all symbols in all_data.
    2. Compute signals concurrently.
    3. Save results to database.
    """
    print(f"Starting live scan for {len(all_data)} symbols...")
    start_time = time.time()
    
    live_prices = {}
    
    # Fetch live prices with max concurrency
    with concurrent.futures.ThreadPoolExecutor(max_workers=100) as executor:
        future_to_symbol = {executor.submit(fetch_groww_price, symbol): symbol for symbol in all_data.keys()}
        
        for future in concurrent.futures.as_completed(future_to_symbol):
            res = future.result()
            if res['success']:
                live_prices[res['symbol']] = res

    fetch_time = time.time()
    print(f"Fetched {len(live_prices)} live prices in {fetch_time - start_time:.2f} seconds.")
    
    all_signals = []
    
    # Process calculations using ProcessPoolExecutor for CPU-bound tasks
    # But since it's just pandas over ~2000 symbols x ~1500 rows, ThreadPoolExecutor or even serial might be fast enough.
    # Let's use ThreadPoolExecutor to prevent blocking and utilize multiple cores if pandas releases GIL on some ops.
    # Actually, ProcessPoolExecutor is better for pandas, but serial is simpler. Let's try ThreadPoolExecutor.
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:
        futures = []
        for symbol, hist_df in all_data.items():
            if symbol in live_prices:
                futures.append(
                    executor.submit(process_symbol_logic, symbol, hist_df, live_prices[symbol], config)
                )
        
        for future in concurrent.futures.as_completed(futures):
            try:
                sig_list = future.result()
                all_signals.extend(sig_list)
            except Exception as e:
                print(f"Error processing symbol: {e}")
                
    calc_time = time.time()
    print(f"Computed signals in {calc_time - fetch_time:.2f} seconds. Found {len(all_signals)} signals.")
    
    # Deduplicate multiple ST triggers for the same symbol/date
    seen = set()
    deduped_signals = []
    
    # Sort signals so we get deterministic deduplication
    all_signals.sort(key=lambda s: s["symbol"])
    
    for sig in all_signals:
        key = (sig["symbol"], sig["signal_date"])
        if key not in seen:
            seen.add(key)
            deduped_signals.append(sig)
            
    print(f"After deduplication: {len(deduped_signals)} signals.")
    
    # Save to database
    save_signals(deduped_signals)
    print(f"Live scan completed in {time.time() - start_time:.2f} seconds.")
    
    # Paper Trading Execution (Buy)
    portfolio = get_portfolio()
    available_capital = portfolio['available_capital']
    total_equity = portfolio['total_equity']
    
    for sig in deduped_signals:
        if available_capital <= 0:
            print("No available capital left for paper trading.")
            break
            
        # Invest 20% of TOTAL equity, capped by available capital
        allocation = total_equity * 0.20
        if allocation > available_capital:
            allocation = available_capital
            
        entry_price = sig['entry_price']
        
        # Calculate quantity
        quantity = int(allocation / entry_price)
        if quantity <= 0:
            continue
            
        invested_amount = quantity * entry_price
        
        # We only deduct the actual invested amount
        if available_capital >= invested_amount:
            available_capital -= invested_amount
            save_paper_trade(
                symbol=sig['symbol'], 
                entry_date=str(sig['signal_date']), 
                entry_price=entry_price, 
                quantity=quantity, 
                invested_amount=invested_amount
            )
            print(f"Paper Trade BUY: {sig['symbol']} at ₹{entry_price} x {quantity} = ₹{invested_amount}")
            
    # Update portfolio with new available capital (total equity remains unchanged on buy, ignoring fees for now)
    update_portfolio(available_capital, total_equity)
    
    return deduped_signals

def run_live_sell_scan():
    """
    1. Fetch all OPEN paper trades.
    2. Fetch latest Groww price (open or ltp).
    3. Calculate PNL, mark as CLOSED.
    4. Restore capital to portfolio.
    """
    print("Starting live sell scan (9:15 AM)...")
    open_trades = get_open_trades()
    if not open_trades:
        print("No open trades to sell.")
        return []
        
    portfolio = get_portfolio()
    available_capital = portfolio['available_capital']
    total_equity = portfolio['total_equity']
    
    today = date.today().isoformat()
    closed_trades = []
    
    # Fetch live prices concurrently for the open trades
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        future_to_trade = {executor.submit(fetch_groww_price, trade['symbol']): trade for trade in open_trades}
        
        for future in concurrent.futures.as_completed(future_to_trade):
            trade = future_to_trade[future]
            try:
                res = future.result()
                if res['success']:
                    # At 9:15 AM, the market might just be opening, we use open price if valid, else ltp (close)
                    exit_price = res['open'] if res['open'] > 0 else res['close']
                    
                    pnl = (exit_price - trade['entry_price']) * trade['quantity']
                    
                    update_paper_trade(
                        trade_id=trade['id'], 
                        exit_date=today, 
                        exit_price=exit_price, 
                        pnl=pnl
                    )
                    
                    available_capital += (trade['invested_amount'] + pnl)
                    total_equity += pnl
                    
                    closed_trades.append({
                        'symbol': trade['symbol'],
                        'exit_price': exit_price,
                        'pnl': pnl
                    })
                    print(f"Paper Trade SELL: {trade['symbol']} at ₹{exit_price}. PNL: ₹{pnl}")
            except Exception as e:
                print(f"Error selling {trade['symbol']}: {e}")
                
    update_portfolio(available_capital, total_equity)
    print(f"Sell scan completed. Closed {len(closed_trades)} trades. New Equity: ₹{total_equity}")
    return closed_trades

if __name__ == "__main__":
    from data_loader import load_all_daily_data
    import os
    DATA_DIR = r"C:\Users\Yug\Desktop\datas"
    
    all_data = load_all_daily_data(DATA_DIR)
    # Just take 50 for a quick test
    test_data = {k: all_data[k] for k in list(all_data.keys())[:50]}
    
    config = BacktestConfig()
    run_live_scan(test_data, config)
