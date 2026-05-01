# Woolies Price Tracker

Track Woolworths grocery prices over time. Compares current prices against your invoice baseline, shows deals sorted by biggest savings.

## Live Dashboard

[wooliespricetracker.netlify.app](https://wooliespricetracker.netlify.app)

## Structure

```
├── site/                      # Netlify deploy directory (dashboard)
│   ├── index.html             # Price tracker dashboard
│   └── golden_retriever_pixel.png
├── scrape_prices_direct.py    # Main scraper (direct API)
├── scrape_prices.py           # Legacy scraper (MCP/browser)
├── price_tracker_helpers.py   # Shared helpers
├── mcp_server.py              # MCP server for browser-based auth
├── invoice_baseline.py        # Invoice PDF parser
├── price_history.json         # Time-series price data
├── prices.csv                 # Current price snapshot
├── woolies_items.json         # Tracked items list
└── netlify.toml               # Netlify deploy config
```

## Setup

1. Clone and `cd wooliespricetracker`
2. Run `python3 scrape_prices_direct.py` (requires Woolworths cookies)
3. Open `site/index.html` to view the dashboard

## Deploy

Push to `main` — Netlify auto-deploys the `site/` directory.
