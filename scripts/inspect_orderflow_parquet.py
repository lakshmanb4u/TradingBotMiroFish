"""
Inspect normalized orderflow parquet files
"""
import argparse
import pandas as pd
from pathlib import Path

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', required=True, help='Input parquet file')
    args = parser.parse_args()
    
    df = pd.read_parquet(args.input)
    
    print(f"Orderflow Data Inspection for {Path(args.input).name}")
    print("="*50)
    
    # Basic stats
    print(f"\n[Basic Statistics]")
    print(f"Rows: {len(df):,}")
    print(f"Columns: {len(df.columns)}")
    print(f"Time range: {df['timestamp'].min()} to {df['timestamp'].max()}")
    
    # Null analysis
    print(f"\n[Null Analysis]")
    null_counts = df.isnull().sum()
    for col, count in null_counts.items():
        if count > 0:
            print(f"{col}: {count} nulls ({count/len(df):.1%})")
    
    # Replay safety
    print(f"\n[Replay Safety]")
    time_diff = df['timestamp'].diff().dt.total_seconds()
    negative_jumps = (time_diff < 0).sum()
    print(f"Timestamp monotonic: {'✓' if negative_jumps == 0 else f'{negative_jumps} violations'}")
    print(f"Duplicate timestamps: {df['timestamp'].duplicated().sum()}")
    
    # Field availability
    print(f"\n[Field Availability]")
    print(f"Delta available: {df['delta_available'].any()}")
    print(f"Liquidity fields: {all(f in df.columns for f in ['liquidity_above', 'liquidity_below'])}")

if __name__ == '__main__':
    main()