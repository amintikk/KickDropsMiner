<p align="center">
  <img src="icons/pickaxe.png" alt="Kick Drops Miner logo" width="110" />
</p>

<h1 align="center">Kick Drops Miner</h1>

<p align="center">
  Desktop client for monitoring Kick Drops campaigns and running automated watch sessions.
</p>

<p align="center">
  Inspired by <strong>TwitchDropsMiner</strong> by <strong>DevilXD</strong>.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/platform-Windows%20%7C%20Linux-1f6feb" alt="Platform badge" />
  <img src="https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white" alt="Python badge" />
</p>

## Overview
- Desktop UI for Kick Drops campaigns, progress, channels, and inventory.
- Session management with persistent cookies.
- Queue-based channel worker with automatic fallback when a channel goes offline.
- Campaign/reward thumbnails cached locally for fast UI rendering.

## Requirements
- Python 3.10+
- Google Chrome
- Windows 10/11 or modern Linux desktop

## Run From Source
```powershell
py -3 -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
python app/main.py
```

## Build Portable Binary
```powershell
py -3 -m pip install -r requirements.txt
py -3 -m pip install pyinstaller
py -3 -m PyInstaller --noconfirm --clean build.spec
```

Output: `dist/`

## CI Artifacts
GitHub Actions workflow: `Build Portable Apps`

Artifacts:
- `KickDropsMiner-Windows-x64`
- `KickDropsMiner-Linux-x64`

## Runtime Files
- `kick_config.json` (local settings/queue)
- `cookies/kick.com.json` (saved session cookies)
- `chrome_data/` (browser profile data)
- `cache/reward_thumbs/` (thumbnail cache)
- `logs/app.log` (current session log)

## Troubleshooting
```powershell
py -3 app/diagnose_env.py
```
