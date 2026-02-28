from __future__ import annotations

import json
import time
import shutil
import logging
import sys
import importlib
import tempfile
import re
import socket
import subprocess
import webbrowser
import urllib.request
import urllib.parse
from pathlib import Path
from dataclasses import dataclass
from typing import Any


logger = logging.getLogger("KickDrops")


class KickBrowserError(RuntimeError):
    pass


def _compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _ensure_json(value: str, *, ctx: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise KickBrowserError(f"{ctx}: invalid JSON response: {value[:300]}") from exc
    if not isinstance(parsed, dict):
        raise KickBrowserError(f"{ctx}: unexpected payload type {type(parsed).__name__}")
    return parsed


@dataclass(slots=True)
class BrowserConfig:
    cookies_dir: Path
    chrome_data_dir: Path
    driver_log_dir: Path
    start_url: str = "https://kick.com"
    drops_campaigns_url: str = "https://web.kick.com/api/v1/drops/campaigns"
    drops_progress_url: str = "https://web.kick.com/api/v1/drops/progress"
    drops_inventory_url: str = "https://kick.com/drops/rewards"


class KickBrowserClient:
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.config = BrowserConfig(
            cookies_dir=base_dir / "cookies",
            chrome_data_dir=base_dir / "chrome_data",
            driver_log_dir=base_dir / "logs",
        )
        self.config.cookies_dir.mkdir(parents=True, exist_ok=True)
        self.config.chrome_data_dir.mkdir(parents=True, exist_ok=True)
        self.config.driver_log_dir.mkdir(parents=True, exist_ok=True)
        self.auth_profile_dir = self.config.chrome_data_dir / "kick-auth-profile"
        self.auth_profile_dir.mkdir(parents=True, exist_ok=True)
        self._last_cookie_apply_stats: tuple[int, int] | None = None
        self._prefer_offscreen_fetch = False
        self._offscreen_preference_logged = False
        self._last_identity_token: str = ""
        self._last_identity_info: dict[str, Any] = {}

    @staticmethod
    def _find_free_local_port() -> int:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        try:
            return int(sock.getsockname()[1])
        finally:
            sock.close()

    @staticmethod
    def _terminate_process(proc: subprocess.Popen) -> None:
        try:
            proc.terminate()
            proc.wait(timeout=5)
            return
        except Exception:
            pass
        try:
            proc.kill()
        except Exception:
            pass

    @staticmethod
    def _resolve_browser_binary(prefer: str = "chrome") -> str:
        prefer = (prefer or "chrome").strip().lower()
        candidates: list[Path] = []
        local_app = Path.home() / "AppData" / "Local"
        program_files = Path("C:/Program Files")
        program_files_x86 = Path("C:/Program Files (x86)")

        if prefer in {"chrome", "google-chrome"}:
            candidates.extend(
                [
                    local_app / "Google/Chrome/Application/chrome.exe",
                    program_files / "Google/Chrome/Application/chrome.exe",
                    program_files_x86 / "Google/Chrome/Application/chrome.exe",
                ]
            )
        if prefer in {"edge", "msedge"}:
            candidates.extend(
                [
                    local_app / "Microsoft/Edge/Application/msedge.exe",
                    program_files / "Microsoft/Edge/Application/msedge.exe",
                    program_files_x86 / "Microsoft/Edge/Application/msedge.exe",
                ]
            )
        if prefer not in {"edge", "msedge"}:
            candidates.extend(
                [
                    local_app / "Microsoft/Edge/Application/msedge.exe",
                    program_files / "Microsoft/Edge/Application/msedge.exe",
                    program_files_x86 / "Microsoft/Edge/Application/msedge.exe",
                ]
            )
        for candidate in candidates:
            if candidate.exists():
                return str(candidate)
        raise KickBrowserError("No Chrome/Edge binary found in standard paths.")

    def open_login_in_system_browser(self, *, browser_hint: str = "chrome") -> None:
        login_url = "https://kick.com/login"
        try:
            binary = self._resolve_browser_binary(browser_hint)
            subprocess.Popen([binary, "--new-window", login_url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except Exception:
            pass
        try:
            webbrowser.open(login_url)
        except Exception as exc:
            raise KickBrowserError(f"Failed opening login URL in system browser: {exc}") from exc

    @staticmethod
    def _load_json_url(url: str, *, timeout: float = 4.0) -> Any:
        import urllib.request

        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise KickBrowserError(f"Invalid JSON from {url}") from exc

    @staticmethod
    def _import_websocket_client_module():
        if getattr(sys, "frozen", False):
            ws_module = importlib.import_module("websocket")
            if not hasattr(ws_module, "create_connection"):
                raise ImportError(
                    f"Imported wrong websocket module: {getattr(ws_module, '__file__', '<unknown>')}"
                )
            return ws_module

        repo_dir_path = Path(__file__).resolve().parent

        def _norm_path(value: str) -> str:
            try:
                return str(Path(value).resolve()).rstrip("\\/").casefold()
            except Exception:
                return str(value).rstrip("\\/").casefold()

        repo_dir_norm = _norm_path(str(repo_dir_path))
        removed_entries: list[tuple[int, str]] = []
        for idx in range(len(sys.path) - 1, -1, -1):
            entry = sys.path[idx]
            if entry in ("", ".") or _norm_path(entry) == repo_dir_norm:
                removed_entries.append((idx, entry))
                sys.path.pop(idx)
        bad_websocket = sys.modules.get("websocket")
        if bad_websocket is not None:
            bad_file = str(getattr(bad_websocket, "__file__", "") or "")
            if bad_file and _norm_path(str(Path(bad_file).resolve().parent)) == repo_dir_norm:
                del sys.modules["websocket"]
        try:
            ws_module = importlib.import_module("websocket")
            if not hasattr(ws_module, "create_connection"):
                raise ImportError(
                    f"Imported wrong websocket module: {getattr(ws_module, '__file__', '<unknown>')}"
                )
            return ws_module
        finally:
            for idx, entry in sorted(removed_entries, key=lambda x: x[0]):
                sys.path.insert(idx, entry)

    @staticmethod
    def _is_disconnected_driver_exception(exc: Exception) -> bool:
        text = f"{exc.__class__.__name__}: {exc}".lower()
        tokens = (
            "invalid session id",
            "not connected to devtools",
            "disconnected: not connected to devtools",
            "session deleted as the browser has closed the connection",
            "connection refused",
            "failed to establish a new connection",
            "winerror 10061",
            "winerror 10054",
            "target window already closed",
            "chrome not reachable",
        )
        return any(token in text for token in tokens)

    @classmethod
    def _raise_if_driver_disconnected(cls, exc: Exception, *, action: str) -> None:
        if cls._is_disconnected_driver_exception(exc):
            raise KickBrowserError(f"Browser session ended unexpectedly while {action}.") from exc

    @staticmethod
    def _apply_stealth_patches(driver) -> None:
        # Best-effort anti-flakiness patching. This does not bypass captcha/2FA.
        source = r"""
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
window.chrome = window.chrome || { runtime: {} };
Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4] });
const originalQuery = window.navigator.permissions && window.navigator.permissions.query;
if (originalQuery) {
  window.navigator.permissions.query = (parameters) => (
    parameters && parameters.name === 'notifications'
      ? Promise.resolve({ state: Notification.permission })
      : originalQuery(parameters)
  );
}
"""
        try:
            driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": source})
        except Exception:
            pass

    @staticmethod
    def is_driver_alive(driver) -> bool:
        try:
            _ = driver.current_url
            return True
        except Exception:
            return False

    def _ensure_active_window(self, driver, *, action: str) -> None:
        try:
            handles = driver.window_handles
        except Exception as exc:
            self._raise_if_driver_disconnected(exc, action=action)
            raise KickBrowserError(f"Could not inspect browser windows while {action}.") from exc
        if not handles:
            raise KickBrowserError(f"Browser window closed unexpectedly while {action}.")
        try:
            current = driver.current_window_handle
        except Exception:
            current = None
        if current in handles:
            return
        try:
            driver.switch_to.window(handles[0])
        except Exception as exc:
            self._raise_if_driver_disconnected(exc, action=action)
            raise KickBrowserError(f"Could not switch to active browser window while {action}.") from exc

    @property
    def cookie_file(self) -> Path:
        return self.config.cookies_dir / "kick.com.json"

    def has_saved_cookies(self) -> bool:
        return self.cookie_file.exists()

    def get_saved_session_token(self) -> str | None:
        try:
            cookies = self._load_saved_cookies()
        except Exception:
            return None
        for cookie in cookies:
            if str(cookie.get("name") or "") != "session_token":
                continue
            token = str(cookie.get("value") or "").strip()
            if token:
                return token
        return None

    def save_driver_cookies(self, driver) -> int:
        cookies = driver.get_cookies()
        with self.cookie_file.open("w", encoding="utf-8") as f:
            json.dump(cookies, f, ensure_ascii=False, indent=2)
        logger.info("Saved %s cookies to %s", len(cookies), self.cookie_file)
        return len(cookies)

    def clear_saved_cookies(self) -> None:
        if self.cookie_file.exists():
            self.cookie_file.unlink()

    def import_browser_cookies(self, browser: str) -> int:
        try:
            import browser_cookie3  # type: ignore
        except Exception as exc:
            raise KickBrowserError(
                "browser_cookie3 is not installed. Install it to import cookies automatically."
            ) from exc

        browser = browser.lower().strip()
        loaders = {
            "chrome": browser_cookie3.chrome,
            "edge": browser_cookie3.edge,
            "firefox": browser_cookie3.firefox,
        }
        if browser not in loaders:
            raise KickBrowserError(f"Unsupported browser: {browser}")
        jar = loaders[browser](domain_name="kick.com")
        cookies: list[dict[str, Any]] = []
        for c in jar:
            if "kick.com" not in (c.domain or ""):
                continue
            cookie: dict[str, Any] = {
                "name": c.name,
                "value": c.value,
                "domain": c.domain,
                "path": c.path or "/",
                "secure": bool(c.secure),
                "httpOnly": False,
            }
            if c.expires:
                try:
                    cookie["expiry"] = int(c.expires)
                except Exception:
                    pass
            cookies.append(cookie)
        if not cookies:
            raise KickBrowserError("No Kick cookies found in selected browser profile")
        has_session = any(str(c.get("name") or "") == "session_token" and str(c.get("value") or "") for c in cookies)
        if not has_session:
            raise KickBrowserError("No Kick session_token found in selected browser profile")
        with self.cookie_file.open("w", encoding="utf-8") as f:
            json.dump(cookies, f, ensure_ascii=False, indent=2)
        logger.info("Imported %s cookies from %s", len(cookies), browser)
        return len(cookies)

    def _load_saved_cookies(self) -> list[dict[str, Any]]:
        if not self.cookie_file.exists():
            return []
        try:
            with self.cookie_file.open("r", encoding="utf-8") as f:
                cookies = json.load(f)
        except Exception as exc:
            raise KickBrowserError(f"Failed to read cookie file: {self.cookie_file}") from exc
        if not isinstance(cookies, list):
            raise KickBrowserError("Cookie file format is invalid")
        return [c for c in cookies if isinstance(c, dict)]

    def _build_driver(
        self,
        *,
        visible: bool = True,
        offscreen: bool = False,
        headless: bool = False,
        ephemeral_profile: bool = False,
        use_uc: bool | None = None,
        profile_name: str = "default",
    ):
        # Guard against local modules shadowing the third-party `websocket-client`
        # dependency required by Selenium (source run only).
        removed_entries: list[tuple[int, str]] = []
        if not getattr(sys, "frozen", False):
            repo_dir_path = Path(__file__).resolve().parent

            def _norm_path(value: str) -> str:
                try:
                    return str(Path(value).resolve()).rstrip("\\/").casefold()
                except Exception:
                    return str(value).rstrip("\\/").casefold()

            repo_dir_norm = _norm_path(str(repo_dir_path))
            for idx in range(len(sys.path) - 1, -1, -1):
                entry = sys.path[idx]
                if entry in ("", ".") or _norm_path(entry) == repo_dir_norm:
                    removed_entries.append((idx, entry))
                    sys.path.pop(idx)
            bad_websocket = sys.modules.get("websocket")
            if bad_websocket is not None:
                bad_file = str(getattr(bad_websocket, "__file__", "") or "")
                if bad_file and _norm_path(str(Path(bad_file).resolve().parent)) == repo_dir_norm:
                    del sys.modules["websocket"]
        try:
            # Preload the correct third-party package while repo path is hidden.
            ws_client = importlib.import_module("websocket")
            if not hasattr(ws_client, "WebSocketApp"):
                raise ImportError(
                    f"Imported wrong 'websocket' module: {getattr(ws_client, '__file__', '<unknown>')}"
                )
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
        except Exception as exc:
            raise KickBrowserError(
                f"Failed importing Selenium runtime ({exc.__class__.__name__}: {exc})"
            ) from exc
        finally:
            for idx, entry in sorted(removed_entries, key=lambda x: x[0]):
                sys.path.insert(idx, entry)

        options = Options()
        options.add_argument("--disable-notifications")
        options.add_argument("--disable-popup-blocking")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--mute-audio")
        options.add_argument("--lang=en-US")
        options.add_argument("--window-size=1280,800")
        options.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
        options.add_experimental_option("useAutomationExtension", False)
        temp_profile_dir: str | None = None
        if headless:
            # User-requested behavior: keep automation headless except login.
            # Kick may detect/block headless more often than visible/offscreen mode.
            options.add_argument("--headless=new")
            options.add_argument("--disable-gpu")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--remote-debugging-port=0")
            options.add_argument("--no-first-run")
            options.add_argument("--no-default-browser-check")
            options.add_argument("--disable-extensions")
            # Avoid persistent profile locks/corruption causing DevToolsActivePort failures.
            temp_profile_dir = tempfile.mkdtemp(prefix="kickminer-headless-", dir=str(self.config.chrome_data_dir))
            options.add_argument(f"--user-data-dir={temp_profile_dir}")
        elif not visible:
            # Non-headless hidden mode fallback (legacy behavior).
            offscreen = True
            options.add_argument("--window-position=-32000,-32000")
            options.add_argument("--start-minimized")
        elif ephemeral_profile:
            temp_profile_dir = tempfile.mkdtemp(prefix="kickminer-visible-", dir=str(self.config.chrome_data_dir))
            options.add_argument(f"--user-data-dir={temp_profile_dir}")
        else:
            profile_dir = self.config.chrome_data_dir / profile_name
            profile_dir.mkdir(parents=True, exist_ok=True)
            options.add_argument(f"--user-data-dir={profile_dir}")

        driver = None
        if use_uc is None:
            use_uc = not headless
        uc_error: Exception | None = None
        if use_uc:
            try:
                import undetected_chromedriver as uc  # type: ignore

                uc_options = uc.ChromeOptions()
                for arg in options.arguments:
                    uc_options.add_argument(arg)
                driver = uc.Chrome(options=uc_options, use_subprocess=True)
            except Exception as exc:
                uc_error = exc
                logger.debug("undetected_chromedriver unavailable/failing, falling back to selenium: %r", exc)

        if driver is None:
            try:
                driver = webdriver.Chrome(options=options)
            except Exception as exc:
                msg = "Failed to start Chrome driver"
                if uc_error is not None:
                    msg += f" | uc error: {uc_error!r}"
                msg += f" | selenium error: {exc!r}"
                raise KickBrowserError(msg) from exc

        self._apply_stealth_patches(driver)
        try:
            if offscreen and not headless:
                driver.set_window_position(-2400, -2400)
            elif not headless:
                driver.set_window_position(50, 50)
        except Exception:
            pass
        # Keep temp dir path on driver for cleanup when we quit.
        if temp_profile_dir is not None:
            try:
                setattr(driver, "_kick_temp_profile_dir", temp_profile_dir)
            except Exception:
                pass
        return driver

    def create_visible_driver(self, *, profile_name: str = "interactive"):
        return self._build_driver(
            visible=True, offscreen=False, headless=False, use_uc=True, profile_name=profile_name
        )

    def create_visible_login_driver(self, *, use_uc: bool = True, profile_name: str = "interactive-login-auto"):
        # Fresh profile per run avoids stale locks and inconsistent auth state.
        return self._build_driver(
            visible=True,
            offscreen=False,
            headless=False,
            ephemeral_profile=True,
            use_uc=use_uc,
            profile_name=profile_name,
        )

    def create_visible_driver_plain(self, *, profile_name: str = "interactive-plain"):
        return self._build_driver(
            visible=True,
            offscreen=False,
            headless=False,
            ephemeral_profile=False,
            use_uc=False,
            profile_name=profile_name,
        )

    def create_helper_driver(self, *, profile_name: str = "helper"):
        # Headless by default so background refreshes/workers don't steal focus.
        return self._build_driver(
            visible=False, offscreen=False, headless=True, use_uc=False, profile_name=profile_name
        )

    def create_offscreen_driver(self, *, profile_name: str = "helper-offscreen"):
        # Hidden fallback when headless is blocked by Kick/Cloudflare.
        return self._build_driver(
            visible=False, offscreen=True, headless=False, use_uc=False, profile_name=profile_name
        )

    def start_assisted_login_browser(self, *, browser_hint: str = "chrome") -> dict[str, Any]:
        browser_bin = self._resolve_browser_binary(browser_hint)
        port = self._find_free_local_port()
        profile_dir = self.auth_profile_dir
        profile_dir.mkdir(parents=True, exist_ok=True)
        args = [
            browser_bin,
            f"--remote-debugging-port={port}",
            "--remote-allow-origins=*",
            "--new-window",
            "https://kick.com/login",
            f"--user-data-dir={profile_dir}",
            "--window-size=1280,900",
            "--disable-notifications",
            "--disable-popup-blocking",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-search-engine-choice-screen",
            "--disable-features=ChromeWhatsNewUI,OptimizationGuideModelDownloading",
            "--password-store=basic",
            "--disable-sync",
            "--disable-extensions",
        ]
        try:
            proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as exc:
            raise KickBrowserError(f"Failed launching login browser: {exc}") from exc

        deadline = time.time() + 20.0
        seen_early_exit = False
        while time.time() < deadline:
            try:
                version = self._load_json_url(f"http://127.0.0.1:{port}/json/version", timeout=2.0)
                if isinstance(version, dict):
                    return {
                        "process": proc,
                        "port": port,
                        "profile_dir": str(profile_dir),
                        "temporary_profile": False,
                        "browser_hint": browser_hint,
                    }
            except Exception:
                pass
            if proc.poll() is not None:
                seen_early_exit = True
            time.sleep(0.4)
        self._terminate_process(proc)
        if seen_early_exit:
            raise KickBrowserError(
                "Login browser closed immediately. Close all Chrome/Edge windows and retry."
            )
        raise KickBrowserError("Login browser did not expose DevTools endpoint in time.")

    def stop_assisted_login_browser(self, ctx: dict[str, Any]) -> None:
        port = int(ctx.get("port") or 0)
        if port > 0:
            try:
                self._close_cdp_browser(port)
            except Exception:
                pass
        proc = ctx.get("process")
        if isinstance(proc, subprocess.Popen):
            self._terminate_process(proc)
        profile_dir = str(ctx.get("profile_dir") or "").strip()
        if profile_dir and bool(ctx.get("temporary_profile", False)):
            shutil.rmtree(profile_dir, ignore_errors=True)

    def _cdp_send(self, ws, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = {"id": int(time.time() * 1000) % 1_000_000_000, "method": method}
        if params:
            payload["params"] = params
        ws.send(json.dumps(payload))
        deadline = time.time() + 6.0
        while time.time() < deadline:
            raw = ws.recv()
            if not raw:
                continue
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            if not isinstance(msg, dict):
                continue
            if int(msg.get("id") or -1) == int(payload["id"]):
                if "error" in msg:
                    raise KickBrowserError(f"CDP {method} failed: {msg.get('error')}")
                result = msg.get("result")
                return result if isinstance(result, dict) else {}
        raise KickBrowserError(f"CDP {method} timeout.")

    def _close_cdp_browser(self, port: int) -> None:
        version = self._load_json_url(f"http://127.0.0.1:{port}/json/version", timeout=2.0)
        if not isinstance(version, dict):
            return
        ws_url = str(version.get("webSocketDebuggerUrl") or "")
        if not ws_url:
            return
        ws_module = self._import_websocket_client_module()
        ws = None
        try:
            ws = ws_module.create_connection(ws_url, timeout=3)
            try:
                self._cdp_send(ws, "Browser.close")
            except Exception:
                pass
        finally:
            if ws is not None:
                try:
                    ws.close()
                except Exception:
                    pass

    def _get_kick_cookies_via_cdp(self, port: int) -> list[dict[str, Any]]:
        version = self._load_json_url(f"http://127.0.0.1:{port}/json/version", timeout=3.0)
        if not isinstance(version, dict):
            raise KickBrowserError("DevTools version endpoint is invalid.")
        ws_url = str(version.get("webSocketDebuggerUrl") or "")
        if not ws_url:
            raise KickBrowserError("DevTools browser target has no websocket URL.")

        ws_module = self._import_websocket_client_module()
        ws = None
        try:
            ws = ws_module.create_connection(ws_url, timeout=6)
            raw_cookies: Any = None
            try:
                result = self._cdp_send(ws, "Storage.getCookies")
                raw_cookies = result.get("cookies")
            except Exception:
                raw_cookies = None
            if not isinstance(raw_cookies, list):
                self._cdp_send(ws, "Network.enable")
                result = self._cdp_send(ws, "Network.getAllCookies")
                raw_cookies = result.get("cookies")
            if not isinstance(raw_cookies, list):
                return []
            out: list[dict[str, Any]] = []
            for cookie in raw_cookies:
                if not isinstance(cookie, dict):
                    continue
                domain = str(cookie.get("domain") or "")
                if "kick.com" not in domain:
                    continue
                item: dict[str, Any] = {
                    "name": str(cookie.get("name") or ""),
                    "value": str(cookie.get("value") or ""),
                    "domain": domain,
                    "path": str(cookie.get("path") or "/"),
                    "secure": bool(cookie.get("secure", False)),
                    "httpOnly": bool(cookie.get("httpOnly", False)),
                }
                expires = cookie.get("expires")
                try:
                    exp = int(float(expires))
                    if exp > 0:
                        item["expiry"] = exp
                except Exception:
                    pass
                if item["name"]:
                    out.append(item)
            return out
        finally:
            if ws is not None:
                try:
                    ws.close()
                except Exception:
                    pass

    def wait_for_assisted_login_session(
        self,
        ctx: dict[str, Any],
        *,
        timeout_seconds: float = 600.0,
    ) -> dict[str, Any]:
        port = int(ctx.get("port") or 0)
        if port <= 0:
            raise KickBrowserError("Invalid assisted login context (missing port).")
        deadline = time.time() + max(30.0, float(timeout_seconds))
        last_hint = "sin cookies todavia"
        while time.time() < deadline:
            cookies = self._get_kick_cookies_via_cdp(port)
            if cookies:
                with self.cookie_file.open("w", encoding="utf-8") as f:
                    json.dump(cookies, f, ensure_ascii=False, indent=2)
                token = None
                for cookie in cookies:
                    if str(cookie.get("name") or "") == "session_token":
                        token = str(cookie.get("value") or "")
                        break
                if token:
                    return {
                        "state": "logged_in",
                        "label": "Session cookie detected",
                        "cookies_count": len(cookies),
                    }
                last_hint = f"cookies detectadas ({len(cookies)}), falta session_token"
            else:
                last_hint = "No Kick cookies found in assisted browser yet"
            time.sleep(3.0)
        raise KickBrowserError(f"Timeout waiting assisted login session: {last_hint}")

    def close_driver(self, driver) -> None:
        temp_profile_dir = getattr(driver, "_kick_temp_profile_dir", None)
        try:
            driver.quit()
        finally:
            if temp_profile_dir:
                shutil.rmtree(str(temp_profile_dir), ignore_errors=True)

    @staticmethod
    def _is_headless_fetch_failure(exc: Exception) -> bool:
        text = str(exc)
        if not isinstance(exc, KickBrowserError):
            return False
        lowered = text.lower()
        return (
            "failed to fetch" in lowered
            or "request blocked by security policy" in lowered
            or "http 403" in lowered
        )

    def _fetch_modes(self) -> tuple[str, ...]:
        if self._prefer_offscreen_fetch:
            return ("offscreen",)
        return ("headless", "offscreen")

    def fetch_image_bytes_fast(self, url: str, *, timeout_seconds: float = 12.0) -> bytes:
        target_url = str(url or "").strip()
        if not target_url:
            raise KickBrowserError("Image URL is empty.")

        # Primary path: TLS/browser fingerprint impersonation without launching any browser window.
        try:
            from curl_cffi import requests as curl_requests  # type: ignore

            resp = curl_requests.get(
                target_url,
                impersonate="chrome131",
                timeout=max(3, int(timeout_seconds)),
                headers={
                    "Referer": "https://kick.com/",
                    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
                },
            )
            if int(resp.status_code) >= 400:
                raise KickBrowserError(f"HTTP {resp.status_code}")
            return bytes(resp.content)
        except Exception as exc:
            # Secondary path: plain urllib (may still work for non-protected assets).
            try:
                req = urllib.request.Request(
                    target_url,
                    headers={
                        "User-Agent": "Mozilla/5.0",
                        "Referer": "https://kick.com/",
                    },
                )
                with urllib.request.urlopen(req, timeout=max(3, int(timeout_seconds))) as resp:
                    return resp.read()
            except Exception as exc2:
                raise KickBrowserError(
                    f"HTTP image fetch failed for {target_url}: {exc.__class__.__name__}: {exc} | "
                    f"fallback: {exc2.__class__.__name__}: {exc2}"
                ) from exc2

    # Backward-compatible alias name kept intentionally.
    def fetch_image_bytes_via_offscreen(self, url: str, *, timeout_seconds: float = 20.0) -> bytes:
        return self.fetch_image_bytes_fast(url, timeout_seconds=timeout_seconds)

    def close_thumb_fetcher(self) -> None:
        # No-op: image fetching no longer uses Selenium/browser instances.
        return

    @staticmethod
    def _extract_session_user_from_local_storage(driver) -> dict[str, Any] | None:
        try:
            keys = driver.execute_script("return Object.keys(localStorage);")
        except Exception:
            return None
        if not isinstance(keys, list):
            return None
        # Example key seen in Kick web:
        # @fpjs@client@__{"type":"session","authStatus":"authenticated","username":"foo","userId":123}__"123"__false
        pattern = re.compile(
            r'authStatus":"(?P<status>authenticated|unauthenticated)".*?'
            r'(?:username":"(?P<username>[^"]+)")?.*?(?:userId":(?P<user_id>\d+))?',
            re.IGNORECASE,
        )
        for key in keys:
            if not isinstance(key, str):
                continue
            if "@fpjs@client@" not in key or '"type":"session"' not in key:
                continue
            m = pattern.search(key)
            if not m:
                continue
            status = (m.group("status") or "").lower()
            username = m.group("username")
            user_id_raw = m.group("user_id")
            user_id = int(user_id_raw) if user_id_raw and user_id_raw.isdigit() else None
            return {
                "auth_status": status,
                "username": username,
                "user_id": user_id,
                "source": "localStorage",
            }
        return None

    def prime_session_with_cookies(self, driver) -> None:
        driver.get(self.config.start_url)
        time.sleep(1.0)
        cookies = self._load_saved_cookies()
        if not cookies:
            return
        added = 0
        for cookie in cookies:
            c = dict(cookie)
            if c.get("expiry") is None:
                c.pop("expiry", None)
            try:
                driver.add_cookie(c)
                added += 1
            except Exception:
                continue
        if added:
            driver.refresh()
            time.sleep(1.0)
        stats = (added, len(cookies))
        if stats != self._last_cookie_apply_stats:
            self._last_cookie_apply_stats = stats
            logger.info("Applied %s/%s cookies", added, len(cookies))

    def get_session_token_from_driver(self, driver) -> str | None:
        try:
            for c in driver.get_cookies():
                if c.get("name") == "session_token":
                    return str(c.get("value") or "")
        except Exception as exc:
            self._raise_if_driver_disconnected(exc, action="reading session cookies")
            return None
        return None

    @staticmethod
    def _find_first_interactable(driver, selectors: list[str]):
        for selector in selectors:
            try:
                elements = driver.find_elements("css selector", selector)
            except Exception as exc:
                KickBrowserClient._raise_if_driver_disconnected(exc, action="searching login fields")
                continue
            for element in elements:
                try:
                    if element.is_displayed() and element.is_enabled():
                        return element
                except Exception as exc:
                    KickBrowserClient._raise_if_driver_disconnected(exc, action="reading login field")
                    continue
        return None

    def _find_login_inputs(self, driver):
        user_selectors = [
            "input[name='email']",
            "input[id='email']",
            "input[type='email']",
            "input[name='username']",
            "input[id='username']",
            "input[name='login']",
            "input[autocomplete='username']",
            "input[autocomplete='email']",
            "input[data-testid*='email']",
            "input[data-testid*='username']",
        ]
        pass_selectors = [
            "input[name='password']",
            "input[id='password']",
            "input[type='password']",
            "input[autocomplete='current-password']",
            "input[data-testid*='password']",
        ]
        user_input = self._find_first_interactable(driver, user_selectors)
        pass_input = self._find_first_interactable(driver, pass_selectors)
        if user_input is None:
            try:
                generic_inputs = driver.find_elements("css selector", "input")
            except Exception as exc:
                self._raise_if_driver_disconnected(exc, action="listing login inputs")
                generic_inputs = []
            for element in generic_inputs:
                try:
                    if not (element.is_displayed() and element.is_enabled()):
                        continue
                    input_type = str(element.get_attribute("type") or "").strip().lower()
                    if input_type in {"hidden", "password", "checkbox", "radio", "submit", "button"}:
                        continue
                    user_input = element
                    break
                except Exception as exc:
                    self._raise_if_driver_disconnected(exc, action="reading login input attributes")
                    continue
        return user_input, pass_input

    def _find_login_inputs_any_frame(self, driver):
        self._ensure_active_window(driver, action="searching login fields")
        driver.switch_to.default_content()
        user_input, pass_input = self._find_login_inputs(driver)
        if pass_input is not None:
            return user_input, pass_input

        frames = []
        try:
            frames = driver.find_elements("css selector", "iframe,frame")
        except Exception as exc:
            self._raise_if_driver_disconnected(exc, action="listing iframes")
            return user_input, pass_input
        for frame in frames:
            try:
                driver.switch_to.default_content()
                driver.switch_to.frame(frame)
                frame_user, frame_pass = self._find_login_inputs(driver)
                if frame_pass is not None:
                    return frame_user, frame_pass
            except Exception as exc:
                self._raise_if_driver_disconnected(exc, action="switching login iframe")
                continue
        driver.switch_to.default_content()
        return user_input, pass_input

    @staticmethod
    def _click_login_entrypoint(driver) -> bool:
        script = """
return (() => {
  const tokens = ["log in", "login", "sign in", "iniciar sesion", "iniciar sesión", "acceder"];
  const nodes = Array.from(document.querySelectorAll("a,button,[role='button']"));
  for (const n of nodes) {
    const text = (n.innerText || n.textContent || "").trim().toLowerCase();
    if (!text) continue;
    if (!tokens.some((t) => text.includes(t))) continue;
    try { n.click(); return true; } catch (_) {}
  }
  return false;
})();
"""
        try:
            return bool(driver.execute_script(script))
        except Exception as exc:
            KickBrowserClient._raise_if_driver_disconnected(exc, action="clicking login entrypoint")
            return False

    @staticmethod
    def _set_input_value(driver, element, value: str) -> None:
        try:
            element.click()
        except Exception as exc:
            KickBrowserClient._raise_if_driver_disconnected(exc, action="focusing login input")
            pass
        try:
            element.clear()
        except Exception as exc:
            KickBrowserClient._raise_if_driver_disconnected(exc, action="clearing login input")
            pass
        try:
            element.send_keys(value)
            current = str(element.get_attribute("value") or "")
            if current.strip() == value.strip():
                return
        except Exception as exc:
            KickBrowserClient._raise_if_driver_disconnected(exc, action="typing login input")
            pass
        script = """
const el = arguments[0];
const value = arguments[1];
el.focus();
el.value = "";
el.dispatchEvent(new Event("input", { bubbles: true }));
el.value = value;
el.dispatchEvent(new Event("input", { bubbles: true }));
el.dispatchEvent(new Event("change", { bubbles: true }));
"""
        try:
            driver.execute_script(script, element, value)
        except Exception as exc:
            KickBrowserClient._raise_if_driver_disconnected(exc, action="setting login input value")
            raise KickBrowserError(f"Failed setting login input value: {exc}") from exc

    def _submit_login_form(self, driver, password_input) -> bool:
        submit_selectors = [
            "button[type='submit']",
            "input[type='submit']",
            "button[data-testid*='submit']",
            "button[data-testid*='login']",
        ]
        submit_btn = self._find_first_interactable(driver, submit_selectors)
        if submit_btn is not None:
            try:
                submit_btn.click()
                return True
            except Exception as exc:
                self._raise_if_driver_disconnected(exc, action="clicking login submit button")
                pass
        script = """
return (() => {
  const tokens = ["log in", "login", "sign in", "iniciar sesion", "iniciar sesión", "acceder"];
  const nodes = Array.from(document.querySelectorAll("button,[role='button'],input[type='button']"));
  for (const n of nodes) {
    const text = (n.innerText || n.textContent || n.value || "").trim().toLowerCase();
    if (!text) continue;
    if (!tokens.some((t) => text.includes(t))) continue;
    try { n.click(); return true; } catch (_) {}
  }
  return false;
})();
"""
        try:
            if bool(driver.execute_script(script)):
                return True
        except Exception as exc:
            self._raise_if_driver_disconnected(exc, action="submitting login form with script")
            pass
        try:
            password_input.send_keys("\n")
            return True
        except Exception as exc:
            self._raise_if_driver_disconnected(exc, action="submitting login form with Enter key")
            return False

    @staticmethod
    def _detect_login_error_text(driver) -> str | None:
        try:
            body = driver.find_element("tag name", "body")
            text = str(body.text or "").strip().lower()
        except Exception as exc:
            KickBrowserClient._raise_if_driver_disconnected(exc, action="reading login page errors")
            return None
        if not text:
            return None
        invalid_tokens = [
            "invalid credentials",
            "incorrect password",
            "invalid email",
            "wrong password",
            "email or password is incorrect",
            "credenciales invalidas",
            "credenciales inválidas",
            "usuario o contraseña",
            "contrasena incorrecta",
            "contraseña incorrecta",
        ]
        for token in invalid_tokens:
            if token in text:
                return "Login failed: invalid username/email or password."
        blocked_tokens = [
            "too many attempts",
            "try again later",
            "temporarily blocked",
            "demasiados intentos",
        ]
        for token in blocked_tokens:
            if token in text:
                return "Login blocked temporarily due to too many attempts."
        return None

    @staticmethod
    def _detect_login_error_text_enhanced(driver) -> str | None:
        base = KickBrowserClient._detect_login_error_text(driver)
        if base:
            return base
        try:
            body = driver.find_element("tag name", "body")
            text = str(body.text or "").strip().lower()
        except Exception as exc:
            KickBrowserClient._raise_if_driver_disconnected(
                exc, action="reading enhanced login error text"
            )
            return None
        if not text:
            return None
        unknown_tokens = (
            "unknown error",
            "something went wrong",
            "error desconocido",
        )
        for token in unknown_tokens:
            if token in text:
                return "Kick login returned an unknown error (possible anti-bot/captcha challenge)."
        rate_limit_tokens = (
            "too many requests",
            "429",
            "rate limit",
            "demasiadas solicitudes",
        )
        for token in rate_limit_tokens:
            if token in text:
                return "Kick rate-limited login (HTTP 429 Too Many Requests)."
        return None

    @staticmethod
    def _looks_like_challenge(driver) -> bool:
        checks = []
        try:
            checks.append(str(driver.current_url or "").lower())
        except Exception as exc:
            KickBrowserClient._raise_if_driver_disconnected(exc, action="reading login current URL")
            pass
        try:
            body = driver.find_element("tag name", "body")
            checks.append(str(body.text or "").lower())
        except Exception as exc:
            KickBrowserClient._raise_if_driver_disconnected(exc, action="reading login page body")
            pass
        text = "\n".join(checks)
        if not text:
            return False
        challenge_tokens = [
            "captcha",
            "cloudflare",
            "verify you are human",
            "verification code",
            "two-factor",
            "2fa",
            "authenticator app",
            "codigo de verificacion",
            "código de verificación",
        ]
        return any(token in text for token in challenge_tokens)

    def _extract_authenticated_identity(self, driver) -> dict[str, Any] | None:
        session_token = self.get_session_token_from_driver(driver)
        if not session_token:
            return None
        ls_session = self._extract_session_user_from_local_storage(driver)
        if isinstance(ls_session, dict):
            auth_status = str(ls_session.get("auth_status") or "").lower()
            if auth_status == "unauthenticated":
                return None
            username = str(ls_session.get("username") or "").strip()
            user_id = ls_session.get("user_id")
            if auth_status == "authenticated":
                return {
                    "username": username or None,
                    "user_id": user_id,
                    "source": "localStorage",
                }
        return {"username": None, "user_id": None, "source": "cookie"}

    def _reset_login_surface(self, driver) -> None:
        try:
            driver.delete_all_cookies()
        except Exception as exc:
            self._raise_if_driver_disconnected(exc, action="clearing browser cookies")
        try:
            driver.get(self.config.start_url)
            driver.execute_script(
                "try { localStorage.clear(); sessionStorage.clear(); } catch (_) {}"
            )
        except Exception as exc:
            self._raise_if_driver_disconnected(exc, action="resetting browser storage")

    @staticmethod
    def _init_login_network_monitor(driver) -> None:
        script = r"""
(() => {
  if (window.__kickminerLoginMonitorInitialized) return;
  window.__kickminerLoginMonitorInitialized = true;
  window.__kickminerLoginMonitor = { events: [] };
  const push = (item) => {
    try {
      const events = window.__kickminerLoginMonitor.events;
      events.push(item);
      if (events.length > 30) events.splice(0, events.length - 30);
    } catch (_) {}
  };
  const isLoginUrl = (url) => {
    const u = String(url || '').toLowerCase();
    return u.includes('/mobile/login') || u.includes('/api') && u.includes('login');
  };

  const originalFetch = window.fetch;
  if (typeof originalFetch === 'function') {
    window.fetch = function (...args) {
      const url = args[0];
      return originalFetch.apply(this, args).then((resp) => {
        try {
          if (isLoginUrl(url)) {
            push({
              ts: Date.now(),
              kind: 'fetch',
              url: String(url || ''),
              status: Number(resp && resp.status || 0),
            });
          }
        } catch (_) {}
        return resp;
      }).catch((err) => {
        try {
          if (isLoginUrl(url)) {
            push({
              ts: Date.now(),
              kind: 'fetch',
              url: String(url || ''),
              status: 0,
              error: String(err || ''),
            });
          }
        } catch (_) {}
        throw err;
      });
    };
  }

  const OriginalXHR = window.XMLHttpRequest;
  if (OriginalXHR && OriginalXHR.prototype) {
    const open = OriginalXHR.prototype.open;
    const send = OriginalXHR.prototype.send;
    OriginalXHR.prototype.open = function(method, url, ...rest) {
      this.__kickminerLoginUrl = String(url || '');
      return open.call(this, method, url, ...rest);
    };
    OriginalXHR.prototype.send = function(...args) {
      this.addEventListener('loadend', () => {
        try {
          const url = String(this.__kickminerLoginUrl || '');
          if (!isLoginUrl(url)) return;
          push({
            ts: Date.now(),
            kind: 'xhr',
            url,
            status: Number(this.status || 0),
          });
        } catch (_) {}
      });
      return send.apply(this, args);
    };
  }
})();
"""
        try:
            driver.execute_script(script)
        except Exception as exc:
            KickBrowserClient._raise_if_driver_disconnected(exc, action="initializing login network monitor")

    @staticmethod
    def _read_login_network_events(driver) -> list[dict[str, Any]]:
        script = """
return (window.__kickminerLoginMonitor && Array.isArray(window.__kickminerLoginMonitor.events))
  ? window.__kickminerLoginMonitor.events
  : [];
"""
        try:
            events = driver.execute_script(script)
        except Exception as exc:
            KickBrowserClient._raise_if_driver_disconnected(exc, action="reading login network monitor")
            return []
        if not isinstance(events, list):
            return []
        out: list[dict[str, Any]] = []
        for event in events:
            if isinstance(event, dict):
                out.append(event)
        return out

    @classmethod
    def _detect_login_network_error(cls, driver) -> str | None:
        events = cls._read_login_network_events(driver)
        if not events:
            return None
        recent = events[-8:]
        for event in reversed(recent):
            status = int(event.get("status") or 0)
            if status == 429:
                return "Kick rate-limited login (HTTP 429 Too Many Requests)."
            if status in (401, 403):
                return "Kick rejected login request (HTTP 401/403). Check credentials or anti-bot challenge."
        return None

    def _wait_for_login_success(
        self,
        driver,
        *,
        timeout_seconds: float = 180.0,
    ) -> dict[str, Any]:
        deadline = time.time() + max(10.0, float(timeout_seconds))
        challenge_seen = False
        while time.time() < deadline:
            self._ensure_active_window(driver, action="waiting for login result")
            try:
                driver.switch_to.default_content()
            except Exception as exc:
                self._raise_if_driver_disconnected(exc, action="switching to main document during login wait")
            identity = self._extract_authenticated_identity(driver)
            if isinstance(identity, dict):
                return identity
            network_error = self._detect_login_network_error(driver)
            if network_error:
                raise KickBrowserError(network_error)
            error_text = self._detect_login_error_text_enhanced(driver)
            if error_text:
                raise KickBrowserError(error_text)
            if self._looks_like_challenge(driver):
                challenge_seen = True
            time.sleep(0.8)
        if challenge_seen:
            raise KickBrowserError(
                "Login not completed in time (captcha/2FA detected). Complete it in the open browser and then save cookies."
            )
        raise KickBrowserError("Automatic login timed out without creating a valid session.")

    def login_with_credentials_on_driver(
        self,
        driver,
        *,
        username: str,
        password: str,
        timeout_seconds: float = 180.0,
    ) -> dict[str, Any]:
        username = username.strip()
        if not username:
            raise KickBrowserError("Username/email is required.")
        if not password:
            raise KickBrowserError("Password is required.")

        self._reset_login_surface(driver)
        existing_identity = self._extract_authenticated_identity(driver)
        if isinstance(existing_identity, dict):
            existing_name = str(existing_identity.get("username") or "").strip()
            return {
                "state": "logged_in",
                "label": f"Session already active: {existing_name}" if existing_name else "Session already active",
                "username": existing_name or None,
                "user_id": existing_identity.get("user_id"),
                "source": existing_identity.get("source"),
            }

        user_input = None
        pass_input = None
        login_urls = ("https://kick.com/login", "https://www.kick.com/login")
        for url in login_urls:
            try:
                driver.get(url)
            except Exception as exc:
                self._raise_if_driver_disconnected(exc, action="opening Kick login URL")
                continue
            time.sleep(1.5)
            user_input, pass_input = self._find_login_inputs_any_frame(driver)
            if pass_input is not None:
                break

        if pass_input is None:
            try:
                driver.switch_to.default_content()
                driver.get(self.config.start_url)
            except Exception as exc:
                self._raise_if_driver_disconnected(exc, action="opening Kick homepage for login fallback")
                raise
            time.sleep(1.2)
            self._click_login_entrypoint(driver)
            for _ in range(12):
                time.sleep(0.7)
                user_input, pass_input = self._find_login_inputs_any_frame(driver)
                if pass_input is not None:
                    break

        if pass_input is None:
            if self._looks_like_challenge(driver):
                raise KickBrowserError(
                    "Kick requested captcha/verification before showing login form."
                )
            current_url = ""
            title = ""
            frames_count = 0
            try:
                current_url = str(driver.current_url or "")
            except Exception:
                pass
            try:
                title = str(driver.title or "")
            except Exception:
                pass
            try:
                frames_count = len(driver.find_elements("css selector", "iframe,frame"))
            except Exception:
                pass
            raise KickBrowserError(
                "Could not locate Kick login form fields "
                f"(url={current_url or 'n/a'}, title={title or 'n/a'}, iframes={frames_count})."
            )
        if user_input is None:
            raise KickBrowserError("Could not locate the username/email input field.")

        self._init_login_network_monitor(driver)
        self._set_input_value(driver, user_input, username)
        self._set_input_value(driver, pass_input, password)
        if not self._submit_login_form(driver, pass_input):
            raise KickBrowserError("Could not submit Kick login form.")

        identity = self._wait_for_login_success(driver, timeout_seconds=timeout_seconds)
        username_out = str(identity.get("username") or "").strip()
        label = f"Session started: {username_out}" if username_out else "Session started"
        return {
            "state": "logged_in",
            "label": label,
            "username": username_out or None,
            "user_id": identity.get("user_id"),
            "source": identity.get("source"),
        }

    def login_with_credentials(
        self,
        *,
        username: str,
        password: str,
        timeout_seconds: float = 180.0,
    ) -> dict[str, Any]:
        driver = None
        try:
            driver = self.create_visible_driver(profile_name="interactive-login-auto")
            self.prime_session_with_cookies(driver)
            info = self.login_with_credentials_on_driver(
                driver,
                username=username,
                password=password,
                timeout_seconds=timeout_seconds,
            )
            cookies_saved = self.save_driver_cookies(driver)
            info["cookies_saved"] = cookies_saved
            return info
        finally:
            if driver is not None:
                try:
                    self.close_driver(driver)
                except Exception:
                    pass

    def open_login_page(self, driver) -> None:
        self._ensure_active_window(driver, action="opening Kick login page")
        driver.get("https://kick.com/login")
        self._init_login_network_monitor(driver)

    def wait_for_manual_login_on_driver(
        self,
        driver,
        *,
        timeout_seconds: float = 480.0,
        expected_username: str | None = None,
    ) -> dict[str, Any]:
        expected_username_norm = (expected_username or "").strip().casefold()
        deadline = time.time() + max(30.0, float(timeout_seconds))
        last_hint: str | None = None
        challenge_seen = False
        while time.time() < deadline:
            self._ensure_active_window(driver, action="waiting for manual login")
            try:
                driver.switch_to.default_content()
            except Exception as exc:
                self._raise_if_driver_disconnected(exc, action="switching to main document in manual login wait")
            identity = self._extract_authenticated_identity(driver)
            if isinstance(identity, dict):
                username = str(identity.get("username") or "").strip()
                if expected_username_norm and username and username.casefold() != expected_username_norm:
                    return {
                        "state": "logged_in",
                        "label": f"Session started as {username} (different from expected user)",
                        "username": username,
                        "user_id": identity.get("user_id"),
                        "source": identity.get("source"),
                    }
                return {
                    "state": "logged_in",
                    "label": f"Session started: {username}" if username else "Session started",
                    "username": username or None,
                    "user_id": identity.get("user_id"),
                    "source": identity.get("source"),
                }
            network_error = self._detect_login_network_error(driver)
            if network_error:
                last_hint = network_error
            if self._looks_like_challenge(driver):
                challenge_seen = True
            time.sleep(0.8)
        if last_hint:
            raise KickBrowserError(
                f"Manual login not completed in time ({last_hint}). Wait a bit and retry."
            )
        if challenge_seen:
            raise KickBrowserError(
                "Manual login not completed in time (captcha/verification still pending)."
            )
        raise KickBrowserError("Manual login timed out without a valid session.")

    def can_continue_login_manually(self, driver) -> bool:
        if not self.is_driver_alive(driver):
            return False
        try:
            if self._looks_like_challenge(driver):
                return True
            driver.switch_to.default_content()
            user_input, pass_input = self._find_login_inputs_any_frame(driver)
            return pass_input is not None or user_input is not None
        except Exception:
            return False

    def _fetch_json_in_page(
        self,
        driver,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        method: str = "GET",
        credentials_include: bool = True,
    ) -> dict[str, Any]:
        result = self._fetch_response_in_page(
            driver,
            url,
            headers=headers,
            method=method,
            credentials_include=credentials_include,
        )
        status = int(result.get("status") or 0)
        text = str(result.get("text") or "")
        if status == 403 and "blocked" in text.lower():
            raise KickBrowserError(
                f"Kick blocked the request (403 security policy) for {url}. Try logging in again."
            )
        if status >= 400 and not text:
            raise KickBrowserError(f"HTTP {status} for {url}")
        payload = result.get("json")
        if not isinstance(payload, dict):
            raise KickBrowserError(f"Unexpected JSON payload from {url}")
        return payload

    def _fetch_response_in_page(
        self,
        driver,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        method: str = "GET",
        credentials_include: bool = True,
    ) -> dict[str, Any]:
        # Running fetch in page context avoids the direct 403 that happens for raw HTTP clients.
        script = """
const done = arguments[arguments.length - 1];
const url = arguments[0];
const method = arguments[1];
const headers = arguments[2] || {};
const includeCreds = arguments[3];
fetch(url, {
  method,
  headers,
  credentials: includeCreds ? "include" : "same-origin",
})
  .then(async (resp) => {
    const text = await resp.text();
    done(JSON.stringify({
      ok: resp.ok,
      status: resp.status,
      statusText: resp.statusText,
      text
    }));
  })
  .catch((err) => done(JSON.stringify({ ok: false, status: 0, error: String(err) })));
"""
        raw = driver.execute_async_script(script, url, method, headers or {}, credentials_include)
        wrapper = _ensure_json(str(raw), ctx=f"fetch({url}) wrapper")
        if "error" in wrapper:
            raise KickBrowserError(f"fetch({url}) failed: {wrapper['error']}")
        status = int(wrapper.get("status") or 0)
        text = str(wrapper.get("text") or "")
        payload: dict[str, Any] | None = None
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                payload = parsed
        except json.JSONDecodeError:
            payload = None
        return {
            "ok": bool(wrapper.get("ok", False)),
            "status": status,
            "statusText": str(wrapper.get("statusText") or ""),
            "text": text,
            "json": payload,
        }

    def _http_cookie_dict(self) -> dict[str, str]:
        cookies: dict[str, str] = {}
        for cookie in self._load_saved_cookies():
            name = str(cookie.get("name") or "").strip()
            if not name:
                continue
            cookies[name] = str(cookie.get("value") or "")
        return cookies

    def _http_fetch_response(
        self,
        url: str,
        *,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        auth_bearer: bool = False,
        timeout_seconds: float = 20.0,
    ) -> dict[str, Any]:
        try:
            from curl_cffi import requests as curl_requests  # type: ignore
        except Exception as exc:
            raise KickBrowserError(
                "curl_cffi is required for HTTP API calls. Install dependencies from requirements.txt."
            ) from exc

        req_headers = {
            "Accept": "application/json",
            "Referer": "https://kick.com/",
        }
        if headers:
            req_headers.update({str(k): str(v) for k, v in headers.items()})
        if auth_bearer:
            token = self.get_saved_session_token()
            if token:
                req_headers["Authorization"] = f"Bearer {token}"

        resp = curl_requests.request(
            method,
            url,
            impersonate="chrome131",
            headers=req_headers,
            cookies=self._http_cookie_dict(),
            timeout=max(3, int(timeout_seconds)),
            allow_redirects=True,
        )
        text = str(resp.text or "")
        payload: dict[str, Any] | None = None
        try:
            parsed = resp.json()
            if isinstance(parsed, dict):
                payload = parsed
        except Exception:
            payload = None
        return {
            "status": int(resp.status_code),
            "text": text,
            "json": payload,
        }

    def _http_fetch_json(
        self,
        url: str,
        *,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        auth_bearer: bool = False,
        timeout_seconds: float = 20.0,
    ) -> dict[str, Any]:
        result = self._http_fetch_response(
            url,
            method=method,
            headers=headers,
            auth_bearer=auth_bearer,
            timeout_seconds=timeout_seconds,
        )
        status = int(result.get("status") or 0)
        text = str(result.get("text") or "")
        if status == 403 and "blocked" in text.lower():
            raise KickBrowserError(
                f"Kick blocked the request (403 security policy) for {url}."
            )
        if status >= 400:
            raise KickBrowserError(f"HTTP {status} for {url}: {text[:240]}")
        payload = result.get("json")
        if not isinstance(payload, dict):
            raise KickBrowserError(f"Unexpected JSON payload from {url}")
        return payload

    @staticmethod
    def _extract_user_id_from_session_token(token: str | None) -> int | None:
        raw = str(token or "").strip()
        if not raw:
            return None
        try:
            decoded = urllib.parse.unquote(raw)
        except Exception:
            decoded = raw
        first = decoded.split("|", 1)[0].strip()
        if first.isdigit():
            try:
                return int(first)
            except Exception:
                return None
        return None

    @staticmethod
    def _coerce_user_id(value: Any) -> int | None:
        if value is None:
            return None
        text = str(value).strip()
        if text.isdigit():
            try:
                return int(text)
            except Exception:
                return None
        return None

    @classmethod
    def _extract_identity_from_payload(cls, payload: dict[str, Any]) -> tuple[str | None, int | None]:
        def _from_node(node: Any) -> tuple[str | None, int | None]:
            if not isinstance(node, dict):
                return (None, None)
            username = None
            for key in ("username", "login", "slug", "name"):
                raw = node.get(key)
                if isinstance(raw, str) and raw.strip():
                    username = raw.strip()
                    break
            user_id = None
            for key in ("id", "user_id", "userId"):
                uid = cls._coerce_user_id(node.get(key))
                if uid is not None:
                    user_id = uid
                    break
            if username or user_id is not None:
                return (username, user_id)
            return (None, None)

        roots: list[Any] = [payload]
        for key in ("data", "user", "viewer", "current_user", "profile", "channel"):
            value = payload.get(key)
            if value is not None:
                roots.append(value)
        for root in roots:
            username, user_id = _from_node(root)
            if username or user_id is not None:
                return (username, user_id)
        return (None, None)

    def _fetch_session_identity(self, session_token: str) -> dict[str, Any]:
        token = str(session_token or "").strip()
        fallback_user_id = self._extract_user_id_from_session_token(token)
        if token and token == self._last_identity_token and self._last_identity_info:
            cached = dict(self._last_identity_info)
            if cached.get("user_id") is None and fallback_user_id is not None:
                cached["user_id"] = fallback_user_id
            return cached

        candidate_urls = [
            "https://kick.com/api/v1/user",
            "https://kick.com/api/v1/users/me",
            "https://web.kick.com/api/v1/user",
            "https://web.kick.com/api/v1/users/me",
        ]
        if fallback_user_id is not None:
            candidate_urls.extend(
                [
                    f"https://kick.com/api/v1/users/{fallback_user_id}",
                    f"https://kick.com/api/v1/channels/{fallback_user_id}",
                    f"https://web.kick.com/api/v1/users/{fallback_user_id}",
                    f"https://web.kick.com/api/v1/channels/{fallback_user_id}",
                ]
            )

        for url in candidate_urls:
            try:
                resp = self._http_fetch_response(
                    url,
                    headers={"Accept": "application/json"},
                    auth_bearer=True,
                    timeout_seconds=12.0,
                )
            except Exception:
                continue
            status = int(resp.get("status") or 0)
            if status >= 400:
                continue
            payload = resp.get("json")
            if not isinstance(payload, dict):
                continue
            username, user_id = self._extract_identity_from_payload(payload)
            info = {
                "username": username or None,
                "user_id": user_id if user_id is not None else fallback_user_id,
                "source": f"http:{url}",
            }
            self._last_identity_token = token
            self._last_identity_info = dict(info)
            return info

        info = {
            "username": None,
            "user_id": fallback_user_id,
            "source": "token",
        }
        self._last_identity_token = token
        self._last_identity_info = dict(info)
        return info

    def get_session_status(self) -> dict[str, Any]:
        if not self.has_saved_cookies():
            return {"state": "no_session", "label": "Sesion no iniciada (sin cookies guardadas)"}
        session_token = self.get_saved_session_token()
        if not session_token:
            return {"state": "logged_out", "label": "Sesion cerrada o cookies invalidas (sin session_token)"}

        try:
            resp = self._http_fetch_response(
                self.config.drops_progress_url,
                headers={"Accept": "application/json"},
                auth_bearer=True,
                timeout_seconds=18.0,
            )
        except Exception as exc:
            return {
                "state": "unknown",
                "label": f"Cookies guardadas (no verificable: {exc})",
                "error": str(exc),
            }

        status = int(resp.get("status") or 0)
        if status in (401, 403):
            return {
                "state": "logged_out",
                "label": f"Sesion cerrada/expirada (HTTP {status})",
                "http_status": status,
            }
        if status >= 400:
            return {
                "state": "unknown",
                "label": f"Cookies guardadas, verificacion fallo (HTTP {status})",
                "http_status": status,
            }
        identity = self._fetch_session_identity(session_token)
        username = str(identity.get("username") or "").strip()
        user_id = identity.get("user_id")
        if username:
            label = f"Sesion iniciada: {username}"
        elif user_id not in (None, ""):
            label = f"Sesion iniciada (id {user_id})"
        else:
            label = "Sesion iniciada (token valido)"
        return {
            "state": "logged_in",
            "label": label,
            "username": username or None,
            "user_id": user_id,
            "source": "http",
            "identity_source": identity.get("source"),
        }

    def fetch_campaigns_and_progress(self) -> tuple[dict[str, Any], dict[str, Any]]:
        if not self.get_saved_session_token():
            raise KickBrowserError("No hay sesion guardada valida. Inicia sesion primero.")
        campaigns = self._http_fetch_json(
            self.config.drops_campaigns_url,
            headers={"Accept": "application/json"},
            auth_bearer=False,
            timeout_seconds=20.0,
        )
        progress = self._http_fetch_json(
            self.config.drops_progress_url,
            headers={"Accept": "application/json"},
            auth_bearer=True,
            timeout_seconds=20.0,
        )
        return campaigns, progress

    def fetch_campaigns(self) -> dict[str, Any]:
        if not self.get_saved_session_token():
            raise KickBrowserError("No hay sesion guardada valida. Inicia sesion primero.")
        return self._http_fetch_json(
            self.config.drops_campaigns_url,
            headers={"Accept": "application/json"},
            auth_bearer=False,
            timeout_seconds=20.0,
        )

    def fetch_progress(self) -> dict[str, Any]:
        if not self.get_saved_session_token():
            raise KickBrowserError("No hay sesion guardada valida. Inicia sesion primero.")
        return self._http_fetch_json(
            self.config.drops_progress_url,
            headers={"Accept": "application/json"},
            auth_bearer=True,
            timeout_seconds=20.0,
        )

    def channel_live_status(self, driver, slug: str) -> dict[str, Any]:
        payload = self._http_fetch_json(
            f"https://kick.com/api/v2/channels/{slug}",
            headers={"Accept": "application/json"},
            auth_bearer=False,
            timeout_seconds=16.0,
        )
        # Kick's shape differs across endpoints/versions, so keep this heuristic tolerant.
        data = payload if "data" not in payload else payload.get("data") or {}
        if not isinstance(data, dict):
            data = {}
        stream = data.get("livestream") or data.get("stream") or data.get("live_stream") or {}
        if not isinstance(stream, dict):
            stream = {}
        viewers = (
            stream.get("viewer_count")
            or stream.get("viewers")
            or data.get("viewer_count")
            or 0
        )
        category = stream.get("category") or data.get("category") or {}
        category_id = None
        if isinstance(category, dict) and category.get("id") is not None:
            try:
                category_id = int(category["id"])
            except Exception:
                category_id = None

        live = False
        truthy_keys = (
            "is_live",
            "isLive",
            "live",
            "online",
        )
        for key in truthy_keys:
            if isinstance(stream.get(key), bool):
                live = stream[key]
                break
            if isinstance(data.get(key), bool):
                live = data[key]
                break
        if not live:
            # Heuristics when no boolean flag is present
            live = any(
                stream.get(k) is not None
                for k in ("id", "session_title", "created_at", "playback_url", "key")
            )
        return {
            "live": bool(live),
            "viewer_count": int(viewers or 0),
            "category_id": category_id,
            "raw": payload,
        }

    def open_channel(self, driver, url: str) -> None:
        driver.get(url)
        time.sleep(2.5)
        self.apply_watch_page_tweaks(driver)

    def apply_watch_page_tweaks(self, driver, *, hide_player: bool = False) -> None:
        script = """
(() => {
  try {
    const videos = Array.from(document.querySelectorAll("video"));
    for (const v of videos) {
      v.muted = true;
      try { v.volume = 0; } catch (_) {}
      try { v.play().catch(() => {}); } catch (_) {}
      if (%s) {
        v.style.visibility = "hidden";
        v.style.opacity = "0";
      }
    }
  } catch (_) {}
})();
""" % ("true" if hide_player else "false")
        try:
            driver.execute_script(script)
        except Exception:
            pass

    def open_drops_inventory(self, driver) -> None:
        driver.get(self.config.drops_inventory_url)

    def best_effort_claim_all(self, driver) -> int:
        # DOM-only fallback because Kick doesn't document a viewer claim endpoint.
        self.open_drops_inventory(driver)
        time.sleep(2)
        script = """
return (() => {
  const labels = ["claim", "reclamar", "rivendica", "claimer"];
  let clicked = 0;
  const buttons = Array.from(document.querySelectorAll("button"));
  for (const btn of buttons) {
    const text = (btn.innerText || btn.textContent || "").trim().toLowerCase();
    if (!text) continue;
    if (!labels.some((l) => text.includes(l))) continue;
    if (btn.disabled) continue;
    try { btn.click(); clicked += 1; } catch (_) {}
  }
  return clicked;
})();
"""
        try:
            clicked = driver.execute_script(script)
        except Exception as exc:
            raise KickBrowserError(f"Auto-claim DOM click failed: {exc}") from exc
        return int(clicked or 0)

    def reset_profile(self, profile_name: str) -> None:
        profile_dir = self.config.chrome_data_dir / profile_name
        if profile_dir.exists():
            shutil.rmtree(profile_dir, ignore_errors=True)
