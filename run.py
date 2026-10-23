"""
Parishad — Autonomous Company Brain & Decision Ledger
Startup script to launch the FastAPI server and serve the UI.
"""
import os
import sys
import uvicorn
from pathlib import Path

# Add backend directory to Python path
backend_dir = Path(__file__).resolve().parent / "backend"
sys.path.insert(0, str(backend_dir))

def get_target_port(default_port: int = 3000) -> int:
    """
    Determine the server port from CLI arguments, npm environment,
    or environment variables. Defaults to 3000.
    """
    # 1. Positional argument: e.g. python run.py 3000 or python run.py 8000
    for arg in sys.argv[1:]:
        if arg.isdigit():
            return int(arg)
        if arg.startswith("--port="):
            p = arg.split("=", 1)[1]
            if p.isdigit():
                return int(p)
    
    # 2. Named flag: e.g. python run.py --port 3000
    for i, arg in enumerate(sys.argv[1:], start=1):
        if arg in ("--port", "-p") and i < len(sys.argv) - 1:
            val = sys.argv[i + 1]
            if val.isdigit():
                return int(val)

    # 3. Environment variable PORT
    if "PORT" in os.environ and os.environ["PORT"].isdigit():
        return int(os.environ["PORT"])

    return default_port

if __name__ == "__main__":
    if sys.stdout.encoding != 'utf-8':
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except Exception:
            pass

    port = get_target_port(default_port=3000)
    print("=" * 65)
    print("  [*] Parishad -- Autonomous Company Brain & Decision Ledger")
    print(f"  [>] Web UI & API running at: http://localhost:{port}")
    print(f"  [>] Swagger Interactive API docs: http://localhost:{port}/docs")
    print("=" * 65)
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, reload=True, app_dir=str(backend_dir))
