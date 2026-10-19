"""Build a self-contained, one-file `autopilot` executable for this OS."""
from pathlib import Path
import platform
import subprocess
import sys


ROOT = Path(__file__).resolve().parent
SYSTEM_NAMES = {"Linux": "linux", "Windows": "windows", "Darwin": "macos"}


def main() -> int:
    system = SYSTEM_NAMES.get(platform.system())
    if system is None:
        raise SystemExit(f"Unsupported operating system: {platform.system()}")
    arch = platform.machine().lower().replace("amd64", "x86_64").replace("aarch64", "arm64")
    name = f"autopilot-{system}-{arch}"
    if system == "windows":
        name += ".exe"
    command = [
        sys.executable, "-m", "PyInstaller", "--clean", "--noconfirm", "--onefile",
        "--name", name, "--paths", str(ROOT / "src"),
        "--collect-submodules", "autopilot_sdk",
        "--collect-all", "pydantic", "--collect-all", "requests",
        str(ROOT / "src" / "autopilot_sdk" / "__main__.py"),
    ]
    subprocess.run(command, cwd=ROOT, check=True)
    output = ROOT / "dist" / name
    print(f"Built {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
