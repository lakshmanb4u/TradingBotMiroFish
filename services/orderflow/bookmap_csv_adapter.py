"""
Bookmap export adapter - handles multiple formats with strict replay safety

Key features:
- CSV/JSON export support
- Schema normalization
- Null-safe field handling
- Nanosecond timestamp precision
- Source auditing
"""
import pandas as pd
import json
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Optional
import pytz

class BookmapAdapter:
    # Required schema with type hints
    REQUIRED_SCHEMA = {
        'timestamp': 'datetime64[ns]',
        'symbol': 'str',
        'price': 'float64',
        'best_bid': 'float64',
        'best_ask': 'float64',
        'bid_depth_top': 'float64',
        'ask_depth_top': 'float64',
        'traded_volume': 'float64',
        'liquidity_above': 'float64',
        'liquidity_below': 'float64',
        'source_file': 'str',
        'ingestion_ts': 'datetime64[ns]'
    }
    
    # Optional nullable fields
    OPTIONAL_FIELDS = {
        'buy_volume': 'float64',
        'sell_volume': 'float64',
        'delta': 'float64',
        'cvd': 'float64',
        'imbalance_score': 'float64'
    }

    def __init__(self, raw_dir: str):
        self.raw_dir = Path(raw_dir)
        self.audit_log = []
    
    def detect_format(self, file_path: Path) -> str:
        """Detect export format from file extension/content"""
        if file_path.suffix.lower() == '.csv':
            return 'csv'
        elif file_path.suffix.lower() == '.json':
            return 'json'
        raise ValueError(f"Unsupported file format: {file_path.suffix}")
    
    def load_file(self, file_path: Path) -> pd.DataFrame:
        """Load and normalize a single export file"""
        file_format = self.detect_format(file_path)
        
        if file_format == 'csv':
            df = pd.read_csv(file_path)
        elif file_format == 'json':
            with open(file_path) as f:
                data = json.load(f)
            df = pd.DataFrame(data)
        
        return self.normalize(df, str(file_path))
    
    def normalize(self, raw_df: pd.DataFrame, source_file: str) -> pd.DataFrame:
        """Normalize raw data to standard schema"""
        # Initialize with required fields
        normalized = {}
        
        # Process required fields
        for field, dtype in self.REQUIRED_SCHEMA.items():
            if field in ['source_file', 'ingestion_ts']:
                continue  # Handled separately
                
            if field in raw_df.columns:
                normalized[field] = raw_df[field].astype(dtype)
            else:
                normalized[field] = pd.Series(dtype=dtype)
                self.audit_log.append(f"Missing required field: {field}")
        
        # Process optional fields
        delta_available = False
        for field, dtype in self.OPTIONAL_FIELDS.items():
            if field in raw_df.columns:
                normalized[field] = raw_df[field].astype(dtype)
                if field in ['buy_volume', 'sell_volume', 'delta']:
                    delta_available = True
            else:
                normalized[field] = pd.Series(dtype=dtype)
        
        # Add metadata
        normalized['source_file'] = source_file
        normalized['ingestion_ts'] = datetime.now(timezone.utc)
        normalized['delta_available'] = delta_available
        
        # Convert to DataFrame
        df = pd.DataFrame(normalized)
        
        # Validate
        self.validate(df)
        
        return df
    
    def validate(self, df: pd.DataFrame):
        """Run replay safety checks"""
        # Check timestamp monotonicity
        if not df['timestamp'].is_monotonic_increasing:
            self.audit_log.append("Timestamps not monotonic - potential lookahead risk")
        
        # Check for duplicates
        if df['timestamp'].duplicated().any():
            self.audit_log.append("Duplicate timestamps detected")
        
        # Check symbol consistency
        if df['symbol'].nunique() > 1:
            self.audit_log.append("Multiple symbols detected in single file")
    
    def export_parquet(self, df: pd.DataFrame, output_path: Path):
        """Export normalized data to parquet"""
        df.to_parquet(output_path, index=False)
    
    def generate_audit(self) -> Dict:
        """Generate source audit report"""
        return {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'checks': self.audit_log,
            'schema_status': {
                'required_fields_missing': [
                    f for f in self.REQUIRED_SCHEMA 
                    if f in self.audit_log
                ],
                'delta_available': any(
                    'delta' not in log for log in self.audit_log
                )
            }
        }