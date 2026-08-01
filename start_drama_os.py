import subprocess
import atexit
import time
import os
import sys

# Core requirement: The exact physical path of the independent acting engine
ENGINE_PATH = r"E:\projects\Drama_Acting_Engine-main"

def start_engine():
    print(f"[*] Starting local acting engine from: {ENGINE_PATH}")

    if sys.platform == "win32":
        cmd = ["python", "-m", "uvicorn", "main:app", "--port", "8000"]
        cwd = ENGINE_PATH
    else:
        print(f"[*] Warning: Not on Windows. Simulating engine start for path {ENGINE_PATH}")
        cmd = ["python3", "-m", "http.server", "8000"]
        cwd = None

    try:
        process = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        print(f"[*] Engine started with PID: {process.pid} on port 8000")
        return process
    except Exception as e:
        print(f"[!] Failed to start engine: {e}")
        return None

def cleanup(process):
    if process and process.poll() is None:
        print(f"[*] Gracefully shutting down acting engine (PID {process.pid})...")
        process.terminate()
        try:
            process.wait(timeout=5)
            print("[*] Engine shut down successfully.")
        except subprocess.TimeoutExpired:
            print("[!] Engine did not shut down gracefully, killing it...")
            process.kill()
            process.wait()
            print("[*] Engine killed.")

from cinemadna.core.drama_os_blueprint import execute_drama_os_cycle

def main_control_loop():
    print("\n" + "="*50)
    print("DramaOS Master Control Loop Started")
    print("="*50)
    print("[*] CinemaDNA Brain Initialized.")
    print("[*] Running initial cycle...")

    # Run one cycle for demonstration
    execute_drama_os_cycle()

    print("[*] Waiting for further tasks (Press Ctrl+C to exit)...")
    try:
        while True:
            # Main control loop placeholder for periodic polling
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[*] Shutting down DramaOS...")

if __name__ == "__main__":
    engine_process = start_engine()

    if engine_process:
        atexit.register(cleanup, engine_process)
        time.sleep(2)
        main_control_loop()
    else:
        print("[!] Cannot start DramaOS without the acting engine.")
        sys.exit(1)
