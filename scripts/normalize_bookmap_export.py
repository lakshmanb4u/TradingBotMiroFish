"""
Normalize Bookmap exports to replay-safe parquet format
"""
import argparse
from pathlib import Path
import pandas as pd
from datetime import datetime
from services.orderflow.bookmap_csv_adapter import BookmapAdapter
import json

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', required=True, help='Input Bookmap export file')
    parser.add_argument('--output-dir', default='state/orderflow', help='Output directory')
    args = parser.parse_args()
    
    # Setup paths
    input_path = Path(args.input)
    output_dir = Path(args.output_dir)
    parquet_dir = output_dir / datetime.now().strftime('%Y-%m-%d')
    audit_dir = output_dir / 'audit'
    
    # Ensure directories exist
    parquet_dir.mkdir(parents=True, exist_ok=True)
    audit_dir.mkdir(parents=True, exist_ok=True)
    
    # Process file
    adapter = BookmapAdapter(input_path.parent)
    df = adapter.load_file(input_path)
    
    # Write outputs
    parquet_path = parquet_dir / 'es_orderflow.parquet'
    adapter.export_parquet(df, parquet_path)
    
    audit_path = audit_dir / 'source_audit.json'
    with open(audit_path, 'w') as f:
        json.dump(adapter.generate_audit(), f)
    
    print(f"Successfully normalized {input_path.name}")
    print(f"Parquet output: {parquet_path}")
    print(f"Audit log: {audit_path}")

if __name__ == '__main__':
    main()