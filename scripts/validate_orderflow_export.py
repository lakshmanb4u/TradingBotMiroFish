"""
Orderflow export validation tool

Checks:
- Schema compliance
- Replay safety
- Data quality
"""
import argparse
from pathlib import Path
from services.orderflow.bookmap_csv_adapter import BookmapAdapter
import json

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', required=True, help='Input export file')
    parser.add_argument('--output', help='Optional audit output path')
    args = parser.parse_args()
    
    adapter = BookmapAdapter('.')
    
    try:
        df = adapter.load_file(Path(args.input))
        audit = adapter.generate_audit()
        
        print(f"Validation complete for {args.input}")
        print("Schema status:")
        print(json.dumps(audit['schema_status'], indent=2))
        print("\nChecks:")
        for check in audit['checks']:
            print(f"- {check}")
        
        if args.output:
            with open(args.output, 'w') as f:
                json.dump(audit, f)
            print(f"\nAudit saved to {args.output}")
        
    except Exception as e:
        print(f"Validation failed: {str(e)}")

if __name__ == '__main__':
    main()