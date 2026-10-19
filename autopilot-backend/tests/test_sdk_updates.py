"""Only the SDK builds of the latest deploy may play; with no manifest, any may."""
import json
import os

from app.services import sdk_updates


def use(monkeypatch, path):
    monkeypatch.setattr(sdk_updates, "_CANDIDATES", (str(path),))
    sdk_updates._cache.update(path=None, mtime=None, accepted=None, manifest=None)


def test_no_manifest_lets_every_sdk_play(monkeypatch, tmp_path):
    use(monkeypatch, tmp_path / "missing.json")
    assert not sdk_updates.outdated("")
    assert not sdk_updates.outdated("anything")


def test_only_the_published_builds_play(monkeypatch, tmp_path):
    path = tmp_path / "sdk-manifest.json"
    path.write_text(json.dumps({"build": "aa" * 32, "wheel": "autopilot_sdk-0.3.0-py3-none-any.whl",
                                "programs": {"autopilot-linux-x86_64": "bb" * 32}}))
    use(monkeypatch, path)
    assert not sdk_updates.outdated("aa" * 32)            # a Python install of the latest package
    assert not sdk_updates.outdated(("BB" * 32) + " ")    # the latest program
    assert sdk_updates.outdated("cc" * 32)                # an older build
    assert sdk_updates.outdated("")                       # an SDK from before the updates sends nothing


def test_a_new_deploy_is_picked_up_without_a_restart(monkeypatch, tmp_path):
    path = tmp_path / "sdk-manifest.json"
    path.write_text(json.dumps({"build": "aa" * 32}))
    use(monkeypatch, path)
    assert not sdk_updates.outdated("aa" * 32)
    path.write_text(json.dumps({"build": "dd" * 32}))
    os.utime(path, (1, 1))  # a different mtime, as a new deploy has
    assert sdk_updates.outdated("aa" * 32) and not sdk_updates.outdated("dd" * 32)
