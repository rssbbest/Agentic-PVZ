# build/pyinstaller.spec (simplified, per doc §16.1)
# -*- mode: python ; coding: utf-8 -*-
a = Analysis(
    ['../src/autopilot_sdk/cli.py'],
    pathex=['../src'],
    hiddenimports=['pydantic', 'requests'],
    excludes=[],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas,
    name='autopilot',
    onefile=True,
    console=True,
    strip=True,
)
