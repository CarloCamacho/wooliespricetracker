#!/usr/bin/env python3
"""
Woolworths Price Scraper - two-phase approach.
Phase 1: Opens browser and waits for you to log in (create a signal file)
Phase 2: After signal, scrapes all items and writes to local JSON/CSV.

When logged in, run:
  touch /tmp/woolies_logged_in

Or run both at once with:
  python3 scrape_prices.py
"""

import json
import subprocess
import time
import sys
import os
import pty
import urllib.request
import urllib.error
import urllib.parse
from pathlib import Path
from datetime import datetime

WOOLIES_SERVER = "/Users/ianf/workspace/meal-prep/mcp-servers/woolworths-server/dist/index.js"
COOKIE_FILE = Path.home() / ".hermes" / "woolworths_cookies.json"
READY_FILE = Path("/tmp/woolies_logged_in")
PRICES_CSV = Path(__file__).parent / "prices.csv"
PRICE_HISTORY = Path(__file__).parent / "price_history.json"
ITEMS_FILE = Path(__file__).parent / "woolies_items.json"
# Chrome profile for passkey support - set to your Chrome profile path
# Default Chrome: ~/Library/Application Support/Google/Chrome/Default
# Chrome Canary: ~/Library/Application Support/Google/Chrome Canary/Default
CHROME_USER_DATA_DIR = Path.home() / "Library/Application Support/Google/Chrome/Default"
UNFOUND_ITEMS_FILE = Path(__file__).parent / "unfound_items.json"


def load_items():
    """Load items from JSON config, filtering by track=true."""
    with open(ITEMS_FILE, "r") as f:
        items = json.load(f)
    # Return tuple list (name, qty, price) for items marked track=true
    return [(i["item"], i["quantity"], i["invoice_price"]) for i in items if i.get("track", True)]


ITEMS = load_items()


class MCPClient:
    def __init__(self, command):
        self.command = command
        self.proc = None
        self._req_id = 0

    def start(self):
        # Use PTY to force Node.js stdout line-buffering
        # (Node block-buffers when stdout is a pipe, causing handshake deadlock)
        import tty
        master_fd, slave_fd = pty.openpty()
        tty.setraw(master_fd)  # Disable PTY echo/line processing
        self.proc = subprocess.Popen(
            self.command,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=subprocess.DEVNULL,
            text=True,
            close_fds=True,
        )
        os.close(slave_fd)
        self.stdout = os.fdopen(master_fd, "r", buffering=1)
        # stdin needs to be writable through the master FD
        self.stdin_fd = master_fd
        # MCP init
        self._send("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "woolies-scraper", "version": "1.0"},
        })
        self._send("notifications/initialized", {})

    def call_tool(self, name, args=None):
        resp = self._send("tools/call", {
            "name": name,
            "arguments": args or {},
        })
        if "error" in resp:
            return {"success": False, "error": resp["error"].get("message", "Unknown")}
        if "result" in resp:
            return {"success": True, "data": resp["result"]}
        return resp

    def _send(self, method, params=None):
        self._req_id += 1
        request = {"jsonrpc": "2.0", "id": self._req_id, "method": method, "params": params or {}}
        line = json.dumps(request) + "\n"
        os.write(self.stdin_fd, line.encode())
        os.fsync(self.stdin_fd)

        while True:
            line = self.stdout.readline()
            if not line:
                return {"error": "No response"}
            try:
                data = json.loads(line.strip())
                if data.get("id") == self._req_id:
                    return data
            except json.JSONDecodeError:
                continue

    def close(self):
        if self.proc:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=5)
            except Exception:
                self.proc.kill()


def extract_price(product):
    # Try Price field first (direct number on inner product)
    price = product.get("Price")
    if price is not None and isinstance(price, (int, float)):
        return price
    # Try WasPrice as fallback (for out-of-stock items where Price is null)
    was = product.get("WasPrice")
    if was is not None and isinstance(was, (int, float)):
        return was
    # Try CupPrice (per-unit price, like $0.40/EA)
    cap = product.get("CupPrice")
    if cap is not None:
        if isinstance(cap, dict):
            return cap.get("Price", cap.get("Amount"))
        if isinstance(cap, (int, float)):
            return cap
    return None


def scrape_all_items(mcp):
    print("\n[2/4] Searching for products...\n")
    results = []

    for i, (item, qty, inv_price) in enumerate(ITEMS, 1):
        print(f"  [{i}/{len(ITEMS)}] {item}")
        resp = mcp.call_tool("woolworths_search_products", {
            "searchTerm": item,
            "pageNumber": 1,
            "pageSize": 3,
        })

        if resp.get("success"):
            content = resp["data"].get("content", [])
            found = False
            for block in content:
                if block.get("type") == "text":
                    try:
                        data = json.loads(block["text"])
                        # Woolworths API wraps products: [{Products: [actual], Name, DisplayName}, ...]
                        products_wrapper = data.get("Products") or data.get("products", [])
                        if products_wrapper:
                            wrapper = products_wrapper[0]
                            # Unwrap nested Products — the real product data is inside
                            inner_products = wrapper.get("Products", [])
                            if inner_products:
                                product = inner_products[0]
                            else:
                                # Fallback: wrapper itself might be the product (old format)
                                product = wrapper
                        else:
                            product = None

                        if product:
                            name = product.get("Name", product.get("name", item))
                            price = extract_price(product)
                            stockcode = product.get("Stockcode", product.get("Id", ""))
                            print(f"    OK {name[:55]} — ${price}")
                            results.append({
                                "item": item, "quantity": qty, "invoice_price": inv_price,
                                "current_price": price, "product_name": name,
                                "stockcode": stockcode, "status": "found",
                            })
                            found = True
                            break
                    except json.JSONDecodeError:
                        pass

            if not found:
                print(f"    X No results")
                results.append({
                    "item": item, "quantity": qty, "invoice_price": inv_price,
                    "current_price": None, "status": "not found",
                    "product_name": None, "stockcode": None,
                })
        else:
            print(f"    X Error: {resp.get('error')}")
            results.append({
                "item": item, "quantity": qty, "invoice_price": inv_price,
                "current_price": None, "status": resp.get("error"),
                "product_name": None, "stockcode": None,
            })
        time.sleep(1)

    return results


def write_price_history(results):
    """Append to price history JSON file with timestamp."""
    if PRICE_HISTORY.exists():
        with open(PRICE_HISTORY, "r") as f:
            history = json.load(f)
    else:
        history = []
    
    snapshot = {
        "date": datetime.now().isoformat(),
        "items": results
    }
    history.append(snapshot)
    
    with open(PRICE_HISTORY, "w") as f:
        json.dump(history, f, indent=2)
    
    print(f"  OK Price history updated: {PRICE_HISTORY}")


def write_csv_file(results):
    with open(PRICES_CSV, "w") as f:
        f.write("Date,Item,Quantity,Invoice Price,Current Price,Product Name,Stockcode,Status\n")
        for r in results:
            f.write(f"{datetime.now().strftime('%Y-%m-%d')},{r['item']},{r['quantity']},")
            f.write(f"{r['invoice_price']},{r.get('current_price') or ''},")
            f.write(f"{r.get('product_name') or ''},{r.get('stockcode') or ''},{r['status']}\n")
    print(f"  CSV: {PRICES_CSV}")


def print_summary(results):
    print("\n" + "=" * 70)
    print("Summary")
    print("=" * 70)
    found = [r for r in results if r["status"] == "found"]
    print(f"Items found: {len(found)}/{len(results)}")

    if found:
        total_inv = sum(r["invoice_price"] * r["quantity"] for r in found)
        total_now = sum((r["current_price"] or 0) * r["quantity"] for r in found)
        diff = total_now - total_inv
        pct = (diff / total_inv * 100) if total_inv else 0
        symbol = "+" if diff > 0 else ""
        print(f"Total invoice (found items): ${total_inv:.2f}")
        print(f"Total current prices:        ${total_now:.2f}")
        print(f"Difference: {symbol}${diff:.2f} ({symbol}{pct:.1f}%)")

    print("\nPrice changes:")
    for r in results:
        cp = r.get("current_price")
        if cp is not None:
            try:
                diff = float(cp) - r["invoice_price"]
                if diff > 0.005:
                    arrow = "UP"
                elif diff < -0.005:
                    arrow = "DOWN"
                else:
                    arrow = "="
                print(f"  {arrow} ${r['invoice_price']:.2f} -> ${cp}  {r['item'][:50]}")
            except (ValueError, TypeError):
                print(f"  ? ${r['invoice_price']:.2f} -> {cp}  {r['item'][:50]}")
        else:
            print(f"  X not found   {r['item'][:50]}")


def analyze_price_trend(item_name, current_price, history):
    """Analyze price trend for an item from history."""
    trends = []
    
    # Get all past prices for this item
    past_prices = []
    for snapshot in history:
        for item in snapshot.get("items", []):
            if item.get("item") == item_name and item.get("current_price"):
                try:
                    past_prices.append((snapshot.get("date"), float(item["current_price"])))
                except (ValueError, TypeError):
                    pass
    
    if len(past_prices) < 2:
        return ""
    
    # Sort by date (newest first)
    past_prices.sort(key=lambda x: x[0], reverse=True)
    
    # Check if current is new high/low
    prices_only = [p[1] for p in past_prices]
    if current_price >= max(prices_only):
        trends.append("NEW HIGH")
    elif current_price <= min(prices_only):
        trends.append("NEW LOW")
    
    # Check recent trend (last 3 runs)
    if len(prices_only) >= 3:
        recent = prices_only[:3]
        if all(recent[i] <= recent[i+1] for i in range(len(recent)-1)):
            trends.append("up 3 runs")
        elif all(recent[i] >= recent[i+1] for i in range(len(recent)-1)):
            trends.append("down 3 runs")
    
    return f" [{', '.join(trends)}]" if trends else ""


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
    
    # Update history - items not found are likely WA location-specific
    for item in unfound:
        name = item['item']
        if name not in unfound_history:
            unfound_history[name] = {
                'first_unfound': date_str,
                'times_unfound': 0,
                'last_unfound': date_str,
                'notes': 'May be location-specific (WA vs eastern states) - prices shown are from eastern states'
            }
        unfound_history[name]['times_unfound'] += 1
        unfound_history[name]['last_unfound'] = date_str
    
    with open(UNFOUND_ITEMS_FILE, 'w') as f:
        json.dump(unfound_history, f, indent=2)
    
    # Print summary
    print(f"\nWARNING  Items not found (likely WA location-specific - using eastern state prices):")
    for item in unfound:
        times = unfound_history.get(item['item'], {}).get('times_unfound', 1)
        print(f"  - {item['item'][:50]} (unfound {times} time(s))")


def send_email_summary(results, config_path=None):
    """Send email summary via AgentMail if configured.
    Only sends if at least 50% of items were found to avoid sending
    emails with mostly "not found" results.
    """
    # Check success rate - only email if >= 50% found
    found = [r for r in results if r.get("status") == "found"]
    success_rate = len(found) / len(results) if results else 0
    
    if success_rate < 0.5:
        print(f"  [SKIP] Email skipped - only {success_rate*100:.0f}% items found (< 50% threshold)")
        return
    
    if config_path is None:
        config_path = Path(__file__).parent / "config.json"
    
    if not config_path.exists():
        return
    
    try:
        with open(config_path, "r") as f:
            full_config = json.load(f)
        
        email_config = full_config.get("email", {})
        agentmail_config = full_config.get("agentmail", {})
        
        # Need agentmail method with api_key
        if email_config.get("method") != "agentmail" or not agentmail_config.get("api_key"):
            return
        
        # Merge configs - email for to/recipients, agentmail for api_key/from/inbox
        config = {**email_config, **agentmail_config}
        
        # Load price history for trend analysis
        if PRICE_HISTORY.exists():
            with open(PRICE_HISTORY, "r") as f:
                price_history = json.load(f)
        else:
            price_history = []
        
        # Build email content
        found = [r for r in results if r["status"] == "found"]
        unfound = [r for r in results if r["status"] != "found"]
        total_inv = sum(r["invoice_price"] * r["quantity"] for r in found)
        total_now = sum((r["current_price"] or 0) * r["quantity"] for r in found)
        diff = total_now - total_inv
        pct = (diff / total_inv * 100) if total_inv else 0
        symbol = "+" if diff > 0 else ""
        
        lines = [
            "<html><body>",
            "<h2>Woolworths Price Tracker Summary</h2>",
            f"<p><strong>Date:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M')}</p>",
            f"<p><strong>Items tracked:</strong> {len(results)}</p>",
            f"<p><strong>Found:</strong> {len(found)}</p>",
            f"<p><strong>Not found:</strong> {len(unfound)} (WA location-specific)</p>",
            f"<p><strong>Total invoice:</strong> ${total_inv:.2f}</p>",
            f"<p><strong>Total current:</strong> ${total_now:.2f}</p>",
            f"<p><strong>Difference:</strong> {symbol}${diff:.2f} ({symbol}{pct:.1f}%)</p>",
            "<h3>Price Changes:</h3>",
            "<ul>",
        ]
        
        for r in results:
            cp = r.get("current_price")
            if cp is not None:
                try:
                    price_diff = float(cp) - r["invoice_price"]
                    if abs(price_diff) > 0.005:
                        arrow = "UP" if price_diff > 0 else "DOWN"
                        trend = analyze_price_trend(r["item"], float(cp), price_history)
                        lines.append(f"<li>{arrow} ${r['invoice_price']:.2f} -> ${cp}  {r['item']}{trend}</li>")
                except (ValueError, TypeError):
                    pass
            else:
                lines.append(f"<li>X {r['item']} (not found - WA location)</li>")
        
        lines.extend(["</ul>", "</body></html>"])
        html_body = "\n".join(lines)
        text_body = f"""Woolworths Price Tracker Summary
Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}

Items tracked: {len(results)}
Found: {len(found)}
Not found: {len(unfound)} (WA location-specific)
Total invoice: ${total_inv:.2f}
Total current: ${total_now:.2f}
Difference: {symbol}${diff:.2f} ({symbol}{pct:.1f}%)

Price Changes:"""
        
        for r in results:
            cp = r.get("current_price")
            if cp is not None:
                try:
                    price_diff = float(cp) - r["invoice_price"]
                    if abs(price_diff) > 0.005:
                        arrow = "UP" if price_diff > 0 else "DOWN"
                        trend = analyze_price_trend(r["item"], float(cp), price_history)
                        text_body += f"\n  {arrow} ${r['invoice_price']:.2f} -> ${cp}  {r['item']}{trend}"
                except (ValueError, TypeError):
                    pass
            else:
                text_body += f"\n  X {r['item']} (not found - WA location)"
        
        # Send via AgentMail API
        req_data = json.dumps({
            "to": config.get("to") or config.get("recipients", ["ian@example.com"])[0],
            "subject": f"Woolworths Price Tracker - {datetime.now().strftime('%Y-%m-%d')}",
            "text": text_body,
            "html": html_body
        }).encode()
        
        req = urllib.request.Request(
            f"https://api.agentmail.to/v0/inboxes/{urllib.parse.quote(config['inbox_id'], safe='@')}/messages/send",
            data=req_data,
            headers={
                "Authorization": f"Bearer {config['api_key']}",
                "Content-Type": "application/json"
            },
            method="POST"
        )
        
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status == 200:
                print(f"  OK Email summary sent to {config.get('to') or config.get('from_address')}")
    except Exception as e:
        print(f"  [WARN] Could not send email: {e}")


def main():
    if READY_FILE.exists():
        READY_FILE.unlink()

    print("=" * 60)
    print("Woolworths Price Tracker")
    print("=" * 60)

    mcp = MCPClient(["node", WOOLIES_SERVER])
    mcp.start()

    try:
        # Check for cached cookies first
        cookies_loaded = False
        if COOKIE_FILE.exists():
            with open(COOKIE_FILE, "r") as f:
                cached_cookies = json.load(f)
            if cached_cookies:
                print("\n[1/3] Loading cached cookies...")
                resp = mcp.call_tool("woolworths_set_cookies", {"cookies": cached_cookies})
                if resp.get("success"):
                    print(f"  OK Loaded {len(cached_cookies)} cached cookies from {COOKIE_FILE}")
                    cookies_loaded = True
                else:
                    print(f"  [WARN] Failed to load cached cookies: {resp.get('error')}")

        # If no cookies, open browser for login with passkey support
        if not cookies_loaded:
            print("\n[1/4] Opening Woolworths browser (with passkey support)...")
            resp = mcp.call_tool("woolworths_open_browser", {
                "headless": False,
                "userDataDir": str(CHROME_USER_DATA_DIR)
            })
            if not resp.get("success"):
                print(f"[ERROR] {resp.get('error')}")
                return
            print("Browser opened using your Chrome profile (extensions/passkeys available).")
            print("Log in to your Woolworths account.")

            print("\n[2/4] Waiting for you to log in...")
            print("  When done, run: touch /tmp/woolies_logged_in")
            print("  Or: python3 scrape_prices.py --continue")

            timeout_seconds = 300
            waited = 0
            while not READY_FILE.exists():
                time.sleep(2)
                waited += 2
                if waited >= timeout_seconds:
                    print("\n[WARN] Timeout after 5 minutes. Check browser manually.")
                    break
                if waited % 20 == 0:
                    print(f"  Still waiting... ({timeout_seconds - waited}s remaining)")

            if not READY_FILE.exists():
                print("[ERROR] Signal file not found. Aborting.")
                return

            print("\n  OK Login signal received. Scraping prices...")

            print("\n[3/4] Capturing session cookies...")
            resp = mcp.call_tool("woolworths_get_cookies")
            if resp.get("success"):
                content = resp["data"].get("content", [])
                for block in content:
                    if block.get("type") == "text":
                        try:
                            cookie_data = json.loads(block["text"])
                            with open(COOKIE_FILE, "w") as f:
                                json.dump(cookie_data, f)
                            print(f"  OK Cached {len(cookie_data)} cookies -> {COOKIE_FILE}")
                        except json.JSONDecodeError:
                            pass

        results = scrape_all_items(mcp)

        print("\n[4/4] Saving results...")
        write_price_history(results)
        write_csv_file(results)
        print_summary(results)
        track_unfound_items(results)
        
        # Send email summary if configured
        send_email_summary(results)

    finally:
        print("\nClosing browser...")
        mcp.call_tool("woolworths_close_browser")
        mcp.close()


if __name__ == "__main__":
    main()