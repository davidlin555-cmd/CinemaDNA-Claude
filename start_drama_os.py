import sys
import subprocess
import time
import os

def main():
    acting_engine_dir = r"E:\projects\Drama_Acting_Engine-main"
    # Ensure the directory exists or we simulate it if it doesn't on this mock system.
    # To avoid crashing immediately if the dir doesn't exist, we fallback to current dir.
    cwd = acting_engine_dir if os.path.exists(acting_engine_dir) else "."

    print(f"Starting acting engine in background on port 8000 at {cwd}...")
    engine_process = subprocess.Popen(
        [sys.executable, "-m", "http.server", "8000"],
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
