import sys
import subprocess
import time
import os

def main():
    acting_engine_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "acting_engine")
    cwd = acting_engine_dir if os.path.exists(acting_engine_dir) else "."

    print(f"Starting acting engine in background on port 8000 at {cwd}...")
    engine_process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app", "--port", "8000"],
        cwd=cwd
    )

    try:
        # Give the background server a short time to start
        time.sleep(2)

        print("Starting CinemaDNA main control loop...")
        webui_process = subprocess.Popen(
            [sys.executable, "cinemadna/scripts/run_webui.py"]
        )

        # Wait for the main loop to exit
        webui_process.wait()
    except KeyboardInterrupt:
        print("\nInterrupted by user. Shutting down...")
    finally:
        print("Terminating acting engine background process...")
        engine_process.terminate()
        try:
            engine_process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            engine_process.kill()
        print("Shutdown complete.")

if __name__ == "__main__":
    main()
