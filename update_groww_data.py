import os
import glob
import time
import requests
import pandas as pd
from datetime import datetime, timezone, timedelta
import concurrent.futures

DATA_DIR = r"D:\backtesting\backtesting\data"
IST = timezone(timedelta(hours=5, minutes=30))

def fetch_and_update(csv_path: str):
    try:
        df = pd.read_csv(csv_path)
        if df.empty:
            return f"Empty CSV: {csv_path}"
            
        # Parse the last datetime
        last_dt_str = df.iloc[-1]['datetime']
        last_dt = pd.to_datetime(last_dt_str)
        
        # We need data from the day after the last date
        # Start time in ms
        start_ms = int(last_dt.timestamp() * 1000)
        now_ms = int(time.time() * 1000)
        
        # If the last date is very recent, no need to fetch
        if (now_ms - start_ms) < 86400 * 1000:
            return f"Already up to date: {csv_path}"
            
        symbol = os.path.basename(csv_path).replace("_Day.csv", "")
        
        url = (
            f"https://groww.in/v1/api/charting_service/v2/chart/"
            f"exchange/NSE/segment/CASH/{symbol}"
            f"?intervalInMinutes=1440&minimal=false"
            f"&startTimeInMillis={start_ms}&endTimeInMillis={now_ms}"
        )
        
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, headers=headers)
        if response.status_code != 200:
            return f"Failed to fetch {symbol}: {response.status_code}"
            
        data = response.json()
        if 'candles' not in data or not data['candles']:
            return f"No new candles for {symbol}"
            
        new_rows = []
        for candle in data['candles']:
            # candle format: [timestamp, open, high, low, close, volume]
            timestamp = candle[0]
            dt = datetime.fromtimestamp(timestamp, IST)
            # Format to match existing CSV: 2019-12-30 05:30:00+05:30
            dt_str = dt.strftime("%Y-%m-%d 05:30:00+05:30")
            
            # Skip if this date is already in our dataframe
            if dt.date() <= last_dt.date():
                continue
                
            new_rows.append({
                'datetime': dt_str,
                'open': candle[1],
                'high': candle[2],
                'low': candle[3],
                'close': candle[4],
                'volume': candle[5]
            })
            
        if new_rows:
            new_df = pd.DataFrame(new_rows)
            # Append to CSV
            new_df.to_csv(csv_path, mode='a', header=False, index=False)
            return f"Updated {symbol} with {len(new_rows)} new rows."
        else:
            return f"No new dates to append for {symbol}."
            
    except Exception as e:
        return f"Error processing {csv_path}: {str(e)}"

def update_all_data():
    csv_files = glob.glob(os.path.join(DATA_DIR, "*_Day.csv"))
    print(f"Found {len(csv_files)} CSV files. Starting update process from Groww...")
    
    updated_count = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
        futures = {executor.submit(fetch_and_update, csv): csv for csv in csv_files}
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            if "Updated" in res:
                updated_count += 1
                print(res)
                
    print(f"Data update complete. {updated_count} files were updated with new data.")

if __name__ == "__main__":
    update_all_data()
