#!/usr/bin/env python3
"""
Pipeline Funnel Metrics Monitor
Displays real-time conversion funnel metrics from pipeline_metrics.json
Shows which stage is the bottleneck and what % of candidates get filtered out.
"""

import json
import time
import sys
from pathlib import Path
from datetime import datetime

METRICS_FILE = Path("/Users/laxman_2026_mac_mini/.openclaw/workspace/market-swarm-lab/state/orderflow/live/pipeline_metrics.json")


def read_metrics():
    """Read and parse the pipeline metrics file."""
    if not METRICS_FILE.exists():
        return None
    
    try:
        with open(METRICS_FILE, 'r') as f:
            return json.load(f)
    except Exception as e:
        print(f"[ERROR] Failed to read metrics: {e}")
        return None


def print_funnel_report(metrics):
    """Print a formatted funnel report."""
    if not metrics:
        print("[ERROR] No metrics available")
        return
    
    print("\n" + "="*80)
    print("🔥 PIPELINE CONVERSION FUNNEL ANALYSIS")
    print("="*80)
    
    timestamp = metrics.get("timestamp_utc", "N/A")
    runtime = metrics.get("runtime_seconds", 0)
    
    print(f"\n📊 Report Generated: {timestamp}")
    print(f"⏱️  Session Runtime: {runtime:.0f} seconds ({runtime/60:.1f} minutes)")
    
    # Counts
    counts = metrics.get("counts", {})
    print("\n" + "-"*80)
    print("📈 STAGE COUNTS")
    print("-"*80)
    
    print(f"  Stage 1 - Raw Absorption:        {counts.get('raw_absorption_candidates', 0):>10,} events")
    print(f"  Stage 2 - Reclaim:               {counts.get('reclaim_candidates', 0):>10,} events")
    print(f"  Stage 3 - Regime Passed:         {counts.get('regime_passed_candidates', 0):>10,} events")
    print(f"  Stage 4 - FollowThrough Passed:  {counts.get('followthrough_passed_candidates', 0):>10,} events")
    print(f"  Stage 5 - Final Alerts:          {counts.get('final_alerts', 0):>10,} events")
    
    # Conversion rates
    conversions = metrics.get("conversion_rates_percent", {})
    print("\n" + "-"*80)
    print("📊 CONVERSION RATES (% passing each gate)")
    print("-"*80)
    
    print(f"  Raw → Reclaim:                   {conversions.get('raw_to_reclaim', 0):>6.2f}%")
    print(f"  Reclaim → Regime:                {conversions.get('reclaim_to_regime', 0):>6.2f}%")
    print(f"  Regime → FollowThrough:          {conversions.get('regime_to_followthrough', 0):>6.2f}%")
    print(f"  FollowThrough → Final:           {conversions.get('followthrough_to_final', 0):>6.2f}%")
    print(f"  Overall (Raw → Final):           {conversions.get('overall', 0):>6.2f}%")
    
    # Funnel visualization
    print("\n" + "-"*80)
    print("🔗 FUNNEL FLOW")
    print("-"*80)
    
    funnel = metrics.get("funnel_visualization", [])
    for line in funnel:
        print(f"  {line}")
    
    # Bottleneck analysis
    bottleneck = metrics.get("bottleneck_analysis", {})
    print("\n" + "-"*80)
    print("🚨 BOTTLENECK ANALYSIS")
    print("-"*80)
    
    most_restrictive = bottleneck.get("most_restrictive_gate", "N/A")
    filtering_pct = bottleneck.get("filtering_out_percent", 0)
    description = bottleneck.get("description", "N/A")
    
    print(f"  Most Restrictive Gate: {most_restrictive}")
    print(f"  Filtering Out:         {filtering_pct:.1f}% of candidates")
    print(f"  Summary:               {description}")
    
    print("\n" + "="*80 + "\n")


def monitor_live():
    """Continuously monitor and display funnel metrics."""
    print("[MONITOR] Starting pipeline funnel monitor...")
    print(f"[CONFIG] Reading metrics from: {METRICS_FILE}")
    print("[WAITING] Waiting for pipeline_metrics.json to be created...")
    
    last_display = None
    display_interval = 2  # Display every 2 seconds
    last_display_time = time.time()
    
    while True:
        try:
            metrics = read_metrics()
            
            current_time = time.time()
            if metrics and (current_time - last_display_time >= display_interval):
                # Clear screen and display
                if sys.platform != "win32":
                    os.system("clear")
                else:
                    os.system("cls")
                
                print_funnel_report(metrics)
                last_display = metrics
                last_display_time = current_time
            elif not metrics:
                print("[WAITING] No metrics file yet. Retrying in 1 second...", end="\r")
            
            time.sleep(0.5)
        
        except KeyboardInterrupt:
            print("\n[STOP] Monitor stopped")
            break
        except Exception as e:
            print(f"[ERROR] {e}")
            time.sleep(1)


if __name__ == "__main__":
    import os
    
    if len(sys.argv) > 1 and sys.argv[1] == "--once":
        # Single read mode
        metrics = read_metrics()
        print_funnel_report(metrics)
    else:
        # Live monitoring mode
        monitor_live()
