# -*- mode: python ; coding: utf-8 -*-

from __future__ import annotations

from pathlib import Path
from PyInstaller.building.build_main import Analysis, PYZ, EXE
from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
)

APP_NAME = "Kick Drops Miner"
ICON_PATH = Path("icons/pickaxe.ico")
APP_ENTRYPOINT = "app/main.py"
APP_PATH = str(Path("app").resolve())

# Keep icon available in packaged builds.
datas: list[tuple[str, str]] = []
if ICON_PATH.exists():
    datas.append((str(ICON_PATH), "icons"))

# Collect dynamic/runtime imports used by Selenium stack and HTTP libs.
hiddenimports: list[str] = ["PIL._tkinter_finder"]
for pkg in (
    "selenium",
    "undetected_chromedriver",
    "browser_cookie3",
    "curl_cffi",
    "websocket",
):
    try:
        hiddenimports += collect_submodules(pkg)
    except Exception:
        pass

# Collect non-Python resources and shared libraries required at runtime.
binaries: list[tuple[str, str]] = []
for pkg in ("curl_cffi", "cryptography"):
    try:
        binaries += collect_dynamic_libs(pkg)
    except Exception:
        pass

for pkg in ("selenium", "undetected_chromedriver", "browser_cookie3", "curl_cffi", "certifi"):
    try:
        datas += collect_data_files(pkg, include_py_files=False)
    except Exception:
        pass

a = Analysis(
    [APP_ENTRYPOINT],
    pathex=[APP_PATH],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
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
