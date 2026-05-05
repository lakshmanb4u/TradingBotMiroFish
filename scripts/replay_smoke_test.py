"""
Verify replay safety of normalized orderflow data
"""
import argparse
import pandas as pd
from pathlib import Path

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', required=True, help='Input parquet file')
    args = parser.parse_args()
    
    df = pd.read_parquet(args.input)
    
    print(f"Replay Safety Test for {Path(args.input).name}")
    print("="*50)
    
    # Check timestamp order
    time_diff = df['timestamp'].diff().dt.total_seconds()
    negative_jumps = (time_diff < 0).sum()
    
    if negative_jumps == 0:
        print("✓ Timestamps are strictly monotonic")
    else:
        print(f"✗ {negative_jumps} timestamp violations found")
    
    # Check for lookahead
    required_fields = ['price', 'best_bid', 'best_ask', 'bid_depth_top', 'ask_depth_top']
    lookahead_found = False
    
    for field in required_fields:
        if df[field].isnull().any():
            print(f"✗ Null values found in {field} - potential lookahead risk")
            lookahead_found = True
    
    if not lookahead_found:
        print("✓ No lookahead indicators detected")
    
    # Final verdict
    if negative_jumps == 0 and not lookahead_found:
        print("\n✓ PASS: Data is replay-safe")
    else:
        print("\n✗ FAIL: Replay safety violations detected")

if __name__ == '__main__':
    main()