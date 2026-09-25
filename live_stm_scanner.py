import streamlit as st
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone
import time
import requests
import concurrent.futures
import psycopg2
import psycopg2.extras
import pytz

st.set_page_config(layout="wide", page_title="Daily_ST_M Live Scanner", page_icon="⚡")

# Styling to WOW the user
st.markdown("""
<style>
    .stApp {
        background-color: #0d1117;
        color: #c9d1d9;
        font-family: 'Inter', sans-serif;
    }
    .metric-card {
        background: rgba(22, 27, 34, 0.8);
        border: 1px solid #30363d;
        border-radius: 12px;
        padding: 20px;
        text-align: center;
        box-shadow: 0 4px 6px rgba(0,0,0,0.3);
        backdrop-filter: blur(10px);
        transition: transform 0.2s ease, box-shadow 0.2s ease;
    }
    .metric-card:hover {
        transform: translateY(-2px);
        box-shadow: 0 6px 12px rgba(88, 166, 255, 0.15);
        border-color: #58a6ff;
    }
    .metric-title { font-size: 14px; color: #8b949e; text-transform: uppercase; font-weight: 600; margin-bottom: 8px; }
    .metric-value { font-size: 32px; font-weight: 800; color: #58a6ff; }
    
    .signal-card {
        background: linear-gradient(145deg, #1f242d, #161b22);
        border-left: 5px solid #3fb950;
        border-radius: 8px;
        padding: 15px 20px;
        margin-bottom: 15px;
        display: flex;
        justify-content: space-between;
        align-items: center;
        box-shadow: 0 4px 6px rgba(0,0,0,0.2);
    }
    .signal-sym { font-size: 24px; font-weight: 800; color: #ffffff; }
    .signal-price { font-size: 20px; color: #3fb950; font-weight: 600; }
    .signal-details { font-size: 13px; color: #8b949e; display: flex; gap: 20px; }
</style>
""", unsafe_allow_html=True)

IST = timezone(timedelta(hours=5, minutes=30))

def get_db_connection():
    return psycopg2.connect("dbname=marubozu user=postgres password=postgres host=localhost port=5432")

def _fetch_groww_daily(sym, start_dt, now_ms):
    start_ms = int(start_dt.timestamp() * 1000)
    url = f"https://groww.in/v1/api/charting_service/v2/chart/exchange/NSE/segment/CASH/{sym}?intervalInMinutes=1440&minimal=false&startTimeInMillis={start_ms}&endTimeInMillis={now_ms}"
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        resp = requests.get(url, headers=headers, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            if 'candles' in data and data['candles']:
                rows = []
                for c in data['candles']:
                    dt_str = datetime.fromtimestamp(c[0], IST).strftime("%Y-%m-%d")
                    rows.append((sym, dt_str, float(c[1]), float(c[2]), float(c[3]), float(c[4]), int(c[5])))
                return rows
    except Exception:
        pass
    return []

def check_and_update_missing_data(symbols):
    c1 = get_db_connection()
    cur1 = c1.cursor()
    cur1.execute("SELECT symbol, MAX(date) FROM market_data WHERE symbol = ANY(%s) GROUP BY symbol", (symbols,))
    last_dates = {row[0]: row[1] for row in cur1.fetchall()}
    c1.close()

    now = datetime.now(IST)
    now_ms = int(time.time() * 1000)
    to_fetch = []

    for sym in symbols:
        last_dt = last_dates.get(sym)
        if last_dt is None:
            start_dt = now - timedelta(days=15)
            to_fetch.append((sym, start_dt, now_ms))
        else:
            if isinstance(last_dt, str):
                last_dt = datetime.strptime(last_dt[:10], "%Y-%m-%d").date()
            last_dt_aware = datetime.combine(last_dt, datetime.min.time(), tzinfo=IST)
            if (now - last_dt_aware).days >= 1:
                start_dt = last_dt_aware - timedelta(days=1)
                to_fetch.append((sym, start_dt, now_ms))

    if not to_fetch:
        return

    progress_bar = st.progress(0)
    status_text = st.empty()
    all_rows = []
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        futures = {executor.submit(_fetch_groww_daily, args[0], args[1], args[2]): args[0] for args in to_fetch}
        completed = 0
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            if res:
                all_rows.extend(res)
            completed += 1
            progress_bar.progress(completed / len(to_fetch))
            status_text.markdown(f"**Fetching missing historical daily data... {completed}/{len(to_fetch)}**")

    if all_rows:
        c2 = get_db_connection()
        cur2 = c2.cursor()
        q = '''
            INSERT INTO market_data (symbol, date, open, high, low, close, volume)
            VALUES %s
            ON CONFLICT (symbol, date) DO NOTHING
        '''
        psycopg2.extras.execute_values(cur2, q, all_rows)
        c2.commit()
        c2.close()
        
    progress_bar.empty()
    status_text.empty()

@st.cache_data(ttl=3600)
def get_top_800_symbols():
    try:
        df = pd.read_csv(r"d:\marubozu\scratch\top_800_liquid_symbols_only_2021_2025.csv")
        col = 'symbol' if 'symbol' in df.columns else df.columns[-1]
        return df[col].dropna().unique().tolist()
    except Exception:
        # Fallback if file doesn't exist
        return []

def get_historical_closes(symbols):
    """Fetch T-1 and T-5 close prices from local DB."""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute("""
            SELECT symbol, date, close, rn
            FROM (
                SELECT symbol, date, close,
                       ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY date DESC) as rn
                FROM market_data 
                WHERE symbol = ANY(%s)
            ) tmp
            WHERE rn <= 6
        """, (symbols,))
        
        rows = cur.fetchall()
        conn.close()
        
        from collections import defaultdict
        data = defaultdict(list)
        for sym, dt, close_val, rn in rows:
            data[sym].append((str(dt), float(close_val)))
            
        history = {sym: {'t1': None, 't5': None} for sym in symbols}
        today_str = datetime.now(IST).strftime("%Y-%m-%d")
        
        for sym, records in data.items():
            records.sort(key=lambda x: x[0], reverse=True)
            if records and records[0][0].startswith(today_str):
                records = records[1:]
                
            if len(records) >= 1:
                history[sym]['t1'] = records[0][1]
            if len(records) >= 5:
                history[sym]['t5'] = records[4][1]
                
        return history
    except Exception as e:
        st.error(f"DB Error: {e}")
        return {}

def fetch_live_5min_groww(sym, start_ms, end_ms):
    """Fetch today's 5-minute candles."""
    url = f"https://groww.in/v1/api/charting_service/v2/chart/exchange/NSE/segment/CASH/{sym}?intervalInMinutes=5&minimal=false&startTimeInMillis={start_ms}&endTimeInMillis={end_ms}"
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        resp = requests.get(url, headers=headers, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            if 'candles' in data and data['candles']:
                return sym, data['candles']
    except Exception:
        pass
    return sym, []

def fetch_all_live_data(symbols):
    now = datetime.now(IST)
    today_start = now.replace(hour=9, minute=0, second=0, microsecond=0)
    start_ms = int(today_start.timestamp() * 1000)
    end_ms = int(now.timestamp() * 1000)
    
    results = {}
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
        futures = {executor.submit(fetch_live_5min_groww, sym, start_ms, end_ms): sym for sym in symbols}
        completed = 0
        for future in concurrent.futures.as_completed(futures):
            sym, candles = future.result()
            results[sym] = candles
            completed += 1
            if completed % 20 == 0 or completed == len(symbols):
                progress_bar.progress(completed / len(symbols))
                status_text.markdown(f"**Fetching ultra-fast live 5-min data from Groww... {completed}/{len(symbols)}**")
                
    progress_bar.empty()
    status_text.empty()
    return results

def process_live_signals():
    symbols = get_top_800_symbols()
    if not symbols:
        st.error("Could not load Top 800 symbols CSV.")
        return
        
    check_and_update_missing_data(symbols)
    hist = get_historical_closes(symbols)
    live_candles = fetch_all_live_data(symbols)
    
    target_time_str = "15:05"
    
    # Rebuild today's candle up to target_time
    market_stats = []
    processed_data = {}
    
    for sym in symbols:
        cands = live_candles.get(sym, [])
        if not cands: continue
        
        t1 = hist.get(sym, {}).get('t1')
        t5 = hist.get(sym, {}).get('t5')
        if t1 is None or t5 is None: continue
        
        early_high = -np.inf
        early_low = np.inf
        entry_price = None
        open_price = None
        
        for c in cands:
            ts, o, h, l, cl, v = c
            dt = datetime.fromtimestamp(ts, IST)
            time_str = dt.strftime("%H:%M")
            
            if open_price is None:
                open_price = float(o)
                
            if time_str <= target_time_str:
                early_high = max(early_high, float(h))
                early_low = min(early_low, float(l))
                entry_price = float(cl) # Last close up to target
                
        if entry_price is None: continue
        
        ret1 = ((entry_price / t1) - 1.0) * 100.0
        ret5 = ((entry_price / t5) - 1.0) * 100.0
        signal_range = early_high - early_low
        close_pos = ((entry_price - early_low) / signal_range * 100.0) if signal_range > 0 else 0
        upper_wick = ((early_high - max(open_price, entry_price)) / entry_price * 100.0)
        
        market_stats.append(ret1)
        
        processed_data[sym] = {
            'entry_price': entry_price,
            'open_price': open_price,
            'ret1': ret1,
            'ret5': ret5,
            'close_pos': close_pos,
            'upper_wick': upper_wick,
            'early_high': early_high,
            'early_low': early_low
        }
        
    # Calculate Regime
    if not market_stats:
        st.error("No valid market data retrieved. Waiting for market open?")
        return
        
    total_valid = len(market_stats)
    up_count = sum(1 for r in market_stats if r > 0)
    breadth_count = sum(1 for r in market_stats if r >= 3.0)
    
    mkt_up_pct = (up_count / total_valid) * 100.0
    breadth_pct = (breadth_count / total_valid) * 100.0
    
    st.session_state.last_scan_time = datetime.now(IST)
    st.session_state.mkt_up = mkt_up_pct
    st.session_state.breadth = breadth_pct
    
    signals = []
    
    # Strong Regime Rule from the Screenshot
    is_strong = mkt_up_pct >= 45.0 and breadth_pct >= 5.0
    st.session_state.is_strong = is_strong
    
    if is_strong:
        for sym, d in processed_data.items():
            if (d['upper_wick'] <= 0.0 and 
                d['ret5'] >= 20.0 and 
                d['ret1'] >= 3.0 and 
                d['entry_price'] <= 500.0 and 
                d['close_pos'] >= 90.0):
                
                signals.append((sym, d))
                
    st.session_state.signals = signals

# --- Main UI ---
st.title("⚡ Daily_ST_M Ultra-Fast Scanner")
st.markdown("Live 15:05 Signals exactly mirroring the 780-trade backend rules. Connects directly to Groww API.")

col1, col2 = st.columns([3, 1])

with col1:
    if st.button("🚀 RUN SCAN NOW", type="primary", use_container_width=True):
        with st.spinner("Connecting to Groww API & Cranking Engine..."):
            process_live_signals()

with col2:
    auto_refresh = st.checkbox("Auto-Scan every 5 mins", value=False)
    if auto_refresh:
        st.write("⏱ Auto-scanning activated.")

if 'last_scan_time' in st.session_state:
    st.markdown("---")
    
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.markdown(f"""
        <div class="metric-card">
            <div class="metric-title">Market Up %</div>
            <div class="metric-value" style="color: {'#3fb950' if st.session_state.mkt_up >= 45 else '#ff7b72'}">
                {st.session_state.mkt_up:.1f}%
            </div>
            <div style="font-size: 11px; color: #8b949e; margin-top: 5px;">Requirement: ≥ 45%</div>
        </div>
        """, unsafe_allow_html=True)
    with c2:
        st.markdown(f"""
        <div class="metric-card">
            <div class="metric-title">Breadth (≥ 3%)</div>
            <div class="metric-value" style="color: {'#3fb950' if st.session_state.breadth >= 5 else '#ff7b72'}">
                {st.session_state.breadth:.1f}%
            </div>
            <div style="font-size: 11px; color: #8b949e; margin-top: 5px;">Requirement: ≥ 5%</div>
        </div>
        """, unsafe_allow_html=True)
    with c3:
        is_st = st.session_state.is_strong
        st.markdown(f"""
        <div class="metric-card">
            <div class="metric-title">Current Regime</div>
            <div class="metric-value" style="color: {'#3fb950' if is_st else '#ff7b72'}">
                {'STRONG' if is_st else 'WEAK'}
            </div>
            <div style="font-size: 11px; color: #8b949e; margin-top: 5px;">{'Scanning active' if is_st else 'Trades skipped'}</div>
        </div>
        """, unsafe_allow_html=True)
    with c4:
        st.markdown(f"""
        <div class="metric-card">
            <div class="metric-title">Signals Found</div>
            <div class="metric-value" style="color: #a371f7;">
                {len(st.session_state.signals)}
            </div>
            <div style="font-size: 11px; color: #8b949e; margin-top: 5px;">As of {st.session_state.last_scan_time.strftime('%I:%M:%S %p')}</div>
        </div>
        """, unsafe_allow_html=True)
        
    st.markdown("### 🎯 Live Signals")
    
    if len(st.session_state.signals) == 0:
        if st.session_state.is_strong:
            st.info("No stocks currently meet all technical rules (Ret5 ≥ 20%, Ret1 ≥ 3%, ClosePos ≥ 90%, Wick = 0, Price ≤ 500).")
        else:
            st.warning("Market is not in Strong Regime. All signals are currently filtered out.")
    else:
        for sym, d in st.session_state.signals:
            with st.expander(f"🟢 {sym} - Signal triggered at ₹{d['entry_price']:.2f}"):
                st.markdown(f"""
                <div style="background: rgba(22, 27, 34, 0.8); border: 1px solid #30363d; border-radius: 8px; padding: 15px; margin-bottom: 15px;">
                    <h4 style="color: #58a6ff; margin-top: 0;">Rule Verification Checklist</h4>
                    <table style="width: 100%; text-align: left; border-collapse: collapse;">
                        <tr style="border-bottom: 1px solid #30363d;">
                            <th style="padding: 8px; color: #8b949e;">Parameter</th>
                            <th style="padding: 8px; color: #8b949e;">Calculated Value</th>
                            <th style="padding: 8px; color: #8b949e;">Required Threshold</th>
                            <th style="padding: 8px; color: #8b949e;">Status</th>
                        </tr>
                        <tr style="border-bottom: 1px solid #30363d;">
                            <td style="padding: 8px;">Ret5</td>
                            <td style="padding: 8px; font-weight: bold; color: #3fb950;">{d['ret5']:.2f}%</td>
                            <td style="padding: 8px;">&ge; 20%</td>
                            <td style="padding: 8px;">✅ Passed</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #30363d;">
                            <td style="padding: 8px;">Ret1</td>
                            <td style="padding: 8px; font-weight: bold; color: #3fb950;">{d['ret1']:.2f}%</td>
                            <td style="padding: 8px;">&ge; 3%</td>
                            <td style="padding: 8px;">✅ Passed</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #30363d;">
                            <td style="padding: 8px;">Close Position</td>
                            <td style="padding: 8px; font-weight: bold; color: #3fb950;">{d['close_pos']:.2f}%</td>
                            <td style="padding: 8px;">&ge; 90%</td>
                            <td style="padding: 8px;">✅ Passed</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #30363d;">
                            <td style="padding: 8px;">Upper Wick</td>
                            <td style="padding: 8px; font-weight: bold; color: #3fb950;">{d['upper_wick']:.2f}%</td>
                            <td style="padding: 8px;">&le; 0%</td>
                            <td style="padding: 8px;">✅ Passed</td>
                        </tr>
                        <tr>
                            <td style="padding: 8px;">Close Price</td>
                            <td style="padding: 8px; font-weight: bold; color: #3fb950;">₹{d['entry_price']:.2f}</td>
                            <td style="padding: 8px;">&le; 500</td>
                            <td style="padding: 8px;">✅ Passed</td>
                        </tr>
                    </table>
                </div>
                
                <div style="background: rgba(22, 27, 34, 0.8); border: 1px solid #30363d; border-radius: 8px; padding: 15px;">
                    <h4 style="color: #a371f7; margin-top: 0;">Synthetic Today OHLC (09:15 to 15:05)</h4>
                    <p style="margin: 0; color: #c9d1d9;">
                        <strong>Open:</strong> ₹{d['open_price']:.2f} &nbsp;&nbsp;|&nbsp;&nbsp;
                        <strong>High:</strong> ₹{d['early_high']:.2f} &nbsp;&nbsp;|&nbsp;&nbsp;
                        <strong>Low:</strong> ₹{d['early_low']:.2f} &nbsp;&nbsp;|&nbsp;&nbsp;
                        <strong>Close (15:05):</strong> ₹{d['entry_price']:.2f}
                    </p>
                </div>
                """, unsafe_allow_html=True)

if auto_refresh:
    time.sleep(300) # 5 minutes
    st.rerun()
