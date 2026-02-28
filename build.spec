# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
from PyInstaller.building.build_main import Analysis, PYZ, EXE

APP_NAME = "Kick Drops Miner"
ICON_PATH = Path("icons/pickaxe.ico")

# Keep optional icon data available at runtime in packaged builds.
datas = []
if ICON_PATH.exists():
    datas.append((str(ICON_PATH), "icons"))

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=["PIL._tkinter_finder"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=APP_NAME,
    debug=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(ICON_PATH) if ICON_PATH.exists() else None,
)
