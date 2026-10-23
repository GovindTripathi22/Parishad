"""
Parishad (Parishad) — 1-Click Zero-Friction Demo Launcher
Run: python launch_demo.py
"""
import sys
import time
import socket
import webbrowser
import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

def is_port_in_use(port: int = 8000) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('127.0.0.1', port)) == 0

def check_health(url: str = "http://127.0.0.1:8000/api/health") -> bool:
    import urllib.request
    try:
        with urllib.request.urlopen(url, timeout=2) as res:
            return res.status == 200
    except Exception:
        return False

def main():
    print("=" * 65)
    print("  PARISHAD (PARISHAD) — AUTONOMOUS COMPANY BRAIN & LEDGER")
    print("  Live System Demonstration Launcher")
    print("=" * 65)

    if is_port_in_use(8000):
        print("[+] Detected active Parishad server on http://localhost:8000")
    else:
        print("[*] Starting FastAPI server on port 8000...")
        subprocess.Popen(
            [sys.executable, str(BASE_DIR / "run.py")],
            cwd=str(BASE_DIR),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        print("[*] Waiting for server initialization...")
        for _ in range(15):
            time.sleep(1)
            if check_health():
                print("[+] Server started successfully!")
                break
        else:
            print("[!] Note: Server is starting up. Launching browser...")

    demo_url = "http://localhost:8000"
    print(f"[+] Opening {demo_url} in your default browser...")
    webbrowser.open(demo_url)

    print("-" * 65)
    print("  QUICK DEMO SHORTCUTS & CHEATSHEET:")
    print("  - Tab 1: Press '1' or click '01 Console' -> Click 48V Battery drift")
    print("  - Tab 2: Press '2' or click '02 Safety Gate' -> Review side-by-side diff")
    print("  - Tab 3: Press '3' or click '03 Company Brain' -> Ask verified questions")
    print("  - Tab 4: Press '4' or click '04 Ledger & SQL' -> Run live SQL queries")
    print("  - Tab 5: Press '5' or click '05 12 Checks' -> Re-run automated tests")
    print("  - Theme: Click Sun/Moon icon in header to switch Dark / Light mode")
    print("  - Script: Read d:\\parishad\\DEMO_SCRIPT_AND_CHEATSHEET.md")
    print("  - PDF:    Open d:\\parishad\\PARISHAD_system demonstration_DEMO_DOSSIER.pdf")
    print("=" * 65)
    print("Press Ctrl+C to close this launcher console.")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nExiting.")
