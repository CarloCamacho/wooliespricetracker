#!/usr/bin/env python3
"""Persistent MCP driver — keeps browser alive between calls via file signals."""

import json
import subprocess
import time
import os
from pathlib import Path

WOOLIES_SERVER = "/Users/ianf/workspace/meal-prep/mcp-servers/woolworths-server/dist/index.js"
SIGNAL_DIR = Path("/tmp/woolies_mcp")
SIGNAL_DIR.mkdir(exist_ok=True)
PID_FILE = SIGNAL_DIR / "pid"
STDOUT_PIPE = SIGNAL_DIR / "stdout"
STDIN_PIPE = SIGNAL_DIR / "stdin"
RESPONSE_FILE = SIGNAL_DIR / "response"
COMMAND_FILE = SIGNAL_DIR / "command"
RESULT_FILE = SIGNAL_DIR / "result"

class PersistentMCP:
    def __init__(self):
        self.proc = None
        self._req_id = 0

    def start(self):
        print(f"[MCP] Starting server...")
        self.proc = subprocess.Popen(
            ["node", WOOLIES_SERVER],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        pid = self.proc.pid
        print(f"[MCP] PID: {pid}")
        with open(PID_FILE, "w") as f:
            f.write(str(pid))

        # Initialize MCP
        self._send("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "woolies", "version": "1.0"},
        })
        self._send("notifications/initialized", {})
        print("[MCP] Initialized")

    def call_tool(self, name, args=None):
        print(f"  → calling {name}")
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
        request = {
            "jsonrpc": "2.0",
            "id": self._req_id,
            "method": method,
            "params": params or {},
        }
        self.proc.stdin.write(json.dumps(request) + "\n")
        self.proc.stdin.flush()

        while True:
            line = self.proc.stdout.readline()
            if not line:
                return {"error": "No response"}
            try:
                data = json.loads(line.strip())
                if data.get("id") == self._req_id:
                    return data
            except json.JSONDecodeError:
                continue

    def wait_forever(self):
        """Keep process alive — read stdin for commands."""
        print(f"[MCP] Server running. PID file: {PID_FILE}")
        print("Press Ctrl+C to stop.")
        try:
            while self.proc.poll() is None:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n[MCP] Shutting down...")
            self.proc.terminate()
            self.proc.wait()

if __name__ == "__main__":
    mcp = PersistentMCP()
    mcp.start()

    # Open browser
    print("\nOpening Woolworths browser (visible)...")
    result = mcp.call_tool("woolworths_open_browser", {"headless": False})
    print(json.dumps(result, indent=2)[:200])
    print("\nBrowser is open. Log in when ready, then run:")
    print("  python3 scrape_prices.py --continue")

    mcp.wait_forever()
