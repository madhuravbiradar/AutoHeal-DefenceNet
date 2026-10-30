"""Start the control plane + dashboard:  python run.py   ->  http://localhost:8000"""
import os
import sys
import threading
import webbrowser

if __name__ == "__main__":
    try:
        import uvicorn
    except ImportError:
        sys.exit("Missing dependencies. Run:  pip install -r requirements.txt")
    os.makedirs("data", exist_ok=True)
    port = int(os.environ.get("PORT", 8000))
    if os.environ.get("NO_BROWSER") != "1":
        threading.Timer(1.5, lambda: webbrowser.open(f"http://localhost:{port}")).start()
    uvicorn.run("api.main:app", host="127.0.0.1", port=port, log_level="warning")
