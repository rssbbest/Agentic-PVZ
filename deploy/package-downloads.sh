#!/usr/bin/env bash
# Prepare the SDK manifest and remove old manual ZIP bundles. Participants use
# the one-line installer or download the program, agent.py and guide separately.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BIN="$ROOT/autopilot-sdk-binary/bin"
rm -f "$BIN"/*.zip

# The SDK manifest: what every SDK compares itself with (autopilot_sdk/updater.py).
# programs: each download's sha256 (a program updates when its own file differs);
# build + wheel: the Python package's fingerprint and file (a pip install updates
# when its package differs). The backend accepts only these builds (sdk_updates.py).
python3 - "$BIN" "$ROOT/autopilot-sdk/src/autopilot_sdk" "$ROOT/autopilot-sdk-binary/src/autopilot_sdk" <<'PY'
import glob, json, os, runpy, sys
bin_dir, package, program_source = sys.argv[1], sys.argv[2], sys.argv[3]
updater = runpy.run_path(os.path.join(package, "updater.py"))
programs = {name: updater["file_sha256"](os.path.join(bin_dir, name))
            for name in ("autopilot-linux-x86_64", "autopilot-macos-arm64", "autopilot-macos-x86_64",
                         "autopilot-windows-x86_64.exe") if os.path.isfile(os.path.join(bin_dir, name))}
wheels = sorted(glob.glob(os.path.join(bin_dir, "autopilot_sdk-*-py3-none-any.whl")))
manifest = {"build": updater["fingerprint"](package), "wheel": os.path.basename(wheels[-1]) if wheels else None,
            "programs": programs,
            # the code the programs were built from: a deploy with the same code keeps the live programs
            "programsBuild": updater["fingerprint"](program_source)}
with open(os.path.join(bin_dir, "sdk-manifest.json"), "w") as fh:
    json.dump(manifest, fh, indent=2)
print(os.path.join(bin_dir, "sdk-manifest.json"), json.dumps(manifest)[:200])
PY
