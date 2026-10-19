"""The SDK keeps itself on the organisers' latest build."""
import hashlib
import subprocess
import sys

import pytest

from autopilot_sdk import updater


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.delenv("AUTOPILOT_NO_UPDATE", raising=False)
    monkeypatch.delenv("AUTOPILOT_JUST_UPDATED", raising=False)
    monkeypatch.delenv("AUTOPILOT_DOWNLOAD_URL", raising=False)
    monkeypatch.setattr(updater, "_identity", None)


def test_the_fingerprint_covers_the_code_only(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.py").write_text("y = 2\n")
    first = updater.fingerprint(tmp_path)
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "a.cpython-312.py").write_text("junk")
    (tmp_path / "notes.txt").write_text("not code")
    assert updater.fingerprint(tmp_path) == first
    (tmp_path / "a.py").write_text("x = 1\r\n")          # a Windows checkout is the same code
    assert updater.fingerprint(tmp_path) == first
    (tmp_path / "sub" / "b.py").write_text("y = 3\n")
    assert updater.fingerprint(tmp_path) != first


def test_downloads_are_beside_the_event_backend(monkeypatch):
    assert updater.download_base("https://host.example/backend") == "https://host.example/download/"
    assert updater.download_base("https://host.example/backend/") == "https://host.example/download/"
    assert updater.download_base("http://127.0.0.1:8010") is None       # a local backend: no updates
    monkeypatch.setenv("AUTOPILOT_DOWNLOAD_URL", "http://127.0.0.1:9000/dl")
    assert updater.download_base("http://127.0.0.1:8010") == "http://127.0.0.1:9000/dl/"


def test_a_program_updates_when_its_file_differs(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(updater, "program_name", lambda: "autopilot-linux-x86_64")
    monkeypatch.setattr(updater, "_identity", "aa" * 32)
    latest = {"programs": {"autopilot-linux-x86_64": "bb" * 32}, "build": "cc" * 32, "wheel": "w.whl"}
    assert updater.wanted(latest) == ("autopilot-linux-x86_64", "bb" * 32)
    assert updater.wanted({**latest, "programs": {"autopilot-linux-x86_64": "aa" * 32}}) is None
    assert updater.wanted({**latest, "programs": {}}) is None           # no program for this computer


def test_a_python_install_updates_when_its_package_differs(monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setattr(updater, "_identity", "aa" * 32)
    assert updater.wanted({"build": "bb" * 32, "wheel": "autopilot_sdk-0.3.0-py3-none-any.whl"}) == \
        ("autopilot_sdk-0.3.0-py3-none-any.whl", "bb" * 32)
    assert updater.wanted({"build": "aa" * 32, "wheel": "x.whl"}) is None


def test_the_program_is_swapped_only_for_the_published_file(monkeypatch, tmp_path):
    exe = tmp_path / "autopilot-linux-x86_64"
    exe.write_bytes(b"old program")
    new = b"new program"
    monkeypatch.setattr(sys, "executable", str(exe))
    monkeypatch.setattr(updater, "_get", lambda url, timeout: new)
    with pytest.raises(OSError):
        updater._swap_program("http://x/", exe.name, "00" * 32)       # tampered or truncated download
    assert exe.read_bytes() == b"old program"
    updater._swap_program("http://x/", exe.name, hashlib.sha256(new).hexdigest())
    assert exe.read_bytes() == new and exe.stat().st_mode & 0o111


def test_update_downloads_swaps_and_restarts(monkeypatch, tmp_path):
    exe = tmp_path / "autopilot-linux-x86_64"
    exe.write_bytes(b"old program")
    new = b"new program"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    monkeypatch.setattr(updater, "program_name", lambda: exe.name)
    monkeypatch.setattr(updater, "fetch_manifest", lambda base, timeout=8.0: {
        "programs": {exe.name: hashlib.sha256(new).hexdigest()}})
    monkeypatch.setattr(updater, "_get", lambda url, timeout: new)
    restarted = []
    monkeypatch.setattr(updater, "_restart", lambda installed: restarted.append(installed))
    said = []
    assert updater.update_if_needed("https://host/backend", say=said.append)
    assert exe.read_bytes() == new and "Updated" in said[-1]
    assert restarted == [hashlib.sha256(new).hexdigest()]   # the restart knows what it installed


def test_no_update_loop_and_no_update_when_turned_off(monkeypatch):
    latest = {"build": "bb" * 32, "wheel": "w.whl"}
    monkeypatch.setattr(updater, "fetch_manifest", lambda base, timeout=8.0: latest)
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setattr(updater, "_identity", "aa" * 32)
    monkeypatch.setattr(updater, "_install_package", lambda base, wheel: pytest.fail("must not install"))
    monkeypatch.setenv("AUTOPILOT_JUST_UPDATED", "bb" * 32)   # bb was just installed, yet this is still aa
    said = []
    assert not updater.update_if_needed("https://host/backend", say=said.append)
    assert "install line" in said[-1]
    monkeypatch.setenv("AUTOPILOT_NO_UPDATE", "1")
    assert not updater.update_if_needed("https://host/backend", say=said.append)


def test_a_release_later_in_the_session_still_updates(monkeypatch):
    # updated to bb at the start; the organisers release cc while the SDK runs
    monkeypatch.setattr(updater, "fetch_manifest", lambda base, timeout=8.0: {"build": "cc" * 32, "wheel": "w.whl"})
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setattr(updater, "_identity", "bb" * 32)
    monkeypatch.setenv("AUTOPILOT_JUST_UPDATED", "bb" * 32)
    installed, restarted = [], []
    monkeypatch.setattr(updater, "_install_package", lambda base, wheel: installed.append(wheel))
    monkeypatch.setattr(updater, "_restart", lambda sha: restarted.append(sha))
    assert updater.update_if_needed("https://host/backend", say=lambda text: None)
    assert installed == ["w.whl"] and restarted == ["cc" * 32]


def test_an_unreachable_download_server_never_stops_the_sdk(monkeypatch):
    monkeypatch.setattr(updater, "fetch_manifest", lambda base, timeout=8.0: None)
    assert not updater.update_if_needed("https://host/backend", say=lambda text: None)


def test_pip_falls_back_to_user_installs_outside_a_venv(monkeypatch):
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0 if "--break-system-packages" in cmd else 1)

    monkeypatch.setattr(updater.subprocess, "run", run)
    monkeypatch.setattr(sys, "base_prefix", sys.prefix)  # not a venv
    updater._install_package("https://host/download/", "autopilot_sdk-0.3.0-py3-none-any.whl")
    assert ["--user", "--break-system-packages"] == [a for a in calls[-1] if a in ("--user", "--break-system-packages")]
    assert "--force-reinstall" in calls[-1]


def test_the_sdk_sends_its_build_with_every_request():
    from autopilot_sdk import local_proxy

    assert local_proxy._session.headers[updater.HEADER] == updater.identity()


def test_windows_swap_waits_for_a_locked_file_and_never_loses_the_program(monkeypatch, tmp_path):
    exe = tmp_path / "autopilot-windows-x86_64.exe"
    exe.write_bytes(b"old program")
    new = b"new program"
    monkeypatch.setattr(sys, "executable", str(exe))
    monkeypatch.setattr(updater, "_get", lambda url, timeout: new)
    monkeypatch.setattr(updater, "_windows", lambda: True)
    monkeypatch.setattr("time.sleep", lambda s: None)
    real_replace, locked = updater.os.replace, {"n": 2}

    def replace(src, dst):
        if str(src).endswith(".new") and locked["n"]:
            locked["n"] -= 1
            raise PermissionError("held by a scan")
        real_replace(src, dst)

    monkeypatch.setattr(updater.os, "replace", replace)
    updater._swap_program("http://x/", exe.name, hashlib.sha256(new).hexdigest())
    assert exe.read_bytes() == new and (tmp_path / (exe.name + ".old")).read_bytes() == b"old program"

    exe.write_bytes(b"old program")
    (tmp_path / (exe.name + ".old")).unlink()
    locked["n"] = 99  # never released: the old program goes back in place
    with pytest.raises(PermissionError):
        updater._swap_program("http://x/", exe.name, hashlib.sha256(new).hexdigest())
    assert exe.read_bytes() == b"old program"
