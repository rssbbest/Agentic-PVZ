
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import os
import threading
import webbrowser

PORT = 8000

# Serve files from the folder containing this script
BUILD_FOLDER = Path(__file__).resolve().parent
os.chdir(BUILD_FOLDER)

if not (BUILD_FOLDER / "index.html").exists():
    raise FileNotFoundError(
        "index.html not found! Put run_game.py inside your WebGL build folder."
    )

server = ThreadingHTTPServer(
    ("0.0.0.0", PORT),
    SimpleHTTPRequestHandler
)

url = f"http://localhost:{PORT}"

print(f"Unity WebGL server running at {url}")
print("Press Ctrl+C to stop the server.")

threading.Timer(1, lambda: webbrowser.open(url)).start()

try:
    server.serve_forever()
except KeyboardInterrupt:
    print("\nStopping server...")
finally:
    server.server_close()
