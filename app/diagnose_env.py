from __future__ import annotations

import json
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from kick_browser import KickBrowserClient, KickBrowserError


def run_cmd(cmd: list[str]) -> str:
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True, timeout=20)
        return out.strip()
    except Exception as exc:
        return f"ERROR: {exc}"


def check_browser(client: KickBrowserClient, mode: str) -> dict[str, Any]:
    driver = None
    try:
        if mode == "headless":
            driver = client.create_helper_driver(profile_name="diag-headless")
        elif mode == "offscreen":
            driver = client.create_offscreen_driver(profile_name="diag-offscreen")
        else:
            raise ValueError(mode)
        client.prime_session_with_cookies(driver)
        campaigns = client._fetch_response_in_page(
            driver,
            "https://web.kick.com/api/v1/drops/campaigns",
            headers={"Accept": "application/json"},
        )
        return {
            "ok": True,
            "status": campaigns.get("status"),
            "preview": str(campaigns.get("text") or "")[:160],
        }
    except Exception as exc:
        return {"ok": False, "error": f"{exc.__class__.__name__}: {exc}"}
    finally:
        if driver is not None:
            try:
                client.close_driver(driver)
            except Exception:
                pass


def main() -> None:
    base = Path(__file__).resolve().parent.parent
    client = KickBrowserClient(base)
    report: dict[str, Any] = {
        "python": run_cmd(["python", "--version"]),
        "platform": platform.platform(),
        "cookies_file_exists": client.has_saved_cookies(),
        "cookies_file": str(client.cookie_file),
        "session_status": client.get_session_status(),
        "check_headless_fetch": check_browser(client, "headless"),
        "check_offscreen_fetch": check_browser(client, "offscreen"),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
