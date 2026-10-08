#!/usr/bin/env python3
"""Launcher for MapLead Pro Web UI & Lead Engine."""
import sys
import os

# Set utf-8 output encoding for Windows consoles
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Add directory to sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 5000))
    print("\n" + "=" * 55)
    print("MapLead Pro Web Dashboard is starting!")
    print(f">> Open in your browser: http://localhost:{port}")
    print("=" * 55 + "\n")
    uvicorn.run("web.backend:app", host="0.0.0.0", port=port, reload=False)
