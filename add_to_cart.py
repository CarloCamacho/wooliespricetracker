#!/usr/bin/env python3
"""
Add deal items to Woolworths cart via MCP server.
Uses PTY (pseudo-terminal) to avoid Node.js stdout buffering.
Requires MCP server at ~/workspace/meal-prep/mcp-servers/woolworths-server/
"""
import json, time, os, pty, tty, subprocess, select
from pathlib import Path

MCP_DIR = Path.home() / "workspace" / "meal-prep" / "mcp-servers" / "woolworths-server"
COOKIE_FILE = Path.home() / ".hermes" / "woolworths_cookies.json"

# Deal items from shopping list
ITEMS = [
    ("49622", "Golden Crumpets Round 6 pack", 1),
    ("919345", "Mission Carb Balance Wraps 6pk", 2),
    ("164644", "Woolworths Asian Salad Bowl", 1),
    ("271264", "Nature's Gift Dog Treats Beef Liver", 1),
    ("669379", "Coca-Cola Zero 10pk", 1),
]


class MCPClient:
    """MCP client using PTY to avoid Node.js buffering."""
    
    def __init__(self):
        self.master_fd = None
        self.proc = None
        self.buf = b""
        
    def start(self):
        master_fd, slave_fd = pty.openpty()
        tty.setraw(master_fd)
        self.master_fd = master_fd
        self.proc = subprocess.Popen(
            ["node", str(MCP_DIR / "dist" / "index.js")],
            stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
        )
        os.close(slave_fd)
        
        # Initialize handshake
        self._call("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "cart-adder", "version": "1.0"}
        })
        self._notify("notifications/initialized", {})
        print("MCP server initialized")
        
    def _send(self, msg):
        data = (json.dumps(msg) + "\n").encode()
        os.write(self.master_fd, data)
        
    def _read_response(self, req_id, timeout=60):
        deadline = time.time() + timeout
        while time.time() < deadline:
            ready, _, _ = select.select([self.master_fd], [], [], 1.0)
            if ready:
                try:
                    chunk = os.read(self.master_fd, 4096)
                    if not chunk:
                        continue
                    self.buf += chunk
                except OSError:
                    continue
            # Try to parse complete lines
            while b"\n" in self.buf:
                line, self.buf = self.buf.split(b"\n", 1)
                try:
                    data = json.loads(line.strip())
                    if data.get("id") == req_id:
                        return data
                    # Check for errors without id
                    if "error" in data and "id" not in data:
                        return data
                except (json.JSONDecodeError, ValueError):
                    pass
        return {"error": "timeout"}
    
    def _call(self, method, params):
        rid = int(time.time() * 1000)
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
        return self._read_response(rid)
    
    def _notify(self, method, params):
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})
    
    def call_tool(self, name, args):
        return self._call("tools/call", {"name": name, "arguments": args})
    
    def close(self):
        if self.proc:
            self.proc.terminate()
            self.proc.wait(timeout=5)
        if self.master_fd:
            os.close(self.master_fd)


def main():
    print("Woolworths Cart Adder")
    print("=" * 50)
    
    # Load cookies and inject them
    if not COOKIE_FILE.exists():
        print("ERROR: No cookies at", COOKIE_FILE)
        return
    
    with open(COOKIE_FILE) as f:
        cookies = json.load(f)
    print(f"Loaded {len(cookies)} cookies")
    
    # Start MCP server
    client = MCPClient()
    client.start()
    
    # Inject cookies (skip browser — use cached session)
    print("Injecting cookies...")
    r = client.call_tool("woolworths_set_cookies", {"cookies": cookies})
    if "error" in r:
        print(f"WARN: set_cookies error: {r['error']}")
    
    # Check current cart
    print("\nChecking cart...")
    r = client.call_tool("woolworths_get_cart", {})
    result = r.get("result", {})
    content = result.get("content", [])
    if content:
        text = content[0].get("text", "")
        try:
            cart_data = json.loads(text)
            print(f"Cart has {len(cart_data.get('items', cart_data.get('Products', [])))} items")
        except:
            print(f"Cart response: {text[:200]}")
    
    # Add items
    print("\nAdding items:")
    for stockcode, name, qty in ITEMS:
        print(f"  Adding: {name} (stockcode={stockcode}, qty={qty})...")
        r = client.call_tool("woolworths_add_to_cart", {
            "stockcode": stockcode,
            "quantity": qty,
        })
        
        result = r.get("result", {})
        content = result.get("content", [])
        err = r.get("error")
        
        if err:
            print(f"    ❌ Error: {err}")
        elif content:
            text = content[0].get("text", "")
            try:
                data = json.loads(text)
                success = data.get("success", data.get("Success", True))
                print(f"    {'✅' if success else '❌'} {text[:120]}")
            except:
                print(f"    ✅ {text[:120]}")
        else:
            print(f"    ✅ Added (no content)")
        
        time.sleep(0.5)
    
    # Verify cart
    print("\nFinal cart:")
    r = client.call_tool("woolworths_get_cart", {})
    result = r.get("result", {})
    content = result.get("content", [])
    if content:
        text = content[0].get("text", "")
        try:
            cart = json.loads(text)
            items = cart.get("items", cart.get("Products", []))
            total = 0
            for item in items:
                name = item.get("DisplayName") or item.get("Name", "?")
                price = item.get("Price") or 0
                qty = item.get("Quantity", 1)
                print(f"  {name[:50]} x{qty} — ${price:.2f}")
                total += price * qty
            print(f"  {'─' * 40}")
            print(f"  Total: ${total:.2f}")
        except:
            print(f"  Raw: {text[:300]}")
    
    client.close()
    print("\nDone!")


if __name__ == "__main__":
    main()
