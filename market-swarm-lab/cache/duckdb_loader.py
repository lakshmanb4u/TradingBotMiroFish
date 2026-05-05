#!/usr/bin/env python3
"""
DuckDB Loader: Fast access to replay windows

Loads parquet files into DuckDB for:
- Sub-millisecond queries
- Automatic indexing
- No in-memory materialization
"""

from pathlib import Path
import time

try:
    import duckdb
except ImportError:
    print("[!] DuckDB not installed. Install: pip install duckdb")
    import sys
    sys.exit(1)

class DuckDBLoader:
    def __init__(self, cache_dir=None):
        self.cache_dir = Path(cache_dir or "/Users/laxman_2026_mac_mini/.openclaw/workspace/market-swarm-lab/cache")
        self.events_parquet = self.cache_dir / "signals_26_50_events.parquet"
        self.metadata_parquet = self.cache_dir / "signals_26_50_metadata.parquet"
        
        self.conn = None
    
    def connect(self):
        """Create in-memory DuckDB connection and load tables"""
        print("[*] Initializing DuckDB...")
        self.conn = duckdb.connect(':memory:')
        
        start = time.time()
        
        # Load events
        print(f"[*] Loading events from {self.events_parquet.name}...")
        self.conn.execute(f"""
            CREATE TABLE replay_events AS
            SELECT * FROM read_parquet('{self.events_parquet}')
        """)
        
        # Load metadata
        print(f"[*] Loading metadata from {self.metadata_parquet.name}...")
        self.conn.execute(f"""
            CREATE TABLE signal_metadata AS
            SELECT * FROM read_parquet('{self.metadata_parquet}')
        """)
        
        # Create indexes
        print(f"[*] Creating indexes...")
        self.conn.execute("CREATE INDEX idx_signal_id ON replay_events(signal_id)")
        self.conn.execute("CREATE INDEX idx_signal_ts ON signal_metadata(signal_ts_utc)")
        
        elapsed = time.time() - start
        print(f"[✓] DuckDB loaded in {elapsed:.2f}s\n")
    
    def get_signal_events(self, signal_id):
        """Get all events for a signal as list of dicts"""
        if not self.conn:
            self.connect()
        
        query = f"""
            SELECT * FROM replay_events
            WHERE signal_id = {signal_id}
            ORDER BY event_idx
        """
        
        result = self.conn.execute(query).fetchall()
        columns = [desc[0] for desc in self.conn.description]
        
        return [dict(zip(columns, row)) for row in result]
    
    def get_signal_events_array(self, signal_id):
        """Get all prices for a signal as numpy array"""
        if not self.conn:
            self.connect()
        
        query = f"""
            SELECT price FROM replay_events
            WHERE signal_id = {signal_id}
            ORDER BY event_idx
        """
        
        result = self.conn.execute(query).fetchall()
        return [row[0] for row in result]
    
    def find_followthrough_breakout(self, signal_id, lookback_events=100, threshold_ticks=0.5):
        """Find first event that breaks beyond initial adverse point"""
        if not self.conn:
            self.connect()
        
        # Get direction
        dir_result = self.conn.execute(f"""
            SELECT direction FROM signal_metadata
            WHERE signal_id = {signal_id}
        """).fetchall()
        
        if not dir_result:
            return None
        
        direction = dir_result[0][0]
        
        # Find min/max in lookback
        lookback_query = f"""
            SELECT 
                MIN(price) as min_price,
                MAX(price) as max_price
            FROM replay_events
            WHERE signal_id = {signal_id}
            AND event_idx < {lookback_events}
        """
        
        lookback_result = self.conn.execute(lookback_query).fetchall()
        min_price, max_price = lookback_result[0]
        
        # Find breakout
        if direction == "SHORT":
            breakout_price = min_price - threshold_ticks
            breakout_query = f"""
                SELECT MIN(event_idx) as idx
                FROM replay_events
                WHERE signal_id = {signal_id}
                AND event_idx >= {lookback_events}
                AND price < {breakout_price}
            """
        else:
            breakout_price = max_price + threshold_ticks
            breakout_query = f"""
                SELECT MIN(event_idx) as idx
                FROM replay_events
                WHERE signal_id = {signal_id}
                AND event_idx >= {lookback_events}
                AND price > {breakout_price}
            """
        
        result = self.conn.execute(breakout_query).fetchall()
        if result and result[0][0]:
            return result[0][0]
        return None
    
    def get_mae_mfe(self, signal_id, entry_idx=0):
        """Calculate MAE/MFE from entry point"""
        if not self.conn:
            self.connect()
        
        # Get entry price and direction
        result = self.conn.execute(f"""
            SELECT e.price, m.direction
            FROM replay_events e
            JOIN signal_metadata m ON e.signal_id = m.signal_id
            WHERE e.signal_id = {signal_id}
            AND e.event_idx = {entry_idx}
        """).fetchall()
        
        if not result:
            return None, None
        
        entry_price, direction = result[0]
        
        # Get all subsequent prices
        prices_result = self.conn.execute(f"""
            SELECT price FROM replay_events
            WHERE signal_id = {signal_id}
            AND event_idx > {entry_idx}
            ORDER BY event_idx
        """).fetchall()
        
        prices = [row[0] for row in prices_result]
        
        if not prices:
            return 0.0, 0.0
        
        if direction == "SHORT":
            mfe = max(entry_price - p for p in prices)
            mae = max(p - entry_price for p in prices)
        else:
            mfe = max(p - entry_price for p in prices)
            mae = max(entry_price - p for p in prices)
        
        return mae, mfe
    
    def close(self):
        """Close DuckDB connection"""
        if self.conn:
            self.conn.close()
            print("[✓] DuckDB closed")

def benchmark():
    """Benchmark DuckDB performance"""
    print("=" * 60)
    print("DuckDB Performance Benchmark")
    print("=" * 60 + "\n")
    
    loader = DuckDBLoader()
    loader.connect()
    
    # Benchmark 1: Load all events for signal 26
    print("[Benchmark 1] Load all events for signal 26")
    start = time.time()
    events = loader.get_signal_events(26)
    elapsed = time.time() - start
    print(f"  Time: {elapsed*1000:.2f}ms")
    print(f"  Events: {len(events)}\n")
    
    # Benchmark 2: Find follow-through breakout
    print("[Benchmark 2] Find follow-through breakout for signal 26")
    start = time.time()
    idx = loader.find_followthrough_breakout(26)
    elapsed = time.time() - start
    print(f"  Time: {elapsed*1000:.2f}ms")
    print(f"  Breakout index: {idx}\n")
    
    # Benchmark 3: Calculate MAE/MFE
    print("[Benchmark 3] Calculate MAE/MFE for signal 26")
    start = time.time()
    mae, mfe = loader.get_mae_mfe(26)
    elapsed = time.time() - start
    print(f"  Time: {elapsed*1000:.2f}ms")
    print(f"  MAE: {mae:.2f}, MFE: {mfe:.2f}\n")
    
    # Benchmark 4: Iterate all 25 signals
    print("[Benchmark 4] Iterate metrics for all 25 signals")
    start = time.time()
    for sig_id in range(26, 51):
        mae, mfe = loader.get_mae_mfe(sig_id)
    elapsed = time.time() - start
    print(f"  Time for 25 signals: {elapsed*1000:.2f}ms")
    print(f"  Per-signal: {elapsed*1000/25:.2f}ms\n")
    
    loader.close()

if __name__ == "__main__":
    benchmark()
