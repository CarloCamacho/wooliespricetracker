#!/usr/bin/env python3
"""
Woolworths Price Scraper — Direct API
Connects directly to Woolworths search API using cached cookies.
No browser, no MCP server, no login required.
Cookies are cached from a one-time extraction via Chrome's cookie DB.

Usage: python3 scrape_prices_direct.py
"""

import json, time, urllib.request, urllib.parse
from pathlib import Path
from datetime import datetime

# Paths
BASE = Path(__file__).parent
ITEMS_FILE = BASE / "woolies_items.json"
COOKIE_FILE = Path.home() / ".hermes" / "woolworths_cookies.json"
PRICE_HISTORY = BASE / "price_history.json"
PRICES_CSV = BASE / "prices.csv"
UNFOUND_FILE = BASE / "unfound_items.json"
CONFIG_FILE = BASE / "config.json"

# Woolworths API
API_URL = "https://www.woolworths.com.au/apis/ui/Search/products"


def load_items():
    with open(ITEMS_FILE) as f:
        items = json.load(f)
    return [(i["item"], i["quantity"], i["invoice_price"]) for i in items if i.get("track", True)]


def load_cookies():
    with open(COOKIE_FILE) as f:
        cookies = json.load(f)
    return "; ".join(f"{c['name']}={c['value']}" for c in cookies)


def search_product(search_term, cookie_str):
    """Search Woolworths API for a product. Returns (product_dict, None) or (None, error_msg)."""
    body = json.dumps({
        "searchTerm": search_term,
        "pageNumber": 1,
        "pageSize": 3,
        "sortType": "TraderRelevance",
        "location": f"/shop/search/products?searchTerm={urllib.parse.quote(search_term)}",
        "formatObject": json.dumps({"name": search_term}),
        "isSpecial": False,
        "isBundle": False,
        "isMobile": False,
        "filters": [],
        "groupEdmVariants": False,
    }).encode()

    req = urllib.request.Request(API_URL, data=body, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Content-Type": "application/json",
        "Cookie": cookie_str,
        "Accept": "*/*",
        "Origin": "https://www.woolworths.com.au",
        "Referer": "https://www.woolworths.com.au/",
    }, method="POST")

    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())

    # Woolworths wraps products: [{Products: [actual], Name, DisplayName}, ...]
    products_wrapper = data.get("Products", [])
    if not products_wrapper:
        return None, "no results"

    wrapper = products_wrapper[0]
    inner = wrapper.get("Products", [])
    if not inner:
        return None, "empty inner products"

    return inner[0], None  # Return the unwrapped product dict


def extract_price(product):
    """Extract price from product dict. Tries Price, then WasPrice, then CupPrice."""
    price = product.get("Price")
    if price is not None and isinstance(price, (int, float)):
        return price
    was = product.get("WasPrice")
    if was is not None and isinstance(was, (int, float)):
        return was
    cap = product.get("CupPrice")
    if cap is not None:
        if isinstance(cap, (int, float)):
            return cap
        if isinstance(cap, dict):
            return cap.get("Price", cap.get("Amount"))
    return None


def scrape_all(cookie_str):
    items = load_items()
    print(f"\n[1/3] Scraping {len(items)} items...\n")

    results = []
    for idx, (search_term, qty, inv_price) in enumerate(items, 1):
        try:
            product, error = search_product(search_term, cookie_str)
        except Exception as e:
            print(f"  [{idx}/{len(items)}] X API error: {search_term[:50]} — {e}")
            results.append({
                "item": search_term, "quantity": qty, "invoice_price": inv_price,
                "current_price": None, "status": f"api_error", "product_name": None, "stockcode": None,
            })
            time.sleep(1)
            continue

        if error:
            print(f"  [{idx}/{len(items)}] X {error}: {search_term[:50]}")
            results.append({
                "item": search_term, "quantity": qty, "invoice_price": inv_price,
                "current_price": None, "status": error.replace(" ", "_"), "product_name": None, "stockcode": None,
            })
            time.sleep(0.5)
            continue

        name = product.get("DisplayName") or product.get("Name", "")
        price = extract_price(product)
        stockcode = str(product.get("Stockcode", ""))
        was_price = product.get("WasPrice")
        is_special = product.get("IsOnSpecial", False) or product.get("IsHalfPrice", False)

        tag = ""
        if product.get("IsHalfPrice"):
            tag = " [HALF PRICE]"
        elif product.get("IsOnSpecial"):
            tag = " [SPECIAL]"

        status = "found"
        if price is None:
            status = "no_price"
            tag += " (no price)"

        print(f"  [{idx}/{len(items)}] {name[:55]} — ${price}{tag}")

        results.append({
            "item": search_term, "quantity": qty, "invoice_price": inv_price,
            "current_price": price, "product_name": name,
            "stockcode": stockcode, "status": status,
            "was_price": was_price, "is_special": is_special,
        })
        time.sleep(0.5)

    return results


def save_results(results):
    now = datetime.now()
    found = [r for r in results if r["status"] == "found"]

    # Price history (append snapshot)
    if PRICE_HISTORY.exists():
        with open(PRICE_HISTORY) as f:
            history = json.load(f)
    else:
        history = []
    history.append({"date": now.isoformat(), "items": results})
    with open(PRICE_HISTORY, "w") as f:
        json.dump(history, f, indent=2)

    # CSV snapshot
    with open(PRICES_CSV, "w") as f:
        f.write("Date,Item,Quantity,Invoice Price,Current Price,Product Name,Stockcode,Status\n")
        for r in results:
            f.write(f"{now.strftime('%Y-%m-%d')},{r['item']},{r['quantity']},")
            f.write(f"{r['invoice_price']},{r.get('current_price') or ''},")
            f.write(f"{r.get('product_name') or ''},{r.get('stockcode') or ''},{r['status']}\n")

    # Unfound tracking
    unfound = [r for r in results if r['status'] != 'found']
    if unfound:
        if UNFOUND_FILE.exists():
            with open(UNFOUND_FILE) as f:
                uhist = json.load(f)
        else:
            uhist = {}
        date_str = now.strftime('%Y-%m-%d')
        for item in unfound:
            name = item['item']
            if name not in uhist:
                uhist[name] = {'first_unfound': date_str, 'times_unfound': 0, 'last_unfound': date_str}
            uhist[name]['times_unfound'] += 1
            uhist[name]['last_unfound'] = date_str
        with open(UNFOUND_FILE, 'w') as f:
            json.dump(uhist, f, indent=2)

    return found


def print_summary(results):
    found = [r for r in results if r["status"] == "found"]

    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    print(f"Items found: {len(found)}/{len(results)}")

    if found:
        total_inv = sum(r["invoice_price"] * r["quantity"] for r in found)
        total_now = sum((r["current_price"] or 0) * r["quantity"] for r in found)
        diff = total_now - total_inv
        pct = (diff / total_inv * 100) if total_inv else 0
        symbol = "+" if diff > 0 else ""
        print(f"Total invoice (found): ${total_inv:.2f}")
        print(f"Total current:         ${total_now:.2f}")
        print(f"Difference: {symbol}${diff:.2f} ({symbol}{pct:.1f}%)")

    print(f"\nPrice changes vs invoice:")
    for r in results:
        cp = r.get("current_price")
        if cp is not None:
            try:
                d = float(cp) - r["invoice_price"]
                if d > 0.005:
                    arrow = "UP  "
                elif d < -0.005:
                    arrow = "DOWN"
                else:
                    arrow = "=   "
                sale = " *** SALE ***" if r.get("is_special") else ""
                print(f"  {arrow} ${r['invoice_price']:.2f} → ${cp:.2f}  {r['item'][:50]}{sale}")
            except (ValueError, TypeError):
                print(f"  ?   ${r['invoice_price']:.2f} → {cp}  {r['item'][:50]}")
        else:
            print(f"  X   not found   {r['item'][:50]}")

    not_found = [r for r in results if r["status"] != "found"]
    if not_found:
        print(f"\nNOT FOUND ({len(not_found)} items):")
        for r in not_found:
            print(f"  - {r['item'][:60]}")


def send_email(results):
    """Send modern, well-designed email summary via AgentMail.
    Uses .format() exclusively — no f-strings with emoji (causes import errors)."""
    found = [r for r in results if r.get("status") == "found"]
    if not found:
        return
    success_rate = len(found) / len(results) if results else 0
    if success_rate < 0.5:
        print("  [SKIP] Email — only {:.0f}% found".format(success_rate * 100))
        return
    if not CONFIG_FILE.exists():
        return

    try:
        with open(CONFIG_FILE) as f:
            cfg = json.load(f)
        email_cfg = cfg.get("email", {})
        am_cfg = cfg.get("agentmail", {})
        if email_cfg.get("method") != "agentmail" or not am_cfg.get("api_key"):
            return

        merged = {**email_cfg, **am_cfg}
        now = datetime.now()

        total_inv = sum(r["invoice_price"] * r["quantity"] for r in found)
        total_now = sum((r["current_price"] or 0) * r["quantity"] for r in found)
        diff = total_now - total_inv
        pct = (diff / total_inv * 100) if total_inv else 0

        # Categorize
        downs, ups, same, deals = [], [], [], []
        for r in results:
            cp = r.get("current_price")
            if cp is None:
                continue
            try:
                d = float(cp) - r["invoice_price"]
            except (ValueError, TypeError):
                continue
            pct_chg = (d / r["invoice_price"] * 100) if r["invoice_price"] else 0
            name = r.get("product_name") or r["item"]
            is_sale = r.get("is_special")
            entry = {"name": name[:60], "inv": r["invoice_price"], "now": cp, "diff": d, "pct": pct_chg, "sale": is_sale}
            if d < -0.005:
                downs.append(entry)
            elif d > 0.005:
                ups.append(entry)
            else:
                same.append(entry)
            if is_sale:
                deals.append(entry)

        def price_row(e):
            if e["diff"] < -0.005:
                arrow, color, bg = "v", "#059669", "#ecfdf5"
            elif e["diff"] > 0.005:
                arrow, color, bg = "^", "#dc2626", "#fef2f2"
            else:
                arrow, color, bg = "~", "#6b7280", "#f9fafb"
            badge = '<span style="background:#fbbf24;color:#7c2d12;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600;margin-left:6px">SALE</span>' if e["sale"] else ""
            return '<tr style="background:{bg};border-bottom:1px solid #e5e7eb">' \
                   '<td style="padding:10px 14px;font-size:13px">{name}{badge}</td>' \
                   '<td style="padding:10px 14px;font-size:13px;text-align:right;color:#9ca3af;text-decoration:line-through">${inv:.2f}</td>' \
                   '<td style="padding:10px 14px;font-size:13px;text-align:right;font-weight:600;color:{color}">{arrow} ${now:.2f}</td>' \
                   '<td style="padding:10px 14px;font-size:12px;text-align:right;color:{color}">{pct:+.0f}%</td></tr>'.format(
                       bg=bg, name=e['name'], badge=badge, inv=e['inv'], color=color, arrow=arrow, now=e['now'], pct=e['pct'])

        rows_down = "\n".join(price_row(e) for e in downs)
        rows_up = "\n".join(price_row(e) for e in ups)

        # Deal cards — color by price direction: DOWN=green, SAME=amber, UP=red
        deal_cards = ""
        for e in deals[:8]:
            if e["diff"] < -0.005:
                deal_color, label = "#059669", "DEAL"
            elif e["diff"] > 0.005:
                deal_color, label = "#dc2626", "ON SPECIAL"
            else:
                deal_color, label = "#d97706", "SAME PRICE"
            deal_cards += '<div style="background:#fffbeb;border:1px solid #fcd34d;border-radius:8px;padding:12px 16px;margin:6px 0">' \
                          '<span style="font-weight:600;font-size:14px">[{label}] {name}</span><br>' \
                          '<span style="color:#9ca3af;text-decoration:line-through">${inv:.2f}</span>' \
                          '<span style="font-weight:700;color:{dc};margin-left:8px">${now:.2f}</span>' \
                          '<span style="color:{dc};font-size:12px;margin-left:4px">({pct:+.0f}%)</span></div>'.format(
                              label=label, name=e['name'], inv=e['inv'], dc=deal_color, now=e['now'], pct=e['pct'])

        deals_html = ""
        if deals:
            deals_html = '<tr><td style="padding:0 32px"><div style="margin:24px 0">' \
                         '<h2 style="font-size:16px;color:#1f2937;margin:0 0 12px 0">Deals &amp; Specials ({n} items)</h2>' \
                         '{cards}</div></td></tr>'.format(n=len(deals), cards=deal_cards)

        drops_html = ""
        if downs:
            drops_html = '<tr><td style="padding:0 32px 24px 32px">' \
                         '<h2 style="font-size:16px;color:#059669;margin:24px 0 12px 0">Price Drops ({n})</h2>' \
                         '<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse">' \
                         '<tr style="background:#f9fafb;border-bottom:2px solid #e5e7eb">' \
                         '<th style="padding:8px 14px;font-size:11px;text-align:left;text-transform:uppercase;color:#9ca3af">Item</th>' \
                         '<th style="padding:8px 14px;font-size:11px;text-align:right;text-transform:uppercase;color:#9ca3af">Was</th>' \
                         '<th style="padding:8px 14px;font-size:11px;text-align:right;text-transform:uppercase;color:#9ca3af">Now</th>' \
                         '<th style="padding:8px 14px;font-size:11px;text-align:right;text-transform:uppercase;color:#9ca3af">Chg</th></tr>' \
                         '{rows}</table></td></tr>'.format(n=len(downs), rows=rows_down)

        ups_html = ""
        if ups:
            ups_html = '<tr><td style="padding:0 32px 24px 32px">' \
                       '<h2 style="font-size:16px;color:#dc2626;margin:24px 0 12px 0">Price Increases ({n})</h2>' \
                       '<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse">' \
                       '<tr style="background:#f9fafb;border-bottom:2px solid #e5e7eb">' \
                       '<th style="padding:8px 14px;font-size:11px;text-align:left;text-transform:uppercase;color:#9ca3af">Item</th>' \
                       '<th style="padding:8px 14px;font-size:11px;text-align:right;text-transform:uppercase;color:#9ca3af">Was</th>' \
                       '<th style="padding:8px 14px;font-size:11px;text-align:right;text-transform:uppercase;color:#9ca3af">Now</th>' \
                       '<th style="padding:8px 14px;font-size:11px;text-align:right;text-transform:uppercase;color:#9ca3af">Chg</th></tr>' \
                       '{rows}</table></td></tr>'.format(n=len(ups), rows=rows_up)

        uncounted = len(results) - len(found)
        sign = '+' if diff > 0 else ''
        date_str = now.strftime('%A, %d %B %Y')
        ts_str = now.strftime('%Y-%m-%d %H:%M')

        html_body = """<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f3f4f6;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f3f4f6;padding:24px 0">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.1)">

<tr><td style="background:linear-gradient(135deg,#059669,#047857);padding:20px 32px;text-align:center">
  <div style="font-size:11px;text-transform:uppercase;letter-spacing:2px;color:#a7f3d0;margin-bottom:4px">Woolworths Price Tracker</div>
  <div style="font-size:13px;color:#d1fae5">{date}</div>
</td></tr>

<tr><td style="background:#fef3c7;padding:12px 32px;text-align:center;border-bottom:1px solid #fcd34d">
  <div style="font-size:13px;color:#92400e;font-weight:500">Current specials have been added to the Home Assistant Shopping List!</div>
</td></tr>

{dh}
{dph}
{uph}

<tr><td style="background:#f9fafb;padding:16px 32px;text-align:center">
  <div style="font-size:11px;color:#9ca3af">Woolworths Price Tracker &bull; {ts}<br><span style="font-size:10px">Prices from woolworths.com.au</span></div>
</td></tr>

</table></td></tr></table></body></html>""".format(
            date=date_str, dh=deals_html, dph=drops_html, uph=ups_html, ts=ts_str
        )

        text_body = "Woolworths Price Tracker — {}\n".format(now.strftime('%Y-%m-%d'))
        text_body += "Found: {}/{} | Total: ${:.2f} ({}{:.1f}%)\n\n".format(
            len(found), len(results), total_now, sign, pct)
        text_body += "DEALS ({}):\n".format(len(deals))
        text_body += "\n".join("  [{}] {}: ${:.2f} (was ${:.2f})".format(
            "DOWN" if d['diff'] < -0.005 else "UP" if d['diff'] > 0.005 else "SAME",
            d['name'], d['now'], d['inv']) for d in deals)
        text_body += "\n\nDROPS:\n" + "\n".join("  v {}: ${:.2f} ({:+.0f}%)".format(d['name'], d['now'], d['pct']) for d in downs)
        text_body += "\n\nINCREASES:\n" + "\n".join("  ^ {}: ${:.2f} ({:+.0f}%)".format(u['name'], u['now'], u['pct']) for u in ups)

        mood = "UP" if diff > 0 else "DOWN" if diff < 0 else "FLAT"
        subject = "Woolworths Prices - {} {}".format(now.strftime('%a %d %b'), mood)
        req_data = json.dumps({
            "to": merged.get("to") or merged.get("recipients", ["ian@example.com"])[0],
            "subject": subject,
            "text": text_body,
            "html": html_body,
        }).encode()

        req = urllib.request.Request(
            "https://api.agentmail.to/v0/inboxes/{}/messages/send".format(
                urllib.parse.quote(merged['inbox_id'], safe='@')),
            data=req_data,
            headers={"Authorization": "Bearer {}".format(merged['api_key']), "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status == 200:
                print("  OK Email sent to {}".format(merged.get('to') or merged.get('from_address')))
    except Exception as e:
        print("  [WARN] Email failed: {}".format(e))


def main():
    print("=" * 60)
    print("Woolworths Price Tracker (Direct API)")
    print("=" * 60)

    if not COOKIE_FILE.exists():
        print("\nERROR: No cached cookies at", COOKIE_FILE)
        print("Run extract_cookies.py first to capture from Chrome.")
        return

    cookie_str = load_cookies()
    print(f"Loaded {len(cookie_str.split('; '))} cached cookies")

    results = scrape_all(cookie_str)

    print(f"\n[2/3] Saving results...")
    save_results(results)

    print(f"\n[3/3] Sending email...")
    send_email(results)

    print_summary(results)

    found = [r for r in results if r["status"] == "found"]
    print(f"\nSaved: {PRICE_HISTORY}")
    print(f"Saved: {PRICES_CSV}")
    print(f"Done! {len(found)}/{len(results)} items with prices.")


if __name__ == "__main__":
    main()
