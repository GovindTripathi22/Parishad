"""
Parishad — Autonomous Company Brain & Decision Ledger
Startup script to launch the FastAPI server and serve the UI.
"""
import sys
import uvicorn
from pathlib import Path

# Add backend directory to Python path
backend_dir = Path(__file__).resolve().parent / "backend"
sys.path.insert(0, str(backend_dir))

if __name__ == "__main__":
    if sys.stdout.encoding != 'utf-8':
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except Exception:
            pass
    print("=" * 65)
    print("  [*] Parishad -- Autonomous Company Brain & Decision Ledger")
    print("  [>] Web UI & API running at: http://localhost:8000")
    print("  [>] Swagger Interactive API docs: http://localhost:8000/docs")
    print("=" * 65)
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True, app_dir=str(backend_dir))
