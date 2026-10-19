"""Keeps every participant on the latest SDK.

Each deploy publishes ``sdk-manifest.json`` next to the downloads (see
deploy/package-downloads.sh): the sha256 of every SDK program and a
fingerprint of the Python package. An SDK knows what it is (``identity()``:
its own program's sha256, or its package's fingerprint) and sends that to the
backend with every request (``X-Autopilot-SDK``). It checks the manifest at
start and before every PLAY / PUBLISH; the backend also refuses a game or a
run from an older SDK (HTTP 426), which makes a running SDK update at once.

Updating: a program downloads its new self, checks the sha256, swaps the file
and starts again with the same arguments; a Python install pip-installs the
new package and starts again. Nothing here needs more than the standard
library, so ``python updater.py fingerprint <package dir>`` works on a bare
machine (the deploy computes the manifest that way).

AUTOPILOT_NO_UPDATE=1 turns the updates off (an organiser's checkout).
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
import urllib.request
from pathlib import Path

HEADER = "X-Autopilot-SDK"
MANIFEST = "sdk-manifest.json"
OUTDATED_STATUS = 426  # the backend's reply to an SDK that is not the latest
_UPDATED_ENV = "AUTOPILOT_JUST_UPDATED"  # on the restart after an update: the build it installed (never loop)


def fingerprint(package_dir: str | os.PathLike | None = None) -> str:
    """sha256 over the package's .py files (path and contents), the same for the
    source tree, an installed wheel and the deploy's manifest."""
    root = Path(package_dir or Path(__file__).resolve().parent)
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.py"), key=lambda p: p.relative_to(root).as_posix()):
        rel = path.relative_to(root).as_posix()
        if "__pycache__" in rel.split("/"):
            continue
        digest.update(rel.encode() + b"\0" + path.read_bytes().replace(b"\r\n", b"\n") + b"\0")
    return digest.hexdigest()


def file_sha256(path: str | os.PathLike) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def program_name() -> str | None:
    """The download that is this computer's SDK program."""
    machine = platform.machine().lower()
    if sys.platform.startswith("win"):
        return "autopilot-windows-x86_64.exe"
    if sys.platform == "darwin":
        return "autopilot-macos-arm64" if machine in ("arm64", "aarch64") else "autopilot-macos-x86_64"
    if sys.platform.startswith("linux") and machine in ("x86_64", "amd64"):
        return "autopilot-linux-x86_64"
    return None


_identity: str | None = None


def identity() -> str:
    """What this SDK is: its program's sha256, or its package's fingerprint."""
    global _identity
    if _identity is None:
        try:
            _identity = file_sha256(sys.executable) if frozen() else fingerprint()
        except OSError:
            _identity = "unknown"
    return _identity


def disabled() -> bool:
    return os.environ.get("AUTOPILOT_NO_UPDATE", "").strip().lower() in ("1", "true", "yes")


def download_base(backend_url: str) -> str | None:
    """Where the downloads are: AUTOPILOT_DOWNLOAD_URL, else beside the event
    backend (https://host/backend -> https://host/download/)."""
    explicit = os.environ.get("AUTOPILOT_DOWNLOAD_URL", "").strip()
    if explicit:
        return explicit.rstrip("/") + "/"
    url = backend_url.rstrip("/")
    if url.endswith("/backend"):
        return url[: -len("/backend")] + "/download/"
    return None  # a local backend has no downloads: nothing to update from


def _get(url: str, timeout: float) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "autopilot-sdk-updater", "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - the organisers' https server
        return resp.read()


def fetch_manifest(base: str, timeout: float = 8.0) -> dict | None:
    try:
        manifest = json.loads(_get(base + MANIFEST, timeout))
    except (OSError, ValueError):
        return None
    return manifest if isinstance(manifest, dict) else None


def wanted(manifest: dict) -> tuple[str, str] | None:
    """(what to download, its sha256 or the package fingerprint) if this SDK is
    not the latest, else None."""
    if frozen():
        name = program_name()
        sha = (manifest.get("programs") or {}).get(name or "")
        if name and sha and sha != identity():
            return name, sha
        return None
    build, wheel = manifest.get("build"), manifest.get("wheel")
    if build and wheel and build != identity():
        return wheel, build
    return None


def _restart(installed: str) -> None:
    """Start this SDK again with the same arguments (never returns)."""
    env = {**os.environ, _UPDATED_ENV: installed}
    if frozen():
        # PyInstaller (>= 6.9): the new program starts as a fresh program, not as a
        # child of this one's unpacked copy
        env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
        cmd = [sys.executable, *sys.argv[1:]]
    else:
        cmd = [sys.executable, "-m", "autopilot_sdk", *sys.argv[1:]]
    sys.stdout.flush()
    sys.stderr.flush()
    if os.name == "nt":  # Windows has no exec: run the new SDK in this console, then leave with its exit code
        sys.exit(subprocess.call(cmd, env=env))
    os.execve(cmd[0], cmd, env)


def _swap_program(base: str, name: str, sha: str) -> None:
    exe = Path(sys.executable).resolve()
    new = exe.with_name(exe.name + ".new")
    data = _get(base + name, timeout=300)
    if hashlib.sha256(data).hexdigest() != sha:
        raise OSError("the download did not match the published sha256")
    new.write_bytes(data)
    new.chmod(0o755)
    if not _windows():
        os.replace(new, exe)  # the running program keeps its old file until it restarts
        return
    # Windows: a running .exe can be renamed but not replaced, and a virus scan may
    # hold the new file for a moment. Never leave the participant without a program.
    old = exe.with_name(exe.name + ".old")
    try:
        old.unlink()
    except OSError:
        pass
    _retry(lambda: os.replace(exe, old))
    try:
        _retry(lambda: os.replace(new, exe))
    except OSError:
        os.replace(old, exe)  # put the running program back
        raise


def _windows() -> bool:
    return os.name == "nt"


def _retry(step, tries: int = 10, wait: float = 1.0) -> None:
    import time

    for attempt in range(tries):
        try:
            step()
            return
        except PermissionError:
            if attempt == tries - 1:
                raise
            time.sleep(wait)


def _install_package(base: str, wheel: str) -> None:
    """pip into the same Python, as install.sh / install.ps1 did: the venv, else --user."""
    pip = [sys.executable, "-m", "pip", "install", "-q", "--disable-pip-version-check", "--no-cache-dir"]
    in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    tries = [[]] if in_venv else [["--user"], ["--user", "--break-system-packages"]]
    for extra in tries:
        # first any new dependency, then the package itself even if its version number
        # is unchanged (pip would call it "already satisfied")
        if (subprocess.run([*pip, *extra, "--upgrade", base + wheel]).returncode == 0
                and subprocess.run([*pip, *extra, "--force-reinstall", "--no-deps", base + wheel]).returncode == 0):
            return
    raise subprocess.CalledProcessError(1, "pip install " + base + wheel)


def clean_old() -> None:
    """Windows keeps the replaced program as .old until the next start."""
    if frozen() and os.name == "nt":
        try:
            Path(sys.executable).with_name(Path(sys.executable).name + ".old").unlink()
        except OSError:
            pass


def update_if_needed(backend_url: str, say=print, force: bool = False) -> bool:
    """Update to the latest SDK and restart if this one is older (never returns
    then). Returns False if this SDK is the latest or cannot update (it says why).
    ``force``: the backend just refused this SDK as outdated."""
    if disabled():
        return False
    base = download_base(backend_url)
    if base is None:
        return False
    manifest = fetch_manifest(base)
    if manifest is None:
        if force:
            say(f"This SDK is out of date and the update could not be downloaded from {base}. "
                "Run the install line again.")
        return False
    # Only the first check after an update knows what it installed; a release
    # made later in the session updates again as usual.
    just_installed = os.environ.pop(_UPDATED_ENV, None)
    need = wanted(manifest)
    if need is None:
        return False
    name, sha = need
    if sha == just_installed:  # that very build was just installed and this still is not it: no loop
        say("This SDK was just updated but still differs from the latest one. "
            "Run the install line again (see README_PARTICIPANT.md).")
        return False
    say(f"A new Autopilot SDK is out: updating now ({name}) ...")
    try:
        if frozen():
            _swap_program(base, name, sha)
        else:
            _install_package(base, name)
    except (OSError, subprocess.CalledProcessError) as exc:
        say(f"The update failed ({exc}). Run the install line again (see README_PARTICIPANT.md).")
        return False
    say("Updated. Starting the new SDK ...")
    _restart(sha)
    return True  # pragma: no cover - _restart does not return


class Outdated(Exception):
    """The backend refused this SDK as outdated: update before going on."""


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "fingerprint":
        print(fingerprint(sys.argv[2] if len(sys.argv) > 2 else None))
    elif len(sys.argv) >= 2 and sys.argv[1] == "sha256":
        print(file_sha256(sys.argv[2]))
    else:
        sys.exit("usage: updater.py fingerprint [package dir] | sha256 FILE")
