#!/usr/bin/env python3
"""
Helper functions for Woolworths Price Tracker - unfound item tracking.
"""

import json
from pathlib import Path
from datetime import datetime

UNFOUND_ITEMS_FILE = Path(__file__).parent / "unfound_items.json"
PRICE_HISTORY = Path(__file__).parent / "price_history.json"


def track_unfound_items(results):
    """Track items that couldn't be found and report on them."""
    unfound = [r for r in results if r['status'] != 'found']
    
    if not unfound:
        return
    
    # Load existing unfound items
    if UNFOUND_ITEMS_FILE.exists():
        with open(UNFOUND_ITEMS_FILE, 'r') as f:
            unfound_history = json.load(f)
    else:
        unfound_history = {}
    
    date_str = datetime.now().strftime('%Y-%m-%d')
    
    # Update history
    for item in unfound:
        name = item['item']
        if name not in unfound_history:
            unfound_history[name] = {
                'first_unfound': date_str,
                'times_unfound': 0,
                'last_unfound': date_str,
                'notes': 'May be location-specific (WA vs eastern states)'
            }
        unfound_history[name]['times_unfound'] += 1
        unfound_history[name]['last_unfound'] = date_str
    
    with open(UNFOUND_ITEMS_FILE, 'w') as f:
        json.dump(unfound_history, f, indent=2)
    
    # Print summary
    print(f"\n⚠️  Items not found (may be WA location-specific):")
    for item in unfound:
        times = unfound_history.get(item['item'], {}).get('times_unfound', 1)
        print(f"  • {item['item'][:50]} (unfound {times} time(s))")


def get_unfound_report():
    """Get a summary report of unfound items."""
    if not UNFOUND_ITEMS_FILE.exists():
        return "No unfound items tracked yet."
    
    with open(UNFOUND_ITEMS_FILE, 'r') as f:
        unfound_history = json.load(f)
    
    if not unfound_history:
        return "All items found in last run!"
    
    report = "Items frequently not found (may need location adjustment):\n"
    for name, data in sorted(unfound_history.items(), key=lambda x: -x[1]['times_unfound']):
        report += f"  • {name[:50]}: {data['times_unfound']} time(s)\n"
    return report