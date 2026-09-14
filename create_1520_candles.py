import os
import glob
from pathlib import Path
import pandas as pd
import warnings

warnings.filterwarnings("ignore")

def process_file(file_path: str, out_dir: str):
    symbol = os.path.basename(file_path).split('_5min')[0]
    out_path = os.path.join(out_dir, f"{symbol}_Day.parquet")
    
    try:
        df = pd.read_parquet(file_path)
        if df.empty:
            return
            
        # Convert to IST if timezone-aware, or assume UTC and localize then convert
        if df['datetime'].dt.tz is None:
            df['datetime'] = df['datetime'].dt.tz_localize('UTC').dt.tz_convert('Asia/Kolkata')
        else:
            df['datetime'] = df['datetime'].dt.tz_convert('Asia/Kolkata')
            
        # Extract date and time
        df['date'] = df['datetime'].dt.date
        df['time'] = df['datetime'].dt.time
        
        # Filter for candles from 09:15 to 15:15 (which closes at 15:20)
        from datetime import time
        start_time = time(9, 15)
        end_time = time(15, 15)
        
        mask = (df['time'] >= start_time) & (df['time'] <= end_time)
        df_filtered = df[mask]
        
        if df_filtered.empty:
            return
            
        # Group by date to create daily candles
        daily = df_filtered.groupby('date').agg(
            open=('open', 'first'),
            high=('high', 'max'),
            low=('low', 'min'),
            close=('close', 'last'),
            volume=('volume', 'sum')
        ).reset_index()
        
        # Add datetime column (using the date) to match original format
        # Setting to UTC midnight for consistency with previous files
        daily['datetime'] = pd.to_datetime(daily['date']).dt.tz_localize('UTC')
        daily = daily[['datetime', 'open', 'high', 'low', 'close', 'volume']]
        
        # Save to the new directory
        daily.to_parquet(out_path, index=False)
        
    except Exception as e:
        print(f"Error processing {symbol}: {e}")

def main():
    source_dir = r"C:\Users\Yug\Desktop\datas"
    out_dir = r"C:\Users\Yug\Desktop\datas_1520"
    
    os.makedirs(out_dir, exist_ok=True)
    
    files = glob.glob(os.path.join(source_dir, "*_5min.parquet"))
    print(f"Found {len(files)} 5-minute parquet files.")
    
    for i, f in enumerate(files):
        if i % 100 == 0:
            print(f"  Processed {i}/{len(files)} files...")
        process_file(f, out_dir)
        
    print(f"Finished! Output saved to {out_dir}")

if __name__ == "__main__":
    main()
