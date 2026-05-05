#!/usr/bin/env python3
"""
Parquet Writer: Extract JSONL windows → Parquet format

Converts 55K events per signal into row-wise parquet tables
instead of giant CSV cells.

Usage:
    python3 parquet_writer.py --signals 26-50
"""

import sys
from pathlib import Path
from datetime import datetime, timedelta
import time

try:
    import pyarrow as pa
    import pyarrow.parquet as pq
except ImportError:
    print("[!] PyArrow not installed. Install: pip install pyarrow")
    sys.exit(1)

sys.path.insert(0, str(Path(__file__).parent.parent / "services" / "orderflow"))

from real_signal_extractor import RealSignalExtractor
from jsonl_window_accessor import JsonlWindowAccessor

class ParquetWriter:
    def __init__(self, signals_csv, jsonl_path, output_dir):
        self.signals_csv = signals_csv
        self.jsonl_path = jsonl_path
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        self.extractor = RealSignalExtractor(signals_csv)
        self.accessor = None
        
        self.events_list = []
        self.metadata_list = []
    
    def run(self, start_idx=25, end_idx=50):
        print(f"[*] Building parquet cache for signals {start_idx+1}-{end_idx}")
        
        # Load signals
        signals = self.extractor.load_signals(filter_date="2026-05-04", min_confidence=0.0)
        signals = signals[start_idx:end_idx]
        print(f"[✓] Loaded {len(signals)} signals")
        
        # Build JSONL index
        print(f"[*] Building JSONL index...")
        self.accessor = JsonlWindowAccessor(self.jsonl_path)
        start = time.time()
        self.accessor.build_index(sample_interval=10000)
        elapsed = time.time() - start
        print(f"[✓] Index built in {elapsed:.1f}s")
        
        # Extract events for each signal
        print(f"[*] Extracting events...\n")
        for sig_idx, sig in enumerate(signals, 1):
            signal_ts = datetime.fromisoformat(sig.signal_event_utc)
            
            # Get outcome window (30 minutes)
            outcome_start = signal_ts
            outcome_end = signal_ts + timedelta(minutes=30)
            outcome_events = self.accessor.get_window(outcome_start, outcome_end)
            
            if not outcome_events:
                print(f"  {sig_idx:2d} (sig {start_idx+sig_idx}): ⚠️ No events")
                continue
            
            is_safe, msg = self.accessor.validate_replay_safe(outcome_start, outcome_end, outcome_events)
            if not is_safe:
                print(f"  {sig_idx:2d} (sig {start_idx+sig_idx}): ⚠️ Not replay-safe ({msg})")
                continue
            
            # Add metadata
            self.metadata_list.append({
                'signal_id': start_idx + sig_idx,
                'signal_ts_utc': sig.signal_event_utc,
                'direction': sig.direction,
                'entry_price': sig.entry_price,
                'candle_low': sig.candle_low,
                'candle_high': sig.candle_high,
                'absorption_confidence': sig.confidence,
            })
            
            # Add events (one per row)
            for event_idx, event in enumerate(outcome_events):
                self.events_list.append({
                    'signal_id': start_idx + sig_idx,
                    'event_idx': event_idx,
                    'timestamp_utc': event.get('timestamp', ''),
                    'price': event['price'],
                    'delta': event.get('delta', 0),
                    'bid_volume': event.get('bid_volume', 0),
                    'ask_volume': event.get('ask_volume', 0),
                    'side_imbalance': event.get('side_imbalance', 0.0),
                    'liquidity_pull': event.get('liquidity_pull', 0),
                    'liquidity_stack': event.get('liquidity_stack', 0),
                })
            
            print(f"  {sig_idx:2d} (sig {start_idx+sig_idx}): ✓ {len(outcome_events):6d} events")
        
        print(f"\n[✓] Extracted {len(self.events_list):,} events across {len(self.metadata_list)} signals")
        
        # Write parquet
        self._write_parquet()
    
    def _write_parquet(self):
        if not self.events_list:
            print("[!] No events to write")
            return
        
        print(f"\n[*] Writing parquet...")
        
        # Events table
        events_path = self.output_dir / "signals_26_50_events.parquet"
        events_table = pa.table({
            'signal_id': pa.array([e['signal_id'] for e in self.events_list], type=pa.int32()),
            'event_idx': pa.array([e['event_idx'] for e in self.events_list], type=pa.int32()),
            'timestamp_utc': pa.array([e['timestamp_utc'] for e in self.events_list], type=pa.string()),
            'price': pa.array([e['price'] for e in self.events_list], type=pa.float64()),
            'delta': pa.array([e['delta'] for e in self.events_list], type=pa.int32()),
            'bid_volume': pa.array([e['bid_volume'] for e in self.events_list], type=pa.int32()),
            'ask_volume': pa.array([e['ask_volume'] for e in self.events_list], type=pa.int32()),
            'side_imbalance': pa.array([e['side_imbalance'] for e in self.events_list], type=pa.float32()),
            'liquidity_pull': pa.array([e['liquidity_pull'] for e in self.events_list], type=pa.int32()),
            'liquidity_stack': pa.array([e['liquidity_stack'] for e in self.events_list], type=pa.int32()),
        })
        
        pq.write_table(events_table, events_path, compression='snappy')
        events_size = events_path.stat().st_size / 1024 / 1024
        print(f"[✓] Events table: {events_path.name} ({events_size:.1f} MB)")
        
        # Metadata table
        metadata_path = self.output_dir / "signals_26_50_metadata.parquet"
        metadata_table = pa.table({
            'signal_id': pa.array([m['signal_id'] for m in self.metadata_list], type=pa.int32()),
            'signal_ts_utc': pa.array([m['signal_ts_utc'] for m in self.metadata_list], type=pa.string()),
            'direction': pa.array([m['direction'] for m in self.metadata_list], type=pa.string()),
            'entry_price': pa.array([m['entry_price'] for m in self.metadata_list], type=pa.float64()),
            'candle_low': pa.array([m['candle_low'] for m in self.metadata_list], type=pa.float64()),
            'candle_high': pa.array([m['candle_high'] for m in self.metadata_list], type=pa.float64()),
            'absorption_confidence': pa.array([m['absorption_confidence'] for m in self.metadata_list], type=pa.float32()),
        })
        
        pq.write_table(metadata_table, metadata_path, compression='snappy')
        metadata_size = metadata_path.stat().st_size / 1024
        print(f"[✓] Metadata table: {metadata_path.name} ({metadata_size:.1f} KB)")
        
        print(f"\n[✓] Parquet cache ready")

if __name__ == "__main__":
    signals_csv = Path("/Users/laxman_2026_mac_mini/.openclaw/workspace/market-swarm-lab/state/orderflow/live/footprint_entry_candidates.csv")
    jsonl_path = Path("/Users/laxman_2026_mac_mini/.openclaw/workspace/market-swarm-lab/state/orderflow/bookmap_api/es_orderflow_2026-05-04.jsonl")
    output_dir = Path("/Users/laxman_2026_mac_mini/.openclaw/workspace/market-swarm-lab/cache")
    
    writer = ParquetWriter(signals_csv, jsonl_path, output_dir)
    writer.run(start_idx=25, end_idx=50)
