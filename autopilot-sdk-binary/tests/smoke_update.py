"""Smoke test for the SDK's own updates (run on each OS in CI).

    python tests/smoke_update.py OLD_PROGRAM NEW_PROGRAM [OLD_WHEEL_DIR NEW_WHEEL_DIR]

OLD and NEW are two builds of the SDK (NEW reports a different version). A
stand-in download server publishes NEW in sdk-manifest.json, as a deploy does.
Checks, with the standard library only:
  1. the program: OLD, started as `update`, downloads NEW, checks its sha256,
     replaces itself and starts again as NEW, which finds itself up to date
     (Windows: the replaced program is removed at the next start);
  2. with the wheels: a Python install of OLD updates itself to NEW with pip in
     the same way (`python -m autopilot_sdk update`).
"""
import glob
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import venv
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def sha256(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def version(cmd, env) -> str:
    out = subprocess.run([*cmd, "version"], capture_output=True, text=True, env=env, timeout=120)
    assert out.returncode == 0, out
    return out.stdout.strip()


def serve(folder: Path) -> tuple[ThreadingHTTPServer, str]:
    handler = partial(SimpleHTTPRequestHandler, directory=str(folder))
    handler.log_message = lambda *a: None
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/"


def env_for(url: str) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("AUTOPILOT_", "_PYI", "_MEI"))}
    env.update(AUTOPILOT_DOWNLOAD_URL=url, AUTOPILOT_BACKEND_URL="http://127.0.0.1:9", PYTHONIOENCODING="utf-8")
    return env


def update(cmd, env) -> str:
    out = subprocess.run([*cmd, "update"], capture_output=True, text=True, env=env, timeout=600)
    text = out.stdout + out.stderr
    print(text)
    assert out.returncode == 0, f"update failed ({out.returncode})"
    return text


def check_program(old: Path, new: Path, work: Path) -> None:
    downloads = work / "download"
    downloads.mkdir()
    name = {"win32": "autopilot-windows-x86_64.exe", "darwin": "autopilot-macos-arm64"}.get(
        sys.platform, "autopilot-linux-x86_64")
    if sys.platform == "darwin" and os.uname().machine != "arm64":
        name = "autopilot-macos-x86_64"
    shutil.copy2(new, downloads / name)
    (downloads / "sdk-manifest.json").write_text(json.dumps({"programs": {name: sha256(new)}}))
    server, url = serve(downloads)
    try:
        installed = work / "program" / name  # the participant's copy, any folder
        installed.parent.mkdir()
        shutil.copy2(old, installed)
        env = env_for(url)
        before, wanted = version([str(installed)], env), version([str(new)], env)
        assert before != wanted, "the two builds must report different versions"
        text = update([str(installed)], env)
        assert "Updated" in text and "is the latest SDK" in text, text
        assert sha256(installed) == sha256(new), "the program was not replaced"
        assert version([str(installed)], env) == wanted
        if sys.platform == "win32":
            assert not (installed.parent / (name + ".old")).exists(), "the replaced program was not removed"
        assert "is the latest SDK" in update([str(installed)], env)  # no second download
        print(f"program update OK: {before} -> {wanted}")
    finally:
        server.shutdown()


def check_python(old_dir: Path, new_dir: Path, work: Path) -> None:
    old_wheel = Path(glob.glob(str(old_dir / "autopilot_sdk-*.whl"))[0])
    new_wheel = Path(glob.glob(str(new_dir / "autopilot_sdk-*.whl"))[0])
    assert old_wheel.name == new_wheel.name  # same version number, new code: must still update
    downloads = work / "pydownload"
    downloads.mkdir()
    shutil.copy2(new_wheel, downloads / new_wheel.name)
    with tempfile.TemporaryDirectory() as unpack:  # the new package's fingerprint, as the deploy computes it
        shutil.unpack_archive(str(new_wheel), unpack, "zip")
        sys.path.insert(0, unpack)
        from autopilot_sdk import updater  # noqa: PLC0415 - the new wheel's updater
        build = updater.fingerprint(Path(unpack) / "autopilot_sdk")
        sys.path.pop(0)
    (downloads / "sdk-manifest.json").write_text(json.dumps({"build": build, "wheel": new_wheel.name}))
    server, url = serve(downloads)
    try:
        venv.create(work / "venv", with_pip=True)
        python = work / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        subprocess.run([str(python), "-m", "pip", "install", "-q", str(old_wheel)], check=True)
        env = env_for(url)
        cmd = [str(python), "-m", "autopilot_sdk"]
        before = version(cmd, env)
        text = update(cmd, env)
        assert "Updated" in text and "is the latest SDK" in text, text
        after = version(cmd, env)
        assert after != before and build[:12] in after, (before, after)
        print(f"Python install update OK: {before} -> {after}")
    finally:
        server.shutdown()


def main() -> int:
    old, new = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
    with tempfile.TemporaryDirectory() as tmp:
        check_program(old, new, Path(tmp))
        if len(sys.argv) >= 5:
            check_python(Path(sys.argv[3]).resolve(), Path(sys.argv[4]).resolve(), Path(tmp))
    print("update smoke test passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
