<p align="center">
  <img src="icons/pickaxe.png" alt="Kick Drops Miner logo" width="110" />
</p>

<h1 align="center">Kick Drops Miner</h1>

<p align="center">
  Desktop app to track and farm Kick Drops automatically.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/platform-Windows%20%7C%20Linux-1f6feb" alt="Platform badge" />
  <img src="https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white" alt="Python badge" />
  <img src="https://img.shields.io/github/actions/workflow/status/amintikk/KickDropsMiner/build-binaries.yml?label=portable%20builds" alt="Build status badge" />
</p>

## Features
- Session login with cookie persistence.
- Real-time campaigns and progress from Kick API.
- Auto channel selection: online first, highest viewers first.
- Hidden worker while farming.
- Always-on auto-claim.
- Visual campaigns and rewards inventory.

## Quick Start (Dev)
```powershell
py -3 -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
python app/main.py
```

## Portable Builds (No Python Required)
- Windows: `build.bat`
- Linux: `build.sh`
- Output: `dist/`

## GitHub CI Builds
Go to `Actions` -> `Build Portable Apps`.

Artifacts:
- `KickDropsMiner-Windows-x64`
- `KickDropsMiner-Linux-x64`

## Runtime Files
- `kick_config.json`
- `cookies/kick.com.json`
- `chrome_data/`
- `cache/reward_thumbs/`
- `logs/app.log`

## Troubleshooting
```powershell
py -3 app/diagnose_env.py
```
