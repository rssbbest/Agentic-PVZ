import multiprocessing
import os

from autopilot_sdk.cli import main

if __name__ == "__main__":
    # Required for frozen executables that start isolated agent workers using
    # multiprocessing's spawn context (Windows and packaged macOS/Linux).
    multiprocessing.freeze_support()
    if os.environ.get("AUTOPILOT_DEBUG") and hasattr(__import__("signal"), "SIGUSR1"):
        # kill -USR1 <pid> prints every thread's stack (for bug reports).
        import faulthandler
        import signal

        faulthandler.register(signal.SIGUSR1, all_threads=True)
    raise SystemExit(main())
