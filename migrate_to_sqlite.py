import os
import glob
import sqlite3
import pandas as pd
import concurrent.futures

DATA_DIR = r"D:\backtesting\backtesting\data"
DB_PATH = r"D:\overnight\live_signals.db"

def process_csv(filepath: str):
    try:
        if filepath.endswith('.csv'):
            df = pd.read_csv(filepath)
        else:
            df = pd.read_parquet(filepath)
            
        if df.empty:
            return None
            
        # Normalize datetime to date (drop time/tz info)
        df["date"] = pd.to_datetime(df["datetime"]).dt.date.astype(str)
        df = df.drop(columns=["datetime"])
        
        # Ensure columns are lowercase
        df.columns = [c.lower() for c in df.columns]
        
        # Add symbol column
        basename = os.path.basename(filepath)
        symbol = basename.replace("_Day.parquet", "").replace("_Day.csv", "")
        df.insert(0, "symbol", symbol)
        
        return df
        
    except Exception as e:
        print(f"Error reading {filepath}: {e}")
        return None

def run_migration():
    from database import init_db
    init_db()  # Ensure table exists
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    print("Clearing existing market_data to avoid duplicates...")
    cursor.execute("DELETE FROM market_data")
    conn.commit()
    
    files = glob.glob(os.path.join(DATA_DIR, "*_Day.csv"))
    print(f"Found {len(files)} files. Reading all data into memory (this may take a minute)...")
    
    all_dfs = []
    
    with concurrent.futures.ProcessPoolExecutor(max_workers=8) as executor:
        for df in executor.map(process_csv, files):
            if df is not None:
                all_dfs.append(df)
                
    if not all_dfs:
        print("No data found!")
        return
        
    print(f"Combining {len(all_dfs)} dataframes...")
    master_df = pd.concat(all_dfs, ignore_index=True)
    
    print(f"Inserting {len(master_df)} rows into SQLite...")
    master_df.to_sql("market_data", conn, if_exists="append", index=False)
    
    conn.close()
    print("Migration complete!")

if __name__ == "__main__":
    run_migration()
