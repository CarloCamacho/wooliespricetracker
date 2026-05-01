#!/usr/bin/env python3
"""
Invoice Baseline Analysis for Woolworths Price Tracker.

Provides functions to load and analyze invoice baseline data for trend analysis.
"""

import json
from pathlib import Path
from datetime import datetime
from collections import defaultdict
import statistics

INVOICE_BASELINE_FILE = Path(__file__).parent / "invoice_baseline.json"
ENHANCED_ITEMS_FILE = Path(__file__).parent / "woolies_items_enhanced.json"


def load_invoice_baseline():
    """Load the invoice baseline data."""
    if not INVOICE_BASELINE_FILE.exists():
        return []
    with open(INVOICE_BASELINE_FILE, 'r') as f:
        return json.load(f)


def build_item_timeline(invoices):
    """Build a timeline of prices for each item across invoices."""
    timeline = defaultdict(list)
    for invoice in invoices:
        date = invoice['date']
        for item in invoice['items']:
            name = item['name'].strip()
            if name:
                timeline[name].append({
                    'date': date,
                    'price': item['price'],
                    'quantity': item['quantity'],
                    'supplied': item.get('supplied', item['quantity']),
                    'amount': item.get('amount', item['price'] * item['quantity'])
                })
    return timeline


def get_item_baseline(item_name, timeline):
    """Get baseline price info for an item."""
    # Find best match in timeline
    matches = [(name, data) for name, data in timeline.items() 
               if item_name.lower() in name.lower() or name.lower() in item_name.lower()]
    
    if not matches:
        return None
    
    name, data = matches[0]
    sorted_data = sorted(data, key=lambda x: x['date'])
    
    return {
        'invoice_item_name': name,
        'baseline_price': sorted_data[0]['price'],
        'baseline_date': sorted_data[0]['date'],
        'invoices_tracked': len(sorted_data),
        'price_range': (min(d['price'] for d in sorted_data), 
                       max(d['price'] for d in sorted_data)) if len(sorted_data) > 1 else None,
        'avg_price': statistics.mean(d['price'] for d in sorted_data) if len(sorted_data) > 1 else sorted_data[0]['price'],
        'timeline': sorted_data
    }


def analyze_trend(current_price, baseline_info):
    """Analyze price trend compared to baseline."""
    if not baseline_info:
        return None
    
    baseline_price = baseline_info['baseline_price']
    change = current_price - baseline_price
    pct_change = (change / baseline_price * 100) if baseline_price else 0
    
    if pct_change > 5:
        return f"↑ +{pct_change:.1f}% (above baseline)"
    elif pct_change < -5:
        return f"↓ {pct_change:.1f}% (below baseline - deal!)"
    else:
        return f"≈ {pct_change:+.1f}% (near baseline)"


def run_invoice_analysis():
    """Run a full analysis of invoice baselines."""
    invoices = load_invoice_baseline()
    timeline = build_item_timeline(invoices)
    
    # Find items appearing in multiple invoices
    multi_invoice = [(name, len(data)) for name, data in timeline.items() if len(data) > 1]
    multi_invoice.sort(key=lambda x: -x[1])
    
    print("\n" + "=" * 70)
    print("INVOICE BASELINE ANALYSIS")
    print("=" * 70)
    
    print(f"\nTotal invoices: {len(invoices)}")
    print(f"Date range: {invoices[0]['date']} to {invoices[-1]['date']}")
    print(f"Items tracked: {len(timeline)}")
    print(f"Items with multiple invoices: {len(multi_invoice)}")
    
    print("\nTop items with multiple invoice records:")
    for name, count in multi_invoice[:10]:
        info = get_item_baseline(name, timeline)
        print(f"  • {name[:45]}: {count} invoices, ${info['baseline_price']:.2f} baseline")
    
    # Show trends
    print("\nPrice trends (vs first invoice):")
    for name, count in multi_invoice[:10]:
        info = get_item_baseline(name, timeline)
        timeline_sorted = info['timeline']
        if len(timeline_sorted) > 1:
            first = timeline_sorted[0]['price']
            last = timeline_sorted[-1]['price']
            change = last - first
            pct = (change / first * 100) if first else 0
            trend = "↑" if change > 0 else "↓" if change < 0 else "="
            print(f"  {trend} {name[:40]}: ${first:.2f} → ${last:.2f} ({pct:+.1f}%)")


if __name__ == "__main__":
    run_invoice_analysis()