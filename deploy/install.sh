#!/bin/sh
# Autopilot SDK: install and start, on macOS and Linux (and any Unix with Python 3.10+).
#
#   curl -fsSL https://13-200-183-134.sslip.io/install.sh | sh
#
# Puts everything in ~/autopilot (AUTOPILOT_DIR to change it): the SDK program
# for this computer and a starter agent.py (an existing agent.py is never
# overwritten). If no program fits this computer, or it does not run here, it
# installs the SDK with Python instead: a venv first, then pip --user.
# Then it starts the SDK. Run it again any time to update.
set -eu

BASE="${AUTOPILOT_BASE:-https://13-200-183-134.sslip.io}"
DIR="${AUTOPILOT_DIR:-$HOME/autopilot}"

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*" >&2; }
fetch() {  # fetch URL FILE
	if command -v curl >/dev/null 2>&1; then curl -fsSL "$1" -o "$2"
	elif command -v wget >/dev/null 2>&1; then wget -q -O "$2" "$1"
	else warn "Needs curl or wget"; return 1; fi
}

mkdir -p "$DIR"
cd "$DIR"
os=$(uname -s)
arch=$(uname -m)
case "$os/$arch" in
	Linux/x86_64 | Linux/amd64) program=autopilot-linux-x86_64 ;;
	Darwin/*) program="" ;; # the Mac programs are built separately and can lag behind: Python installs the current SDK
	*) program="" ;;
esac
[ "$os" = Darwin ] && [ -n "${AUTOPILOT_MAC_PROGRAM:-}" ] && program="autopilot-macos-$arch"
[ -n "${AUTOPILOT_FORCE_PYTHON:-}" ] && program=""

[ -f agent.py ] || fetch "$BASE/download/agent.py" agent.py
fetch "$BASE/download/README_PARTICIPANT.md" README_PARTICIPANT.md || true

start=""
if [ -n "$program" ]; then
	say "Downloading the Autopilot SDK for $os $arch"
	if fetch "$BASE/download/$program" "$program.new"; then
		chmod +x "$program.new"
		mv -f "$program.new" "$program"
		if "./$program" version >/dev/null 2>&1; then
			start="./$program"
		else
			warn "The SDK program does not run on this computer; installing it with Python instead"
		fi
	else
		warn "Download failed; installing with Python instead"
	fi
else
	say "No ready-made program for $os $arch: installing the SDK with Python"
fi

if [ -z "$start" ]; then
	py=""
	for candidate in python3.14 python3.13 python3.12 python3.11 python3.10 python3 python; do
		if command -v "$candidate" >/dev/null 2>&1 &&
			"$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
			py=$(command -v "$candidate")
			break
		fi
	done
	if [ -z "$py" ]; then
		warn "Needs Python 3.10 or newer (a Mac's built-in python3 is 3.9). Install it from https://www.python.org/downloads/ and run this line again."
		exit 1
	fi
	fetch "$BASE/download/" listing.html
	wheel=$(grep -o 'autopilot_sdk-[0-9][0-9.]*-py3-none-any\.whl' listing.html | head -n 1)
	rm -f listing.html
	[ -n "$wheel" ] || { warn "The SDK package is missing on the server; tell the organisers"; exit 1; }
	say "Installing the SDK with $py ($("$py" --version 2>&1))"
	if "$py" -m venv .venv >/dev/null 2>&1 &&
		.venv/bin/python -m pip install -q --disable-pip-version-check --no-cache-dir --upgrade "$BASE/download/$wheel" &&
		.venv/bin/python -m pip install -q --disable-pip-version-check --no-cache-dir --force-reinstall --no-deps "$BASE/download/$wheel"; then
		runner="$DIR/.venv/bin/python"
	else
		warn "Could not make a venv (on Debian/Ubuntu: sudo apt install python3-venv); using pip --user"
		rm -rf .venv
		if ! "$py" -m pip --version >/dev/null 2>&1; then
			say "Installing pip for $py"
			fetch https://bootstrap.pypa.io/get-pip.py get-pip.py
			"$py" get-pip.py -q --user 2>/dev/null || "$py" get-pip.py -q --user --break-system-packages
			rm -f get-pip.py
		fi
		# --force-reinstall: the same version number with new code still installs (see updater.py)
		"$py" -m pip install -q --user --no-warn-script-location --no-cache-dir --upgrade "$BASE/download/$wheel" 2>/dev/null ||
			"$py" -m pip install -q --user --no-warn-script-location --no-cache-dir --upgrade --break-system-packages "$BASE/download/$wheel"
		"$py" -m pip install -q --user --no-warn-script-location --no-cache-dir --force-reinstall --no-deps "$BASE/download/$wheel" 2>/dev/null ||
			"$py" -m pip install -q --user --no-warn-script-location --no-cache-dir --force-reinstall --no-deps --break-system-packages "$BASE/download/$wheel"
		runner="$py"
	fi
	printf '#!/bin/sh\ncd "%s" && exec "%s" -m autopilot_sdk "$@"\n' "$DIR" "$runner" >autopilot
	chmod +x autopilot
	start="./autopilot"
fi

say "Installed in $DIR"
say "Next time: open a terminal and run   cd \"$DIR\" && $start"
[ -n "${AUTOPILOT_NO_START:-}" ] && exit 0
if (exec </dev/tty) 2>/dev/null; then
	exec $start </dev/tty # the console reads the keyboard, not this script
fi
say "Start it with the line above."
