from __future__ import annotations

import json
import io
import time
import queue
import threading
import traceback
import logging
import hashlib
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse, urljoin
from datetime import datetime, timezone

import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
from tkinter.scrolledtext import ScrolledText
import tkinter.font as tkfont
import sys
import ctypes

from PIL import Image, ImageTk

from kick_browser import KickBrowserClient
from kick_models import (
    QueueItem,
    KickChannel,
    KickCampaign,
    KickProgressCampaign,
    parse_campaigns_response,
    parse_progress_response,
    merge_campaigns_with_progress,
)


logger = logging.getLogger("KickDrops")

ALL_GAMES_TOKEN = "__ALL_GAMES__"
ALL_GAMES_LABEL = "Todos"
AUTO_GAMES_CHANNEL_SOURCE = "source=auto-games-channel"

UI_ES_TO_EN: dict[str, str] = {
    "Sesion: no comprobada": "Session: not checked",
    "Sesion": "Session",
    "Sesion OK": "Session OK",
    "Sesion cerrada": "Session closed",
    "Sin sesion": "No session",
    "Listo. Inicia sesion en Kick y refresca campañas.": "Ready. Log in to Kick and refresh campaigns.",
    "Iniciar sesion": "Log in",
    "Comprobar sesion": "Check session",
    "Actualizar": "Refresh",
    "Refresco de progreso: tiempo real (15s) | Player oculto y auto-claim: activos siempre": "Progress refresh: real-time (15s) | Hidden player and auto-claim: always on",
    "Usuario activo:": "Active user:",
    "Minado actual": "Current mining",
    "Canal:": "Channel:",
    "Campaña:": "Campaign:",
    "Drop actual:": "Current drop:",
    "Cambiar canal": "Switch channel",
    "Tip: selecciona un canal en la tabla y pulsa 'Cambiar canal' para saltar al siguiente.": "Tip: select a channel in the table and press 'Switch channel' to jump to the next one.",
    "Canal": "Channel",
    "Espectadores": "Viewers",
    "Campaña": "Campaign",
    "Tiempo": "Time",
    "Estado": "Status",
    "Recompensas": "Rewards",
    "Abrir canal": "Open channel",
    "Eliminar": "Remove",
    "Reiniciar tiempo": "Reset time",
    "Subir": "Move up",
    "Bajar": "Move down",
    "Inventario": "Inventory",
    "Campañas y drops visuales": "Visual campaigns and drops",
    "Configuracion": "Settings",
    "Seleccion de juegos para minado automatico": "Game selection for auto-mining",
    "El minado automatico esta siempre activo. Marca juegos concretos o 'Todos'.": "Auto-mining is always active. Select specific games or 'All'.",
    "Idioma:": "Language:",
    "No hay campanas cargadas todavia. Pulsa 'Actualizar'.": "No campaigns loaded yet. Press 'Refresh'.",
    "No hay campañas para mostrar.": "No campaigns to display.",
    "No hay campañas de los juegos seleccionados.": "No campaigns for selected games.",
    "No hay juegos seleccionados. Marca un juego o 'Todos' en Configuración.": "No games selected. Select a game or 'All' in Settings.",
    "Sin drops en esta campana.": "No drops in this campaign.",
    "Todos": "All",
    "Todos los juegos": "All games",
    "juegos seleccionados": "selected games",
    "Objetivo": "Goal",
    "Progreso campana": "Campaign progress",
    "Finaliza": "Ends",
    "Canales": "Channels",
    "Reclamado": "Claimed",
    "Pendiente": "Pending",
    "disponible": "available",
    "caducada": "expired",
    "Campañas": "Campaigns",
    "Progreso": "Progress",
    "Cola vacía": "Empty queue",
    "No hay canales disponibles.": "No channels available.",
    "No hay canales disponibles para cambiar.": "No channels available to switch.",
    "Este tab es solo visual.": "This tab is visual-only.",
    "Sin minado activo": "No active mining",
    "Sin datos de drop todavía": "No drop data yet",
    "Cola iniciada": "Queue started",
    "Cola detenida": "Queue stopped",
    "Deteniendo cola...": "Stopping queue...",
    "LIVE": "LIVE",
    "FINISHED": "FINISHED",
    "EXPIRED": "EXPIRED",
    "RETRY": "RETRY",
    "WRONG_CATEGORY": "WRONG_CATEGORY",
    "CONNECTING": "CONNECTING",
    "PENDING": "PENDING",
    "STOPPED": "STOPPED",
    "Ese canal ya está en la cola": "That channel is already in the queue",
    "Ya existe": "Already exists",
    "URL inválida": "Invalid URL",
    "Error": "Error",
    "Importar cookies": "Import cookies",
}
UI_EN_TO_ES: dict[str, str] = {v: k for k, v in UI_ES_TO_EN.items()}


@dataclass(slots=True)
class AppConfig:
    queue_items: list[QueueItem] = field(default_factory=list)
    hide_player: bool = False
    auto_claim: bool = False
    default_minutes: int = 120
    watch_tick_seconds: int = 60
    poll_interval_seconds: int = 20
    browser_cookie_source: str = "chrome"
    show_browser_window: bool = False
    auto_refresh_progress: bool = True
    auto_refresh_seconds: int = 120
    login_username: str = ""
    auto_game_mining: bool = False
    preferred_games: list[str] = field(default_factory=list)
    language: str = "en"

    def to_dict(self) -> dict[str, object]:
        return {
            "queue_items": [item.to_dict() for item in self.queue_items],
            "hide_player": self.hide_player,
            "auto_claim": self.auto_claim,
            "default_minutes": self.default_minutes,
            "watch_tick_seconds": self.watch_tick_seconds,
            "poll_interval_seconds": self.poll_interval_seconds,
            "browser_cookie_source": self.browser_cookie_source,
            "show_browser_window": self.show_browser_window,
            "auto_refresh_progress": self.auto_refresh_progress,
            "auto_refresh_seconds": self.auto_refresh_seconds,
            "login_username": self.login_username,
            "auto_game_mining": self.auto_game_mining,
            "preferred_games": list(self.preferred_games),
            "language": self.language,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "AppConfig":
        raw_items = data.get("queue_items") or data.get("items") or []
        items: list[QueueItem] = []
        if isinstance(raw_items, list):
            for raw in raw_items:
                if isinstance(raw, dict):
                    try:
                        items.append(QueueItem.from_dict(raw))
                    except Exception:
                        continue
        return cls(
            queue_items=items,
            hide_player=bool(data.get("hide_player", False)),
            auto_claim=bool(data.get("auto_claim", False)),
            default_minutes=int(data.get("default_minutes", 120) or 120),
            watch_tick_seconds=int(data.get("watch_tick_seconds", 60) or 60),
            poll_interval_seconds=int(data.get("poll_interval_seconds", 20) or 20),
            browser_cookie_source=str(data.get("browser_cookie_source") or "chrome"),
            show_browser_window=bool(data.get("show_browser_window", False)),
            auto_refresh_progress=bool(data.get("auto_refresh_progress", True)),
            auto_refresh_seconds=int(data.get("auto_refresh_seconds", 120) or 120),
            login_username=str(data.get("login_username") or "").strip(),
            auto_game_mining=bool(data.get("auto_game_mining", False)),
            preferred_games=[
                str(v).strip()
                for v in (data.get("preferred_games") or [])
                if str(v).strip()
            ] if isinstance(data.get("preferred_games"), list) else [],
            language=str(data.get("language") or "en").strip().lower() or "en",
        )


def _fmt_seconds(seconds: int) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02}:{m:02}:{s:02}"


def _fmt_exc(exc: Exception) -> str:
    msg = str(exc).strip()
    if msg:
        return f"{exc.__class__.__name__}: {msg}"
    return f"{exc.__class__.__name__}: unknown error"


def _app_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _resource_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
    return _app_base_dir()


def _apply_window_icon(root: tk.Tk) -> None:
    resource_dir = _resource_base_dir()
    icon_ico = resource_dir / "icons" / "pickaxe.ico"
    icon_png = resource_dir / "icons" / "pickaxe.png"

    if sys.platform == "win32" and icon_ico.exists():
        try:
            root.iconbitmap(default=str(icon_ico))
            return
        except Exception:
            pass

    if icon_png.exists():
        try:
            image = tk.PhotoImage(file=str(icon_png))
            root.iconphoto(True, image)
            # Keep a strong reference; Tk drops images otherwise.
            root._app_icon_ref = image  # type: ignore[attr-defined]
        except Exception:
            pass


def normalize_kick_url(text: str) -> str:
    value = (text or "").strip()
    if not value:
        raise ValueError("URL vacía")
    if "://" not in value:
        value = f"https://kick.com/{value.lstrip('/')}"
    parsed = urlparse(value)
    if "kick.com" not in parsed.netloc.lower():
        raise ValueError("Solo se admiten URLs de kick.com")
    slug = parsed.path.strip("/").split("/")[0].strip()
    if not slug:
        raise ValueError("URL de canal inválida")
    return f"https://kick.com/{slug}"


class QueueWorker(threading.Thread):
    def __init__(self, app: "KickMinerApp"):
        super().__init__(daemon=True)
        self.app = app
        self.stop_event = threading.Event()
        self.driver = None
        self.current_url = ""
        self.current_slug = ""
        self.hide_player = True
        self.auto_claim = True
        self.show_browser_window = bool(app.show_browser_window_var.get())

    def stop(self) -> None:
        self.stop_event.set()

    def run(self) -> None:
        self.app.post_log("Worker: iniciando cola")
        try:
            self._run_loop()
        except Exception:
            self.app.post_log("Worker: error fatal\n" + traceback.format_exc())
        finally:
            if self.driver is not None:
                try:
                    self.app.browser.close_driver(self.driver)
                except Exception:
                    pass
            self.app.post_worker_stopped()
            self.app.post_log("Worker: detenido")

    def _create_driver(self):
        # Keep watcher fully hidden while the stream is being kept open.
        if self.show_browser_window:
            self.app.post_log("Modo oculto forzado para el worker (se ignora 'Mostrar navegador del worker').")
        driver = self.app.browser.create_offscreen_driver(profile_name="watcher-offscreen")
        self.app.browser.prime_session_with_cookies(driver)
        return driver

    def _run_loop(self) -> None:
        last_save = 0.0
        while not self.stop_event.is_set():
            item = self.app.get_next_queue_item()
            if item is None:
                self.app.post_log("Cola vacía o todo completado")
                time.sleep(1)
                break
            status_upper = str(item.status or "").upper()
            if item.done or status_upper in {"FINISHED", "EXPIRED"}:
                final_status = "EXPIRED" if status_upper == "EXPIRED" else "FINISHED"
                self.app.post_update_item(item.url, status=final_status)
                time.sleep(0.1)
                continue
            if self.driver is None:
                self.driver = self._create_driver()
                self.current_url = ""
                self.current_slug = ""
            result = self._process_item(item)
            if result == "retry":
                self.app.post_retry_campaign_hint(item.campaign_id, item.campaign_name)
                self.app.post_rotate_item(item.url)
            now = time.time()
            if now - last_save >= 3:
                self.app.post_save_config()
                last_save = now

    def _process_item(self, item: QueueItem) -> str:
        url = item.url
        slug = item.slug
        if self.current_url != url:
            self.app.post_log(f"Abriendo canal: {url}")
            self.app.post_update_item(url, status="CONNECTING", notes="")
            self.app.browser.open_channel(self.driver, url)
            self.current_url = url
            self.current_slug = slug
        self.app.browser.apply_watch_page_tweaks(
            self.driver, hide_player=self.hide_player
        )

        poll_every = max(5, self.app.config.poll_interval_seconds)
        tick_seconds = max(30, self.app.config.watch_tick_seconds)
        next_poll = 0.0
        next_tick = time.time() + tick_seconds

        while not self.stop_event.is_set():
            now = time.time()
            if self.app._consume_force_channel_switch(url):
                self.app.post_update_item(url, status="RETRY", notes="cambio manual de canal")
                self.app.post_log(f"Cambio manual solicitado: {slug}")
                return "retry"
            status_upper = str(item.status or "").upper()
            if item.done or status_upper in {"FINISHED", "EXPIRED"}:
                if status_upper == "EXPIRED":
                    self.app.post_update_item(url, status="EXPIRED", notes="campaña caducada")
                    return "finished"
                self.app.post_update_item(url, status="FINISHED", notes="drops completados")
                if self.auto_claim:
                    self._auto_claim_if_enabled()
                return "finished"
            if now >= next_poll:
                next_poll = now + poll_every
                try:
                    live_info = self.app.browser.channel_live_status(self.driver, slug)
                except Exception as exc:
                    self.app.post_update_item(url, status="RETRY", notes=f"error live check: {exc}")
                    self.app.post_log(f"Live check error ({slug}): {exc}")
                    time.sleep(5)
                    return "retry"
                if not live_info.get("live", False):
                    self.app.post_update_item(url, status="RETRY", notes="offline")
                    self.app.post_log(f"Canal offline: {slug}")
                    time.sleep(2)
                    return "retry"
                live_category = live_info.get("category_id")
                if item.category_id and live_category and int(live_category) != int(item.category_id):
                    self.app.post_update_item(
                        url, status="WRONG_CATEGORY", notes=f"cat {live_category} != {item.category_id}"
                    )
                    self.app.post_log(
                        f"Canal en categoría distinta ({slug}): {live_category} != {item.category_id}"
                    )
                    time.sleep(2)
                    return "retry"
                viewers = live_info.get("viewer_count", 0)
                self.app.post_update_item(url, status="LIVE", notes=f"viewers={viewers}")
                self.app.browser.apply_watch_page_tweaks(
                    self.driver, hide_player=self.hide_player
                )
            if now >= next_tick:
                next_tick = now + tick_seconds
                self.app.post_increment_elapsed(url, tick_seconds)
                self.app.post_update_item(url, status="LIVE")
            time.sleep(1)
        self.app.post_update_item(url, status="STOPPED", notes="detenido")
        return "stopped"

    def _auto_claim_if_enabled(self) -> None:
        if self.driver is None:
            return
        try:
            clicked = self.app.browser.best_effort_claim_all(self.driver)
            self.app.post_log(f"Auto-claim (DOM): {clicked} click(s)")
            if self.current_url:
                self.app.browser.open_channel(self.driver, self.current_url)
        except Exception as exc:
            self.app.post_log(f"Auto-claim falló: {exc}")


class KickMinerApp:
    def __init__(self, root: tk.Tk, base_dir: Path):
        self.root = root
        self.base_dir = base_dir
        self.config_path = base_dir / "kick_config.json"
        self.browser = KickBrowserClient(base_dir)
        self.ui_queue: queue.Queue[tuple[str, tuple, dict]] = queue.Queue()
        self.worker: QueueWorker | None = None

        self.config = self._load_config()
        self.config.language = "es" if str(self.config.language or "").strip().lower().startswith("es") else "en"
        # Force always-on operational behaviors.
        self.config.hide_player = True
        self.config.auto_claim = True
        self.config.auto_refresh_progress = True
        self.config.auto_refresh_seconds = 15
        self.config.auto_game_mining = True
        self.queue_items: list[QueueItem] = self.config.queue_items
        self.campaigns: list[KickCampaign] = []
        self.progress: list[KickProgressCampaign] = []
        self.campaign_map: dict[str, KickCampaign] = {}
        self._refresh_campaigns_running = False
        self._refresh_progress_running = False
        self._session_check_running = False
        self._session_state = "unknown"
        self._initial_sync_done = False
        self._auto_login_running = False
        self._last_progress_refresh_ts = 0.0
        self._last_campaigns_refresh_ts = 0.0
        self._shutting_down = False
        self._force_switch_urls: set[str] = set()
        self._force_switch_lock = threading.Lock()
        self._retry_campaign_hint_id: str | None = None
        self._retry_campaign_hint_name: str | None = None
        self._login_driver = None
        self._inventory_driver = None
        self._reward_thumb_cache: dict[str, ImageTk.PhotoImage] = {}
        self._reward_thumb_pending: set[str] = set()
        self._reward_thumb_error_logged: set[str] = set()
        self._reward_thumb_failed: set[str] = set()
        self._reward_thumb_blocked_notice_shown = False
        self._reward_thumb_disk_cache_dir = self.base_dir / "cache" / "reward_thumbs"
        self._reward_thumb_disk_cache_dir.mkdir(parents=True, exist_ok=True)
        self._reward_card_image_labels: dict[str, tk.Label] = {}
        self._campaign_live_probe_token = 0
        self._campaign_channel_by_slug: dict[str, KickChannel] = {}
        self._channel_live_cache: dict[str, tuple[bool | None, int, float]] = {}
        self._preferred_games_cached: list[str] = self._normalize_preferred_games(self.config.preferred_games)
        self._inventory_refresh_pending = False
        self._settings_games_refresh_pending = False
        self._settings_game_vars: dict[str, tk.BooleanVar] = {}
        self._settings_game_cards: dict[str, tk.Frame] = {}
        self._settings_game_checks: dict[str, tk.Checkbutton] = {}
        self._settings_game_images: dict[str, str] = {}
        self._placeholder_thumb = self._make_placeholder_thumb()

        self._setup_logging()
        self._build_ui()
        self._refresh_queue_tree()
        self.root.after(100, self._pump_ui_queue)
        self.root.after(1500, self._auto_refresh_tick)
        self.root.after(900, self._auto_restore_saved_session)

    def _setup_logging(self) -> None:
        logger.setLevel(logging.INFO)

    def _load_config(self) -> AppConfig:
        if not self.config_path.exists():
            return AppConfig()
        try:
            with self.config_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return AppConfig.from_dict(data)
        except Exception:
            pass
        return AppConfig()

    def save_config(self) -> None:
        self.config.queue_items = self.queue_items
        self.config.hide_player = True
        self.config.auto_claim = True
        self.config.default_minutes = 0
        self.config.browser_cookie_source = self.cookie_source_var.get()
        self.config.show_browser_window = self.show_browser_window_var.get()
        self.config.auto_refresh_progress = True
        self.config.auto_refresh_seconds = 15
        self.config.login_username = ""
        self.config.auto_game_mining = True
        if hasattr(self, "language_var"):
            lang_raw = str(self.language_var.get() or "en").strip().lower()
            self.config.language = "es" if lang_raw.startswith("es") else "en"
        if getattr(self, "_settings_game_vars", None):
            self._preferred_games_cached = self._get_selected_games_from_settings()
        self._preferred_games_cached = self._normalize_preferred_games(self._preferred_games_cached)
        self.config.preferred_games = list(self._preferred_games_cached)
        with self.config_path.open("w", encoding="utf-8") as f:
            json.dump(self.config.to_dict(), f, ensure_ascii=False, indent=2)

    def _make_placeholder_thumb(self) -> ImageTk.PhotoImage:
        size = 92
        img = Image.new("RGB", (size, size), color=(33, 37, 41))
        for x in range(size):
            for y in range(size):
                if (x + y) % 12 < 6:
                    img.putpixel((x, y), (45, 52, 58))
        return ImageTk.PhotoImage(img)

    def _lang(self) -> str:
        if hasattr(self, "language_var"):
            raw = str(self.language_var.get() or "en").strip().lower()
        else:
            raw = str(self.config.language or "en").strip().lower()
        return "es" if raw.startswith("es") else "en"

    def _tr(self, text: str) -> str:
        value = str(text or "")
        if not value:
            return value
        if self._lang() == "en":
            direct = UI_ES_TO_EN.get(value)
            if direct is not None:
                return direct
            translated = value
            for es, en in sorted(UI_ES_TO_EN.items(), key=lambda kv: len(kv[0]), reverse=True):
                if es and es in translated:
                    translated = translated.replace(es, en)
            return translated
        direct = UI_EN_TO_ES.get(value)
        if direct is not None:
            return direct
        translated = value
        for en, es in sorted(UI_EN_TO_ES.items(), key=lambda kv: len(kv[0]), reverse=True):
            if en and en in translated:
                translated = translated.replace(en, es)
        return translated

    def _tr_format(self, template: str, **kwargs) -> str:
        return self._tr(template).format(**kwargs)

    def _translate_widget_texts(self, widget) -> None:
        try:
            current = widget.cget("text")
            if isinstance(current, str) and current.strip():
                widget.configure(text=self._tr(current))
        except Exception:
            pass
        for child in widget.winfo_children():
            self._translate_widget_texts(child)

    def _apply_language_to_ui(self) -> None:
        self.root.title(self._tr("Kick Drops Miner"))
        self._translate_widget_texts(self.root)
        if hasattr(self, "notebook"):
            for tab_id in self.notebook.tabs():
                tab_text = self.notebook.tab(tab_id, "text")
                if isinstance(tab_text, str) and tab_text:
                    self.notebook.tab(tab_id, text=self._tr(tab_text))
        if hasattr(self, "queue_tree"):
            for col in self.queue_tree["columns"]:
                heading_text = self.queue_tree.heading(col, "text")
                if isinstance(heading_text, str) and heading_text:
                    self.queue_tree.heading(col, text=self._tr(heading_text))
        if hasattr(self, "queue_menu"):
            for i in range(self.queue_menu.index("end") + 1):
                try:
                    label = self.queue_menu.entrycget(i, "label")
                except Exception:
                    continue
                if isinstance(label, str) and label:
                    self.queue_menu.entryconfigure(i, label=self._tr(label))
        if hasattr(self, "settings_games_count_var"):
            self._refresh_settings_count_label()
        if hasattr(self, "status_var"):
            self.status_var.set(self._tr(self.status_var.get()))
        if hasattr(self, "session_status_var"):
            self.session_status_var.set(self._tr(self.session_status_var.get()))

    @staticmethod
    def _language_code_to_label(code: str) -> str:
        return "Español" if str(code or "").strip().lower().startswith("es") else "English"

    @staticmethod
    def _language_label_to_code(label: str) -> str:
        return "es" if str(label or "").strip().lower().startswith("espa") else "en"

    def _on_language_changed(self, _event=None) -> None:
        selected_label = str(getattr(self, "language_combo_var", tk.StringVar(value="English")).get() or "English")
        code = self._language_label_to_code(selected_label)
        if self._lang() == code:
            return
        self.language_var.set(code)
        self.save_config()
        self._apply_language_to_ui()
        self._refresh_queue_tree()
        self._refresh_inventory_view()

    def _auto_refresh_tick(self) -> None:
        try:
            if self._session_state == "logged_in":
                now = time.time()
                progress_interval = 15
                campaigns_interval = 120
                if not self._refresh_progress_running and (now - self._last_progress_refresh_ts) >= progress_interval:
                    self.refresh_progress(silent=True)
                if not self._refresh_campaigns_running and (now - self._last_campaigns_refresh_ts) >= campaigns_interval:
                    self.refresh_campaigns_and_progress(silent=True)
                self._ensure_queue_worker_running()
        finally:
            self.root.after(1500, self._auto_refresh_tick)

    def _ensure_queue_worker_running(self) -> None:
        if self._shutting_down:
            return
        if self._session_state != "logged_in":
            return
        if self.worker is not None and self.worker.is_alive():
            return
        if not self.queue_items:
            return
        self.start_queue(silent=True)

    def _auto_restore_saved_session(self) -> None:
        if not self.browser.has_saved_cookies():
            self.post_session_status({"state": "no_session", "label": "Sin cookies guardadas"})
            return
        self.post_log("Detectadas cookies guardadas. Validando sesion automaticamente...")
        self.refresh_session_status(notify_if_relogin_needed=True, auto_import_if_missing=False)

    def _on_rewards_frame_configure(self, _event=None) -> None:
        self.rewards_canvas.configure(scrollregion=self.rewards_canvas.bbox("all"))

    def _on_rewards_canvas_configure(self, event=None) -> None:
        if event is not None:
            self.rewards_canvas.itemconfigure(self._rewards_canvas_window, width=event.width)

    def _on_inventory_frame_configure(self, _event=None) -> None:
        if hasattr(self, "inventory_canvas"):
            self.inventory_canvas.configure(scrollregion=self.inventory_canvas.bbox("all"))

    def _on_inventory_canvas_configure(self, event=None) -> None:
        if event is not None and hasattr(self, "inventory_canvas"):
            self.inventory_canvas.itemconfigure(self._inventory_canvas_window, width=event.width)
            self._schedule_inventory_refresh()

    def _is_inventory_widget(self, widget) -> bool:
        while widget is not None:
            if widget is self.inventory_canvas or widget is self.inventory_frame:
                return True
            widget = getattr(widget, "master", None)
        return False

    def _is_settings_widget(self, widget) -> bool:
        while widget is not None:
            if widget is getattr(self, "settings_games_canvas", None) or widget is getattr(self, "settings_games_frame", None):
                return True
            widget = getattr(widget, "master", None)
        return False

    def _on_inventory_mousewheel(self, event) -> str | None:
        if not hasattr(self, "inventory_canvas"):
            return None
        target = None
        try:
            target = self.root.winfo_containing(event.x_root, event.y_root)
        except Exception:
            target = getattr(event, "widget", None)
        if target is None:
            target = getattr(event, "widget", None)
        canvas = None
        if self._is_inventory_widget(target):
            canvas = self.inventory_canvas
        elif self._is_settings_widget(target):
            canvas = self.settings_games_canvas
        if canvas is None:
            return None

        delta = int(getattr(event, "delta", 0) or 0)
        if delta:
            steps = -1 if delta > 0 else 1
            if abs(delta) >= 120:
                steps = int(-delta / 120)
        else:
            num = int(getattr(event, "num", 0) or 0)
            if num == 4:
                steps = -1
            elif num == 5:
                steps = 1
            else:
                return None
        canvas.yview_scroll(steps, "units")
        return "break"

    def _on_settings_games_frame_configure(self, _event=None) -> None:
        if hasattr(self, "settings_games_canvas"):
            self.settings_games_canvas.configure(scrollregion=self.settings_games_canvas.bbox("all"))

    def _on_settings_games_canvas_configure(self, event=None) -> None:
        if event is not None and hasattr(self, "settings_games_canvas"):
            self.settings_games_canvas.itemconfigure(self._settings_games_canvas_window, width=event.width)
            self._schedule_settings_games_refresh()

    def _schedule_settings_games_refresh(self) -> None:
        if self._settings_games_refresh_pending:
            return
        self._settings_games_refresh_pending = True

        def _flush():
            self._settings_games_refresh_pending = False
            self._refresh_settings_games_list()

        self.root.after(200, _flush)

    def _schedule_inventory_refresh(self) -> None:
        if self._inventory_refresh_pending:
            return
        self._inventory_refresh_pending = True

        def _flush():
            self._inventory_refresh_pending = False
            self._refresh_inventory_view()

        self.root.after(200, _flush)

    def _ensure_inventory_styles(self) -> None:
        if getattr(self, "_inventory_styles_ready", False):
            return
        try:
            style = ttk.Style(self.root)
            base_bg = style.lookup("TFrame", "background") or self.root.cget("bg")
            style.configure("InventoryNormal.TFrame", background=base_bg)
            style.configure("InventoryNormal.TLabel", background=base_bg, foreground="#111111")
            style.configure("InventoryExpired.TFrame", background=base_bg)
            style.configure("InventoryExpired.TLabel", background=base_bg, foreground="#111111")
        except Exception:
            pass
        self._inventory_styles_ready = True

    def _refresh_inventory_view(self) -> None:
        if not hasattr(self, "inventory_frame"):
            return
        self._ensure_inventory_styles()
        for child in self.inventory_frame.winfo_children():
            child.destroy()
        self.inventory_frame.columnconfigure(0, weight=1)
        if not self.campaigns:
            ttk.Label(
                self.inventory_frame,
                text=self._tr("No hay campanas cargadas todavia. Pulsa 'Actualizar'."),
            ).grid(row=0, column=0, sticky="ew", padx=12, pady=12)
            return

        mine_all, selected_games = self._preferred_game_filter()
        visible_campaigns = list(self.campaigns) if mine_all else [
            campaign
            for campaign in self.campaigns
            if (campaign.game or "").strip().casefold() in selected_games
        ]

        if not visible_campaigns:
            if mine_all:
                msg = self._tr("No hay campañas para mostrar.")
            elif selected_games:
                msg = self._tr("No hay campañas de los juegos seleccionados.")
            else:
                msg = self._tr("No hay juegos seleccionados. Marca un juego o 'Todos' en Configuración.")
            ttk.Label(self.inventory_frame, text=msg).grid(row=0, column=0, sticky="ew", padx=12, pady=12)
            return

        total_rewards = sum(len(campaign.rewards) for campaign in visible_campaigns)
        ttk.Label(
            self.inventory_frame,
            text=f"{len(visible_campaigns)} {self._tr('Campañas').lower()} | {total_rewards} drops",
        ).grid(row=0, column=0, sticky="w", padx=8, pady=(2, 6))

        available_width = 1200
        try:
            available_width = max(900, int(self.inventory_canvas.winfo_width()))
        except Exception:
            pass
        info_panel_width = 420
        reward_area_width = max(420, available_width - info_panel_width - 60)
        rewards_per_row = max(2, min(6, reward_area_width // 220))

        for row_idx, campaign in enumerate(visible_campaigns, start=1):
            is_expired = self._is_campaign_expired(campaign)
            frame_style = "InventoryNormal.TFrame"
            label_style = "InventoryNormal.TLabel"
            campaign_state = "expired" if is_expired else "available"
            campaign_state_color = "#c0392b" if is_expired else "#1f8f4a"

            card = ttk.Frame(self.inventory_frame, padding=10, relief="ridge", style=frame_style)
            card.grid(row=row_idx, column=0, sticky="ew", padx=6, pady=(0, 8))
            card.columnconfigure(0, weight=0)
            card.columnconfigure(2, weight=1)

            info_panel = ttk.Frame(card, style=frame_style)
            info_panel.grid(row=0, column=0, sticky="nw", padx=(0, 10))
            info_panel.columnconfigure(1, weight=1)

            cover_url = self._effective_reward_image_url(None, campaign.game_image)
            cover_img = self._get_reward_thumb(cover_url)
            cover = ttk.Label(info_panel, image=cover_img, style=label_style)
            cover.image = cover_img
            cover.grid(row=0, column=0, rowspan=6, sticky="nw", padx=(0, 10))
            if cover_url:
                self._request_reward_thumb(cover_url)

            status_raw = (campaign.progress_status or campaign.status or "-").replace("_", " ").strip()
            status = status_raw.title() if status_raw else "-"
            max_units = max(0, int(campaign.max_required_minutes))
            if max_units > 0:
                campaign_percent = int(max(0, min(100, round((campaign.progress_units / max_units) * 100))))
                progress_text = f"{campaign.progress_units}/{max_units} min"
            else:
                campaign_percent = int(
                    max(
                        0,
                        min(
                            100,
                            round((sum(r.progress for r in campaign.rewards) * 100.0) / max(1, len(campaign.rewards))),
                        ),
                    )
                )
                progress_text = f"{campaign.progress_units} min"

            ttk.Label(info_panel, text=f"{campaign.game} | {campaign.name}", style=label_style).grid(row=0, column=1, sticky="w")
            ttk.Label(
                info_panel,
                text=self._tr(f"{campaign_state}"),
                style=label_style,
                foreground=campaign_state_color,
            ).grid(row=1, column=1, sticky="w", pady=(2, 0))
            status_text = (
                f"{self._tr('Estado')}: {status} | "
                f"{self._tr('Canales')}: {len(campaign.channels)} | Drops: {len(campaign.rewards)}"
            )
            ttk.Label(
                info_panel,
                text=status_text,
                style=label_style,
            ).grid(row=2, column=1, sticky="w", pady=(2, 0))
            ttk.Label(
                info_panel,
                text=f"{self._tr('Finaliza')}: {campaign.ends_at or '-'}",
                style=label_style,
            ).grid(row=3, column=1, sticky="w", pady=(2, 0))
            bar = ttk.Progressbar(info_panel, mode="determinate", maximum=100, value=campaign_percent)
            bar.grid(row=4, column=1, sticky="ew", pady=(6, 0))
            ttk.Label(
                info_panel,
                text=f"{self._tr('Progreso campana')}: {campaign_percent}% ({progress_text})",
                style=label_style,
            ).grid(
                row=5, column=1, sticky="w", pady=(2, 0)
            )

            ttk.Separator(card, orient="vertical").grid(row=0, column=1, sticky="ns", padx=(0, 8))

            rewards_grid = ttk.Frame(card, style=frame_style)
            rewards_grid.grid(row=0, column=2, sticky="ew")
            for col in range(rewards_per_row):
                rewards_grid.columnconfigure(col, weight=1, uniform=f"reward-{row_idx}")

            if not campaign.rewards:
                ttk.Label(rewards_grid, text=self._tr("Sin drops en esta campana."), style=label_style).grid(
                    row=0, column=0, sticky="w", padx=4, pady=4
                )
                continue

            wraplength = max(120, min(220, (reward_area_width // rewards_per_row) - 80))
            for idx, reward in enumerate(campaign.rewards):
                reward_row = idx // rewards_per_row
                reward_col = idx % rewards_per_row
                reward_card = ttk.Frame(rewards_grid, padding=8, relief="groove", style=frame_style)
                reward_card.grid(row=reward_row, column=reward_col, sticky="nsew", padx=4, pady=4)
                reward_card.columnconfigure(1, weight=1)

                img_url = self._effective_reward_image_url(reward.image_url, campaign.game_image)
                reward_img = self._get_reward_thumb(img_url)
                img = ttk.Label(reward_card, image=reward_img, style=label_style)
                img.image = reward_img
                img.grid(row=0, column=0, rowspan=4, sticky="nw", padx=(0, 8))
                if img_url:
                    self._request_reward_thumb(img_url)

                reward_percent = int(max(0, min(100, round(float(reward.progress) * 100.0))))
                ttk.Label(reward_card, text=reward.name, wraplength=wraplength, justify=tk.LEFT, style=label_style).grid(
                    row=0, column=1, sticky="w"
                )
                goal_text = f"{self._tr('Objetivo')}: {int(reward.required_units or 0)} min"
                ttk.Label(reward_card, text=goal_text, style=label_style).grid(row=1, column=1, sticky="w", pady=(2, 0))
                reward_bar = ttk.Progressbar(reward_card, mode="determinate", maximum=100, value=reward_percent)
                reward_bar.grid(row=2, column=1, sticky="ew", pady=(4, 0))
                ttk.Label(
                    reward_card,
                    text=f"{reward_percent}% | {self._tr('Reclamado') if reward.claimed else self._tr('Pendiente')}",
                    style=label_style,
                ).grid(row=3, column=1, sticky="w", pady=(4, 0))
        self._apply_language_to_ui()


    def _get_reward_thumb(self, url: str | None) -> ImageTk.PhotoImage:
        if not url:
            return self._placeholder_thumb
        return self._reward_thumb_cache.get(url, self._placeholder_thumb)

    @staticmethod
    def _reward_thumb_candidates(url: str) -> list[str]:
        raw = str(url or "").strip()
        if not raw:
            return []
        parsed = urlparse(raw)
        if parsed.scheme in {"http", "https"}:
            out = [raw]
            p = parsed.path.lstrip("/")
            if "drops/reward-image/" in p and "ext.cdn.kick.com" not in (parsed.netloc or ""):
                out.insert(0, f"https://ext.cdn.kick.com/{p}?width=256,format=webp,quality=75")
            return out
        if raw.startswith("//"):
            return [f"https:{raw}"]
        clean = raw.lstrip("/")
        out = [
            f"https://ext.cdn.kick.com/{clean}?width=256,format=webp,quality=75",
            urljoin("https://ext.cdn.kick.com/", clean),
            urljoin("https://files.kick.com/images/", clean),
            urljoin("https://files.kick.com/", clean),
            urljoin("https://kick.com/", clean),
        ]
        dedup: list[str] = []
        for item in out:
            if item not in dedup:
                dedup.append(item)
        return dedup

    def _reward_thumb_cache_path(self, url: str) -> Path:
        key = hashlib.sha1(url.encode("utf-8", errors="ignore")).hexdigest()
        return self._reward_thumb_disk_cache_dir / f"{key}.png"

    def _request_reward_thumb(self, url: str | None) -> None:
        if (
            not url
            or url in self._reward_thumb_failed
            or url in self._reward_thumb_cache
            or url in self._reward_thumb_pending
        ):
            return
        cache_path = self._reward_thumb_cache_path(url)
        if cache_path.exists():
            try:
                data = cache_path.read_bytes()
                if data:
                    self._dispatch("_ui_reward_thumb_loaded", url, data, None)
                    return
            except Exception:
                pass
        self._reward_thumb_pending.add(url)

        def task():
            err: str | None = None
            data: bytes | None = None
            last_err = "unknown error"
            candidates = self._reward_thumb_candidates(url)
            for candidate in candidates:
                try:
                    data = self.browser.fetch_image_bytes_fast(candidate, timeout_seconds=8.0)
                    err = None
                    break
                except Exception as exc:
                    last_err = str(exc)
                    continue
            if data is None:
                err = last_err
            self._dispatch("_ui_reward_thumb_loaded", url, data, err)

        threading.Thread(target=task, daemon=True).start()

    def _ui_reward_thumb_loaded(self, url: str, data: bytes | None, err: str | None) -> None:
        self._reward_thumb_pending.discard(url)
        if data is None:
            if err and url not in self._reward_thumb_error_logged:
                err_l = err.lower()
                if "403" in err_l or "access denied" in err_l or "security policy" in err_l:
                    self._reward_thumb_failed.add(url)
                    if not self._reward_thumb_blocked_notice_shown:
                        self._reward_thumb_blocked_notice_shown = True
                        self._ui_log(
                            "Miniaturas bloqueadas por Kick (403). Se usa imagen de placeholder."
                        )
                else:
                    self._reward_thumb_error_logged.add(url)
                    self._ui_log(f"Miniatura reward no disponible: {err}")
            return
        try:
            img = Image.open(io.BytesIO(data)).convert("RGB")
            img.thumbnail((92, 92))
            photo = ImageTk.PhotoImage(img)
        except Exception as exc:
            self._ui_log(f"Error procesando miniatura: {exc}")
            return
        try:
            cache_path = self._reward_thumb_cache_path(url)
            if not cache_path.exists():
                cache_path.write_bytes(data)
        except Exception:
            pass
        self._reward_thumb_cache[url] = photo
        # Re-render selected campaign to swap placeholders with real thumbnails.
        self._refresh_campaign_detail(self._selected_campaign())
        self._schedule_inventory_refresh()
        self._schedule_settings_games_refresh()

    def _build_ui(self) -> None:
        self.root.title("Kick Drops Miner")
        self.root.geometry("1280x820")
        self.root.minsize(1040, 680)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self.hide_player_var = tk.BooleanVar(value=True)
        self.auto_claim_var = tk.BooleanVar(value=True)
        self.cookie_source_var = tk.StringVar(value=self.config.browser_cookie_source or "chrome")
        # Forced headless for automated tasks, only login remains visible.
        self.show_browser_window_var = tk.BooleanVar(value=False)
        self.auto_refresh_progress_var = tk.BooleanVar(value=True)
        self.auto_refresh_seconds_var = tk.IntVar(value=15)
        self.language_var = tk.StringVar(value=self.config.language)
        self.session_status_var = tk.StringVar(value="Sesion: no comprobada")
        self.login_username_var = tk.StringVar(value=self.config.login_username or "")
        self.auto_game_mining_var = tk.BooleanVar(value=True)

        self.session_user_var = tk.StringVar(value="-")
        self.general_current_channel_var = tk.StringVar(value="-")
        self.general_current_campaign_var = tk.StringVar(value="-")
        self.general_current_drop_var = tk.StringVar(value="-")
        self.general_current_progress_var = tk.StringVar(value="0%")

        top = ttk.Frame(self.root, padding=8)
        top.pack(fill=tk.BOTH, expand=True)
        top.rowconfigure(0, weight=1)
        top.columnconfigure(0, weight=1)

        self.notebook = ttk.Notebook(top)
        self.notebook.grid(row=0, column=0, sticky="nsew")

        self._build_queue_tab()
        self._build_campaigns_tab()
        self._build_settings_tab()

        self.status_var = tk.StringVar(value="Listo. Inicia sesion en Kick y refresca campañas.")
        ttk.Label(top, textvariable=self.status_var, anchor="w").grid(row=1, column=0, sticky="ew", pady=(8, 0))

        self._refresh_settings_games_list()
        self._refresh_inventory_view()
        self._refresh_general_mining_panel()
        self._apply_language_to_ui()

    def _build_queue_tab(self) -> None:
        frame = ttk.Frame(self.notebook, padding=8)
        frame.rowconfigure(3, weight=1)
        frame.rowconfigure(4, weight=1)
        frame.columnconfigure(0, weight=1)
        self.notebook.add(frame, text="General")

        session_box = ttk.LabelFrame(frame, text="Sesion")
        session_box.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        session_box.columnconfigure(6, weight=1)

        self.open_login_btn = ttk.Button(session_box, text="Iniciar sesion", command=self.login_with_credentials)
        self.open_login_btn.grid(row=0, column=0, padx=4, pady=4)
        ttk.Button(session_box, text="Comprobar sesion", command=self.refresh_session_status).grid(
            row=0, column=1, padx=4, pady=4
        )
        ttk.Button(session_box, text="Actualizar", command=self.refresh_campaigns_and_progress).grid(
            row=0, column=2, padx=4, pady=4
        )
        self.auto_login_btn = self.open_login_btn

        ttk.Label(
            session_box,
            text="Refresco de progreso: tiempo real (15s) | Player oculto y auto-claim: activos siempre",
        ).grid(row=0, column=3, columnspan=4, padx=(12, 4), pady=4, sticky="w")

        ttk.Label(session_box, textvariable=self.session_status_var, anchor="w").grid(
            row=1, column=0, columnspan=5, sticky="w", padx=4, pady=(0, 4)
        )
        ttk.Label(session_box, text="Usuario activo:").grid(row=1, column=5, sticky="e", padx=(4, 2), pady=(0, 4))
        ttk.Label(session_box, textvariable=self.session_user_var, anchor="w").grid(
            row=1, column=6, sticky="w", padx=(0, 4), pady=(0, 4)
        )

        mining_box = ttk.LabelFrame(frame, text="Minado actual")
        mining_box.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        mining_box.columnconfigure(1, weight=1)

        ttk.Label(mining_box, text="Canal:").grid(row=0, column=0, padx=4, pady=2, sticky="w")
        ttk.Label(mining_box, textvariable=self.general_current_channel_var).grid(row=0, column=1, padx=4, pady=2, sticky="w")
        ttk.Label(mining_box, text="Campaña:").grid(row=1, column=0, padx=4, pady=2, sticky="w")
        ttk.Label(mining_box, textvariable=self.general_current_campaign_var).grid(row=1, column=1, padx=4, pady=2, sticky="w")
        ttk.Label(mining_box, text="Drop actual:").grid(row=2, column=0, padx=4, pady=2, sticky="w")
        ttk.Label(mining_box, textvariable=self.general_current_drop_var).grid(row=2, column=1, padx=4, pady=2, sticky="w")
        self.general_campaign_progress = ttk.Progressbar(mining_box, mode="determinate", maximum=100)
        self.general_campaign_progress.grid(row=3, column=0, columnspan=2, padx=4, pady=(4, 2), sticky="ew")
        ttk.Label(mining_box, textvariable=self.general_current_progress_var).grid(
            row=4, column=0, columnspan=2, padx=4, pady=(0, 4), sticky="w"
        )

        controls = ttk.Frame(frame)
        controls.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        controls.columnconfigure(2, weight=1)
        ttk.Button(controls, text="Cambiar canal", command=self.change_channel_now).grid(row=0, column=0, padx=2)
        ttk.Label(
            controls,
            text="Tip: selecciona un canal en la tabla y pulsa 'Cambiar canal' para saltar al siguiente.",
        ).grid(row=1, column=0, columnspan=3, sticky="w", padx=2, pady=(6, 0))

        columns = (
            "live",
            "channel",
            "campaign",
            "viewers",
            "progress",
            "status",
            "rewards",
        )
        self.queue_tree = ttk.Treeview(frame, columns=columns, show="headings", selectmode="extended")
        self.queue_tree.grid(row=3, column=0, sticky="nsew")
        headings = {
            "live": "●",
            "channel": "Canal",
            "campaign": "Campaña",
            "viewers": "Viewers",
            "progress": "Tiempo",
            "status": "Estado",
            "rewards": "Rewards",
        }
        widths = {
            "live": 42,
            "channel": 180,
            "campaign": 250,
            "viewers": 90,
            "progress": 170,
            "status": 210,
            "rewards": 270,
        }
        for col in columns:
            self.queue_tree.heading(col, text=headings[col])
            self.queue_tree.column(col, width=widths[col], anchor="w")
        self.queue_tree.column("live", anchor="center", stretch=False)
        self.queue_tree.tag_configure("LIVE", background="#eaf8ea")
        self.queue_tree.tag_configure("FINISHED", background="#e8f0ff")
        self.queue_tree.tag_configure("EXPIRED", background="#ffe3e3")
        self.queue_tree.tag_configure("RETRY", background="#fff4db")
        self.queue_tree.tag_configure("WRONG_CATEGORY", background="#ffe8e8")
        self.queue_tree.tag_configure("CONNECTING", background="#eef3ff")
        self.queue_tree.tag_configure("q_live_on", foreground="#1f8f4a")
        self.queue_tree.tag_configure("q_live_off", foreground="#c0392b")
        self.queue_tree.tag_configure("q_live_unknown", foreground="#7f8c8d")
        qscroll = ttk.Scrollbar(frame, orient="vertical", command=self.queue_tree.yview)
        self.queue_tree.configure(yscrollcommand=qscroll.set)
        qscroll.grid(row=3, column=1, sticky="ns")
        self.queue_tree.bind("<Double-1>", lambda _e: self.open_selected_queue_channel())
        self.queue_tree.bind("<Button-3>", self._on_queue_tree_right_click)

        self.queue_menu = tk.Menu(self.root, tearoff=0)
        self.queue_menu.add_command(label="Abrir canal", command=self.open_selected_queue_channel)
        self.queue_menu.add_separator()
        self.queue_menu.add_command(label="Eliminar", command=self.remove_selected_queue_items)
        self.queue_menu.add_command(label="Reiniciar tiempo", command=self.reset_selected_elapsed)
        self.queue_menu.add_command(label="Subir", command=self.move_selected_up)
        self.queue_menu.add_command(label="Bajar", command=self.move_selected_down)

        logs_box = ttk.LabelFrame(frame, text="Logs")
        logs_box.grid(row=4, column=0, sticky="nsew", pady=(8, 0))
        logs_box.rowconfigure(0, weight=1)
        logs_box.columnconfigure(0, weight=1)
        self.log_text = ScrolledText(logs_box, wrap=tk.WORD, height=10)
        self.log_text.grid(row=0, column=0, sticky="nsew")
        self.log_text.configure(state="disabled")

    def _build_campaigns_tab(self) -> None:
        frame = ttk.Frame(self.notebook, padding=8)
        frame.rowconfigure(1, weight=1)
        frame.columnconfigure(0, weight=1)
        self.notebook.add(frame, text="Inventario")

        top = ttk.Frame(frame)
        top.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        top.columnconfigure(0, weight=1)
        ttk.Label(top, text="Campañas y drops visuales").grid(row=0, column=0, sticky="w")
        ttk.Button(top, text="Actualizar", command=self.refresh_campaigns_and_progress).grid(row=0, column=1, padx=(8, 0))

        self.inventory_canvas = tk.Canvas(frame, highlightthickness=0)
        self.inventory_canvas.grid(row=1, column=0, sticky="nsew")
        inv_scroll = ttk.Scrollbar(frame, orient="vertical", command=self.inventory_canvas.yview)
        inv_scroll.grid(row=1, column=1, sticky="ns")
        self.inventory_canvas.configure(yscrollcommand=inv_scroll.set)

        self.inventory_frame = ttk.Frame(self.inventory_canvas)
        self._inventory_canvas_window = self.inventory_canvas.create_window((0, 0), window=self.inventory_frame, anchor="nw")
        self.inventory_frame.columnconfigure(0, weight=1)
        self.inventory_frame.bind("<Configure>", self._on_inventory_frame_configure)
        self.inventory_canvas.bind("<Configure>", self._on_inventory_canvas_configure)
        self.root.bind_all("<MouseWheel>", self._on_inventory_mousewheel, add="+")
        self.root.bind_all("<Button-4>", self._on_inventory_mousewheel, add="+")
        self.root.bind_all("<Button-5>", self._on_inventory_mousewheel, add="+")

    def _build_settings_tab(self) -> None:
        frame = ttk.Frame(self.notebook, padding=8)
        frame.rowconfigure(2, weight=1)
        frame.columnconfigure(0, weight=1)
        self.notebook.add(frame, text="Configuracion")

        header = ttk.LabelFrame(frame, text="Seleccion de juegos para minado automatico")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        header.columnconfigure(1, weight=1)
        header.columnconfigure(3, weight=0)
        ttk.Label(
            header,
            text="El minado automatico esta siempre activo. Marca juegos concretos o 'Todos'.",
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=8, pady=(6, 2))
        ttk.Label(header, text="Idioma:").grid(row=1, column=0, sticky="w", padx=8, pady=(0, 6))
        self.language_combo_var = tk.StringVar(value=self._language_code_to_label(self.language_var.get()))
        self.language_combo = ttk.Combobox(
            header,
            textvariable=self.language_combo_var,
            values=["English", "Español"],
            state="readonly",
            width=14,
        )
        self.language_combo.grid(row=1, column=1, sticky="w", padx=(0, 8), pady=(0, 6))
        self.language_combo.bind("<<ComboboxSelected>>", self._on_language_changed)

        self.settings_games_count_var = tk.StringVar(value="0 juegos seleccionados")
        ttk.Label(header, textvariable=self.settings_games_count_var).grid(
            row=0, column=2, rowspan=2, sticky="e", padx=8, pady=6
        )

        games_wrap = ttk.Frame(frame)
        games_wrap.grid(row=2, column=0, sticky="nsew")
        games_wrap.rowconfigure(0, weight=1)
        games_wrap.columnconfigure(0, weight=1)

        self.settings_games_canvas = tk.Canvas(games_wrap, highlightthickness=0)
        self.settings_games_canvas.grid(row=0, column=0, sticky="nsew")
        settings_scroll = ttk.Scrollbar(games_wrap, orient="vertical", command=self.settings_games_canvas.yview)
        settings_scroll.grid(row=0, column=1, sticky="ns")
        self.settings_games_canvas.configure(yscrollcommand=settings_scroll.set)

        self.settings_games_frame = ttk.Frame(self.settings_games_canvas)
        self._settings_games_canvas_window = self.settings_games_canvas.create_window(
            (0, 0), window=self.settings_games_frame, anchor="nw"
        )
        self.settings_games_frame.bind("<Configure>", self._on_settings_games_frame_configure)
        self.settings_games_canvas.bind("<Configure>", self._on_settings_games_canvas_configure)


    def _close_managed_driver(self, attr_name: str) -> None:
        driver = getattr(self, attr_name, None)
        if driver is None:
            return
        try:
            self.browser.close_driver(driver)
        except Exception:
            pass
        setattr(self, attr_name, None)

    def _on_close(self) -> None:
        self._shutting_down = True
        try:
            self.stop_queue()
        except Exception:
            pass
        self._close_managed_driver("_login_driver")
        self._close_managed_driver("_inventory_driver")
        try:
            self.browser.close_thumb_fetcher()
        except Exception:
            pass
        self.save_config()
        self.root.destroy()

    def _pump_ui_queue(self) -> None:
        try:
            while True:
                method, args, kwargs = self.ui_queue.get_nowait()
                getattr(self, method)(*args, **kwargs)
        except queue.Empty:
            pass
        self.root.after(100, self._pump_ui_queue)

    def _dispatch(self, method: str, *args, **kwargs) -> None:
        self.ui_queue.put((method, args, kwargs))

    def _request_force_channel_switch(self, url: str) -> None:
        target = str(url or "").strip()
        if not target:
            return
        with self._force_switch_lock:
            self._force_switch_urls.add(target)

    def _consume_force_channel_switch(self, url: str) -> bool:
        target = str(url or "").strip()
        if not target:
            return False
        with self._force_switch_lock:
            if target in self._force_switch_urls:
                self._force_switch_urls.remove(target)
                return True
        return False

    def post_log(self, text: str) -> None:
        self._dispatch("_ui_log", text)

    def post_worker_stopped(self) -> None:
        self._dispatch("_ui_worker_stopped")

    def post_update_item(self, url: str, **changes) -> None:
        self._dispatch("_ui_update_item", url, changes)

    def post_increment_elapsed(self, url: str, seconds: int) -> None:
        self._dispatch("_ui_increment_elapsed", url, seconds)

    def post_save_config(self) -> None:
        self._dispatch("_ui_save_config")

    def post_rotate_item(self, url: str) -> None:
        self._dispatch("_ui_rotate_item", url)

    def post_retry_campaign_hint(self, campaign_id: str | None, campaign_name: str | None) -> None:
        self._dispatch("_ui_set_retry_campaign_hint", campaign_id, campaign_name)

    def post_session_status(self, info: dict[str, object]) -> None:
        self._dispatch("_ui_set_session_status", info)

    def _ui_log(self, text: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        localized = self._tr(text)
        logger.info(localized)
        self.log_text.configure(state="normal")
        self.log_text.insert(tk.END, f"[{stamp}] {localized}\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state="disabled")
        self.status_var.set(localized.splitlines()[0][:180])

    def _ui_worker_stopped(self) -> None:
        self.worker = None
        self.status_var.set(self._tr("Cola detenida"))
        self._refresh_queue_tree()
        if not self._shutting_down:
            self.root.after(600, self._ensure_queue_worker_running)

    def _ui_save_config(self) -> None:
        try:
            self.save_config()
        except Exception as exc:
            self._ui_log(f"Error guardando config: {exc}")

    def _ui_update_item(self, url: str, changes: dict[str, object]) -> None:
        item = self._find_item_by_url(url)
        if item is None:
            return
        for k, v in changes.items():
            setattr(item, k, v)
        if item.done and item.status != "FINISHED":
            item.status = "FINISHED"
        if item.status in {"FINISHED", "EXPIRED"}:
            hint_id = str(self._retry_campaign_hint_id or "")
            hint_name = str(self._retry_campaign_hint_name or "")
            item_id = str(item.campaign_id or "")
            item_name = str(item.campaign_name or "").strip().lower()
            if (hint_id and item_id == hint_id) or (hint_name and item_name == hint_name):
                self._retry_campaign_hint_id = None
                self._retry_campaign_hint_name = None
        self._refresh_queue_tree()

    def _ui_increment_elapsed(self, url: str, seconds: int) -> None:
        item = self._find_item_by_url(url)
        if item is None:
            return
        item.elapsed_seconds += max(1, int(seconds))
        if item.done:
            item.status = "FINISHED"
        self._refresh_queue_tree()

    def _ui_rotate_item(self, url: str) -> None:
        item = self._find_item_by_url(url)
        if item is None:
            return
        try:
            idx = self.queue_items.index(item)
        except ValueError:
            return
        if idx < len(self.queue_items) - 1:
            self.queue_items.append(self.queue_items.pop(idx))
            self._refresh_queue_tree()

    def _ui_set_retry_campaign_hint(self, campaign_id: str | None, campaign_name: str | None) -> None:
        self._retry_campaign_hint_id = str(campaign_id or "").strip() or None
        self._retry_campaign_hint_name = str(campaign_name or "").strip().lower() or None

    def _ui_set_login_driver(self, driver) -> None:
        previous = getattr(self, "_login_driver", None)
        self._login_driver = driver
        if previous is not None and previous is not driver:
            try:
                self.browser.close_driver(previous)
            except Exception:
                pass

    def _ui_set_inventory_driver(self, driver) -> None:
        previous = getattr(self, "_inventory_driver", None)
        self._inventory_driver = driver
        if previous is not None and previous is not driver:
            try:
                self.browser.close_driver(previous)
            except Exception:
                pass

    def _ui_messagebox_error(self, title: str, msg: str) -> None:
        messagebox.showerror(self._tr(title), self._tr(msg), parent=self.root)

    def _ui_set_auto_login_state(self, running: bool) -> None:
        state = "disabled" if running else "normal"
        try:
            self.auto_login_btn.configure(state=state)
            self.open_login_btn.configure(state=state)
        except Exception:
            pass

    def _ui_clear_login_password(self) -> None:
        self.login_username_var.set("")

    def _ui_set_session_status(self, info: dict[str, object]) -> None:
        state = str(info.get("state") or "unknown")
        label = str(info.get("label") or "Sesion: desconocida")
        username = str(info.get("username") or "").strip()
        user_id = info.get("user_id")
        self._session_state = state
        prefix = "Sesion"
        if state == "logged_in":
            prefix = "Sesion OK"
        elif state == "logged_out":
            prefix = "Sesion cerrada"
        elif state == "no_session":
            prefix = "Sin sesion"
        elif state == "checking":
            prefix = "Sesion"
        self.session_status_var.set(f"{self._tr(prefix)}: {self._tr(label)}")
        if username:
            self.session_user_var.set(username)
        elif user_id not in (None, ""):
            self.session_user_var.set(f"id {user_id}")
        else:
            self.session_user_var.set("-")
        if state != "checking":
            self._ui_log(f"Estado de sesion: {label}")
        if state in {"logged_out", "no_session"}:
            self._initial_sync_done = False
        if state == "logged_in" and not self._initial_sync_done:
            self._initial_sync_done = True
            self._ui_log("Sesion valida detectada. Cargando campanas y progreso...")
            self.refresh_campaigns_and_progress()
        elif state == "logged_in":
            self._ensure_queue_worker_running()
        self._refresh_general_mining_panel()

    def _find_item_by_url(self, url: str) -> QueueItem | None:
        for item in self.queue_items:
            if item.url == url:
                return item
        return None

    @staticmethod
    def _is_progress_campaign_finished(progress_campaign: KickProgressCampaign | None) -> bool:
        if progress_campaign is None:
            return False
        if progress_campaign.rewards:
            return all(bool(reward.claimed) for reward in progress_campaign.rewards)
        status = str(progress_campaign.status or "").strip().lower()
        return status in {"claimed", "completed", "finished", "done"}

    @staticmethod
    def _resolve_item_progress_campaign(
        item: QueueItem,
        progress_by_id: dict[str, KickProgressCampaign],
        progress_by_name: dict[str, KickProgressCampaign],
    ) -> KickProgressCampaign | None:
        progress_campaign = None
        if item.campaign_id:
            progress_campaign = progress_by_id.get(item.campaign_id)
        if progress_campaign is None and item.campaign_name:
            progress_campaign = progress_by_name.get(item.campaign_name.strip().lower())
        return progress_campaign

    @staticmethod
    def _parse_kick_datetime(value: str | None) -> datetime | None:
        raw = str(value or "").strip()
        if not raw:
            return None
        iso = raw.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(iso)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            pass
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
            try:
                dt = datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
                return dt
            except Exception:
                continue
        return None

    def _is_campaign_expired(self, campaign: KickCampaign | None) -> bool:
        if campaign is None:
            return False
        expired_tokens = {"expired", "ended", "closed", "past"}
        status_values = [
            str(campaign.status or "").strip().lower().replace(" ", "_"),
            str(campaign.progress_status or "").strip().lower().replace(" ", "_"),
        ]
        if any(value in expired_tokens for value in status_values if value):
            return True
        ends_at = self._parse_kick_datetime(campaign.ends_at)
        if ends_at is not None and datetime.now(timezone.utc) >= ends_at:
            return True
        return False

    def _refresh_queue_tree(self) -> None:
        campaign_by_id = {campaign.id: campaign for campaign in self.campaigns if campaign.id}
        campaign_by_name = {
            (campaign.name or "").strip().lower(): campaign
            for campaign in self.campaigns
            if (campaign.name or "").strip()
        }
        progress_by_id = {campaign.id: campaign for campaign in self.progress if campaign.id}
        progress_by_name = {
            (campaign.name or "").strip().lower(): campaign
            for campaign in self.progress
            if (campaign.name or "").strip()
        }
        for iid in self.queue_tree.get_children():
            self.queue_tree.delete(iid)
        for idx, item in enumerate(self.queue_items):
            elapsed = _fmt_seconds(item.elapsed_seconds)
            progress_text = elapsed
            campaign = self._resolve_queue_item_campaign(item, campaign_by_id, campaign_by_name)
            if self._is_campaign_expired(campaign):
                item.status = "EXPIRED"
            progress_campaign = self._resolve_item_progress_campaign(item, progress_by_id, progress_by_name)
            if self._is_progress_campaign_finished(progress_campaign):
                item.status = "FINISHED"
            status_upper = (item.status or "").upper()
            live_dot = "●"
            live_state, viewers = self._get_channel_live_snapshot(
                item.slug,
                max_age_seconds=120.0,
                use_network=False,
            )
            if status_upper == "LIVE":
                live_state = True
            if live_state is True:
                live_tag = "q_live_on"
                viewers_text = str(max(0, int(viewers)))
            elif live_state is False:
                live_tag = "q_live_off"
                viewers_text = "0"
            else:
                live_tag = "q_live_unknown"
                viewers_text = "-"

            drop_status = "-"
            drop_units = "-"
            rewards_summary = "-"
            if progress_campaign is not None:
                drop_status = progress_campaign.status or "-"
                drop_units = str(progress_campaign.progress_units)
                if progress_campaign.rewards:
                    claimed_count = sum(1 for reward in progress_campaign.rewards if reward.claimed)
                    max_percent = max(int(reward.progress * 100) for reward in progress_campaign.rewards)
                    rewards_summary = f"{claimed_count}/{len(progress_campaign.rewards)} {self._tr('Reclamado').lower()} | max {max_percent}%"
            if drop_units != "-" and drop_units:
                if rewards_summary == "-":
                    rewards_summary = f"{drop_units}u"
                else:
                    rewards_summary = f"{rewards_summary} | {drop_units}u"

            if drop_status != "-" and drop_status:
                status_text = f"{self._tr(item.status)} | {self._tr(drop_status)}"
            else:
                status_text = self._tr(item.status)

            status_tag = item.status if item.status in {
                "LIVE", "FINISHED", "EXPIRED", "RETRY", "WRONG_CATEGORY", "CONNECTING"
            } else ""
            tags: list[str] = [live_tag]
            if status_tag:
                tags.insert(0, status_tag)
            self.queue_tree.insert(
                "",
                tk.END,
                iid=str(idx),
                values=(
                    live_dot,
                    item.slug,
                    item.campaign_name or "",
                    viewers_text,
                    progress_text,
                    status_text,
                    rewards_summary,
                ),
                tags=tuple(tags),
            )
        self._refresh_general_mining_panel()
        self.save_config()

    def _refresh_general_mining_panel(self) -> None:
        if not hasattr(self, "general_current_channel_var"):
            return
        current_item: QueueItem | None = None
        if self.worker is not None and self.worker.is_alive():
            for item in self.queue_items:
                if item.status in {"LIVE", "CONNECTING", "RETRY", "WRONG_CATEGORY"}:
                    current_item = item
                    break
        if current_item is None:
            for item in self.queue_items:
                if item.status not in {"FINISHED", "EXPIRED"}:
                    current_item = item
                    break
        if current_item is None:
            self.general_current_channel_var.set("-")
            self.general_current_campaign_var.set("-")
            self.general_current_drop_var.set("-")
            self.general_current_progress_var.set(self._tr("Sin minado activo"))
            if hasattr(self, "general_campaign_progress"):
                self.general_campaign_progress["value"] = 0
            return

        self.general_current_channel_var.set(current_item.slug)
        self.general_current_campaign_var.set(current_item.campaign_name or "-")
        if not hasattr(self, "general_campaign_progress"):
            return

        progress_by_id = {campaign.id: campaign for campaign in self.progress if campaign.id}
        progress_by_name = {
            (campaign.name or "").strip().lower(): campaign
            for campaign in self.progress
            if (campaign.name or "").strip()
        }
        campaign_progress = None
        if current_item.campaign_id:
            campaign_progress = progress_by_id.get(current_item.campaign_id)
        if campaign_progress is None and current_item.campaign_name:
            campaign_progress = progress_by_name.get(current_item.campaign_name.strip().lower())

        active_drop_name = "-"
        active_drop_pct = 0
        detail = self._tr("Sin datos de drop todavía")
        if campaign_progress is not None and campaign_progress.rewards:
            reward = next((r for r in campaign_progress.rewards if not r.claimed), campaign_progress.rewards[-1])
            active_drop_name = reward.name or reward.id
            active_drop_pct = int(max(0, min(100, reward.progress * 100)))
            required = int(reward.required_units or 0)
            if required > 0:
                progressed = int(required * reward.progress)
                remaining = max(0, required - progressed)
                detail = f"{active_drop_pct}% · {progressed}/{required} · restante ~{remaining} min"
            else:
                detail = f"{active_drop_pct}%"
        self.general_current_drop_var.set(active_drop_name)
        self.general_current_progress_var.set(detail)
        self.general_campaign_progress["value"] = active_drop_pct

    @staticmethod
    def _normalize_preferred_games(values: list[str] | None) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in values or []:
            game = str(raw or "").strip()
            if not game:
                continue
            key = game.casefold()
            if key in seen:
                continue
            seen.add(key)
            cleaned.append(game)
        if any(game == ALL_GAMES_TOKEN for game in cleaned):
            return [ALL_GAMES_TOKEN]
        if cleaned:
            return sorted(cleaned, key=lambda s: s.casefold())
        return [ALL_GAMES_TOKEN]

    def _preferred_game_filter(self) -> tuple[bool, set[str]]:
        normalized = self._normalize_preferred_games(self._preferred_games_cached)
        self._preferred_games_cached = normalized
        mine_all = ALL_GAMES_TOKEN in normalized
        selected = {
            game.strip().casefold()
            for game in normalized
            if game != ALL_GAMES_TOKEN and game.strip()
        }
        return (mine_all, selected)

    def _refresh_settings_count_label(self) -> None:
        if not hasattr(self, "settings_games_count_var"):
            return
        mine_all, selected_games = self._preferred_game_filter()
        if mine_all:
            self.settings_games_count_var.set(self._tr("Todos los juegos"))
            return
        self.settings_games_count_var.set(f"{len(selected_games)} {self._tr('juegos seleccionados')}")

    @staticmethod
    def _is_auto_games_channel_item(item: QueueItem) -> bool:
        return AUTO_GAMES_CHANNEL_SOURCE in str(item.notes or "")

    @staticmethod
    def _build_auto_games_item_notes(campaign: KickCampaign, channel: KickChannel, viewers: int) -> str:
        return (
            f"{AUTO_GAMES_CHANNEL_SOURCE} | "
            f"game={campaign.game} | auto={channel.slug} ({max(0, int(viewers))} viewers)"
        )

    @staticmethod
    def _channel_live_sort_key(live: bool | None, viewers: int, slug: str) -> tuple[int, int, str]:
        if live is True:
            rank = 0
        elif live is False:
            rank = 1
        else:
            rank = 2
        return (rank, -max(0, int(viewers)), str(slug or "").casefold())

    def _get_selected_games_from_settings(self) -> list[str]:
        if not hasattr(self, "_settings_game_vars"):
            return list(self._preferred_games_cached)
        selected = [
            game
            for game, var in self._settings_game_vars.items()
            if bool(var.get())
        ]
        return self._normalize_preferred_games(selected)

    def _apply_settings_game_card_style(self, game: str) -> None:
        card = self._settings_game_cards.get(game)
        check = self._settings_game_checks.get(game)
        var = self._settings_game_vars.get(game)
        if card is None or check is None or var is None:
            return
        selected = bool(var.get())
        bg = "#d9f7df" if selected else "#f0f0f0"
        fg = "#1f8f4a" if selected else "#1f1f1f"
        card.configure(bg=bg, highlightbackground="#87c995" if selected else "#cfcfcf")
        for child in card.winfo_children():
            try:
                child.configure(bg=bg)
            except Exception:
                pass
        check.configure(bg=bg, activebackground=bg, fg=fg, activeforeground=fg, selectcolor=bg)

    def _on_settings_game_toggle(self, game: str) -> None:
        current_var = self._settings_game_vars.get(game)
        if current_var is None:
            return

        if game == ALL_GAMES_TOKEN and bool(current_var.get()):
            for other_game, other_var in self._settings_game_vars.items():
                if other_game == ALL_GAMES_TOKEN:
                    continue
                if bool(other_var.get()):
                    other_var.set(False)
                    self._apply_settings_game_card_style(other_game)
        elif game != ALL_GAMES_TOKEN and bool(current_var.get()):
            all_var = self._settings_game_vars.get(ALL_GAMES_TOKEN)
            if all_var is not None and bool(all_var.get()):
                all_var.set(False)
                self._apply_settings_game_card_style(ALL_GAMES_TOKEN)

        self._preferred_games_cached = self._get_selected_games_from_settings()
        self._apply_settings_game_card_style(game)
        self._refresh_settings_count_label()
        self._apply_language_to_ui()
        self._refresh_inventory_view()
        self._auto_queue_selected_games()
        self._refresh_queue_tree()
        self._ensure_queue_worker_running()
        self.save_config()

    def _refresh_settings_games_list(self) -> None:
        if not hasattr(self, "settings_games_frame"):
            return

        self._preferred_games_cached = self._normalize_preferred_games(self._preferred_games_cached)
        selected = set(self._preferred_games_cached)
        discovered: dict[str, str] = {}
        for campaign in self.campaigns:
            game_name = (campaign.game or "").strip()
            if not game_name:
                continue
            if game_name not in discovered:
                discovered[game_name] = self._effective_reward_image_url(None, campaign.game_image)
        for game_name in selected:
            discovered.setdefault(game_name, "")

        all_games = [ALL_GAMES_TOKEN] + sorted(
            [name for name in discovered.keys() if name != ALL_GAMES_TOKEN],
            key=lambda s: s.casefold(),
        )
        self._settings_game_images = dict(discovered)

        for child in self.settings_games_frame.winfo_children():
            child.destroy()
        self._settings_game_vars = {}
        self._settings_game_cards = {}
        self._settings_game_checks = {}

        canvas_width = 980
        try:
            canvas_width = max(760, int(self.settings_games_canvas.winfo_width()))
        except Exception:
            pass
        cols = max(2, min(5, canvas_width // 210))
        for col in range(cols):
            self.settings_games_frame.columnconfigure(col, weight=1, uniform="settings-game-card")

        for idx, game in enumerate(all_games):
            row = idx // cols
            col = idx % cols
            selected_now = game in selected
            bg = "#d9f7df" if selected_now else "#f0f0f0"
            fg = "#1f8f4a" if selected_now else "#1f1f1f"
            display_name = self._tr(ALL_GAMES_LABEL) if game == ALL_GAMES_TOKEN else game

            card = tk.Frame(
                self.settings_games_frame,
                bd=1,
                relief="solid",
                bg=bg,
                highlightthickness=1,
                highlightbackground="#87c995" if selected_now else "#cfcfcf",
            )
            card.grid(row=row, column=col, sticky="nsew", padx=6, pady=6)
            self._settings_game_cards[game] = card

            img_url = self._settings_game_images.get(game, "")
            thumb = self._get_reward_thumb(img_url)
            img_label = tk.Label(card, image=thumb, bg=bg)
            img_label.image = thumb
            img_label.pack(anchor="center", padx=8, pady=(8, 6))
            if img_url:
                self._request_reward_thumb(img_url)

            var = tk.BooleanVar(value=selected_now)
            self._settings_game_vars[game] = var
            check = tk.Checkbutton(
                card,
                text=display_name,
                variable=var,
                bg=bg,
                activebackground=bg,
                fg=fg,
                activeforeground=fg,
                selectcolor=bg,
                wraplength=170,
                justify=tk.CENTER,
                anchor="center",
                command=lambda g=game: self._on_settings_game_toggle(g),
            )
            check.pack(fill=tk.X, padx=6, pady=(0, 8))
            self._settings_game_checks[game] = check
            self._apply_settings_game_card_style(game)

        self._refresh_settings_count_label()

    def _auto_queue_selected_games(self) -> int:
        mine_all, selected_games = self._preferred_game_filter()
        if not self.campaigns:
            return 0
        if not mine_all and not selected_games:
            return 0
        desired_by_url: dict[str, dict[str, object]] = {}
        for campaign in self.campaigns:
            game_key = (campaign.game or "").strip().casefold()
            if not mine_all and game_key not in selected_games:
                continue
            if self._is_campaign_expired(campaign):
                continue
            for channel in campaign.channels:
                slug = (channel.slug or "").strip()
                if not slug:
                    continue
                url = (channel.url or f"https://kick.com/{slug}").strip()
                if not url:
                    continue
                live, viewers = self._get_channel_live_snapshot(
                    slug,
                    max_age_seconds=45.0,
                    use_network=True,
                )
                sort_key = self._channel_live_sort_key(live, viewers, slug)
                previous = desired_by_url.get(url)
                if previous is None or sort_key < previous["sort_key"]:
                    desired_by_url[url] = {
                        "campaign": campaign,
                        "channel": channel,
                        "live": live,
                        "viewers": int(viewers),
                        "sort_key": sort_key,
                    }

        ordered = sorted(desired_by_url.values(), key=lambda d: d["sort_key"])
        existing_by_url: dict[str, QueueItem] = {}
        for item in self.queue_items:
            if item.url and item.url not in existing_by_url:
                existing_by_url[item.url] = item

        added = 0
        updated = 0
        removed = 0
        ordered_urls: set[str] = set()
        final_queue: list[QueueItem] = []

        for row in ordered:
            campaign = row["campaign"]
            channel = row["channel"]
            viewers = int(row["viewers"])
            if not isinstance(campaign, KickCampaign) or not isinstance(channel, KickChannel):
                continue
            url = str(channel.url or f"https://kick.com/{channel.slug}")
            existing = existing_by_url.get(url)
            if existing is None:
                existing = QueueItem(
                    url=url,
                    minutes_target=0,
                    status="PENDING",
                    campaign_id=campaign.id,
                    campaign_name=campaign.name,
                    category_id=campaign.category_id,
                    notes=self._build_auto_games_item_notes(campaign, channel, viewers),
                )
                added += 1
            else:
                before = (
                    existing.campaign_id,
                    existing.campaign_name,
                    existing.category_id,
                    existing.notes,
                    existing.status,
                )
                existing.campaign_id = campaign.id
                existing.campaign_name = campaign.name
                existing.category_id = campaign.category_id
                existing.notes = self._build_auto_games_item_notes(campaign, channel, viewers)
                if existing.status == "EXPIRED":
                    existing.status = "PENDING"
                after = (
                    existing.campaign_id,
                    existing.campaign_name,
                    existing.category_id,
                    existing.notes,
                    existing.status,
                )
                if after != before:
                    updated += 1
            ordered_urls.add(url)
            final_queue.append(existing)

        for item in self.queue_items:
            if item.url in ordered_urls:
                continue
            if self._is_auto_games_channel_item(item):
                removed += 1
                continue
            final_queue.append(item)

        order_or_size_changed = len(final_queue) != len(self.queue_items) or any(
            a is not b for a, b in zip(final_queue, self.queue_items)
        )
        if order_or_size_changed:
            self.queue_items = final_queue

        if added or updated or removed:
            self.post_log(
                f"Canales auto por juegos: {len(ordered)} visibles | +{added} nuevos | ~{updated} actualizados | -{removed} retirados"
            )
        return added

    def _find_queue_item_for_campaign(self, campaign_id: str | None, campaign_name: str | None) -> QueueItem | None:
        cid = str(campaign_id or "").strip()
        cname = str(campaign_name or "").strip().lower()
        for item in self.queue_items:
            if cid and str(item.campaign_id or "").strip() == cid:
                return item
            if cname and str(item.campaign_name or "").strip().lower() == cname:
                return item
        return None

    def _resolve_queue_item_campaign(
        self,
        item: QueueItem,
        campaign_by_id: dict[str, KickCampaign],
        campaign_by_name: dict[str, KickCampaign],
    ) -> KickCampaign | None:
        campaign = None
        if item.campaign_id:
            campaign = campaign_by_id.get(item.campaign_id)
        if campaign is None and item.campaign_name:
            campaign = campaign_by_name.get(item.campaign_name.strip().lower())
        return campaign

    def _get_channel_live_snapshot(
        self,
        slug: str,
        *,
        max_age_seconds: float = 45.0,
        use_network: bool = True,
    ) -> tuple[bool | None, int]:
        now = time.time()
        cached = self._channel_live_cache.get(slug)
        if cached is not None and (now - cached[2]) <= max_age_seconds:
            return (cached[0], cached[1])
        if not use_network:
            if cached is not None:
                return (cached[0], cached[1])
            return (None, 0)
        try:
            info = self.browser.channel_live_status(None, slug)
            live = bool(info.get("live", False))
            viewers = int(info.get("viewer_count") or 0)
            self._channel_live_cache[slug] = (live, viewers, time.time())
            return (live, viewers)
        except Exception:
            if cached is not None:
                return (cached[0], cached[1])
            return (None, 0)

    def _pick_preferred_channel_for_campaign(
        self,
        campaign: KickCampaign,
        *,
        use_network: bool,
    ) -> tuple[KickChannel | None, bool, int]:
        if not campaign.channels:
            return (None, False, 0)

        best_live: KickChannel | None = None
        best_live_viewers = -1
        fallback: KickChannel | None = None
        fallback_viewers = -1

        for channel in campaign.channels:
            live, viewers = self._get_channel_live_snapshot(
                channel.slug,
                max_age_seconds=45.0,
                use_network=use_network,
            )
            if fallback is None or viewers > fallback_viewers:
                fallback = channel
                fallback_viewers = viewers
            if live is True and viewers >= best_live_viewers:
                best_live = channel
                best_live_viewers = viewers

        if best_live is not None:
            return (best_live, True, max(0, best_live_viewers))
        if fallback is not None:
            return (fallback, False, max(0, fallback_viewers))
        return (None, False, 0)

    def _set_item_channel_for_campaign(
        self,
        item: QueueItem,
        campaign: KickCampaign | None,
        *,
        use_network: bool,
    ) -> tuple[bool, int]:
        if campaign is None:
            return (False, 0)
        preferred, is_live, viewers = self._pick_preferred_channel_for_campaign(campaign, use_network=use_network)
        if preferred is None:
            return (False, 0)
        if item.url != preferred.url:
            item.url = preferred.url
        item.campaign_id = campaign.id
        item.campaign_name = campaign.name
        item.category_id = campaign.category_id
        item.notes = f"game={campaign.game} | auto={preferred.slug} ({viewers} viewers)"
        return (is_live, viewers)

    def get_next_queue_item(self) -> QueueItem | None:
        mine_all, selected_games = self._preferred_game_filter()
        auto_filter = (not mine_all) and bool(selected_games)
        campaign_by_id = {campaign.id: campaign for campaign in self.campaigns if campaign.id}
        campaign_by_name = {
            (campaign.name or "").strip().lower(): campaign
            for campaign in self.campaigns
            if (campaign.name or "").strip()
        }
        progress_by_id = {campaign.id: campaign for campaign in self.progress if campaign.id}
        progress_by_name = {
            (campaign.name or "").strip().lower(): campaign
            for campaign in self.progress
            if (campaign.name or "").strip()
        }
        hint_id = str(self._retry_campaign_hint_id or "").strip()
        hint_name = str(self._retry_campaign_hint_name or "").strip().lower()
        ordered_items: list[QueueItem]
        if hint_id or hint_name:
            preferred_items: list[QueueItem] = []
            other_items: list[QueueItem] = []
            for item in self.queue_items:
                item_id = str(item.campaign_id or "").strip()
                item_name = str(item.campaign_name or "").strip().lower()
                if (hint_id and item_id == hint_id) or (hint_name and item_name == hint_name):
                    preferred_items.append(item)
                else:
                    other_items.append(item)
            if preferred_items:
                ordered_items = preferred_items + other_items
            else:
                self._retry_campaign_hint_id = None
                self._retry_campaign_hint_name = None
                ordered_items = list(self.queue_items)
        else:
            ordered_items = list(self.queue_items)
        fallback_item: QueueItem | None = None
        fallback_live_viewers = -1
        for item in ordered_items:
            if item.minutes_target > 0 and item.done:
                item.status = "FINISHED"
                continue
            progress_campaign = self._resolve_item_progress_campaign(item, progress_by_id, progress_by_name)
            if self._is_progress_campaign_finished(progress_campaign):
                item.status = "FINISHED"
            if item.status in {"FINISHED", "EXPIRED"}:
                continue
            if item.status == "STOPPED":
                item.status = "PENDING"
            campaign = self._resolve_queue_item_campaign(item, campaign_by_id, campaign_by_name)
            if self._is_campaign_expired(campaign):
                item.status = "EXPIRED"
                item.notes = "campaign expired"
                continue
            if auto_filter and (item.campaign_id or item.campaign_name):
                if campaign is not None:
                    game_name = (campaign.game or "").strip().casefold()
                    if game_name and game_name not in selected_games:
                        continue
            if self._is_auto_games_channel_item(item):
                live_snapshot, viewers = self._get_channel_live_snapshot(
                    item.slug,
                    max_age_seconds=30.0,
                    use_network=True,
                )
                is_live = live_snapshot is True
            else:
                is_live, viewers = self._set_item_channel_for_campaign(item, campaign, use_network=True)
            if is_live:
                return item
            if fallback_item is None or viewers > fallback_live_viewers:
                fallback_item = item
                fallback_live_viewers = viewers
        return fallback_item

    def add_queue_item_dialog(self) -> None:
        raw = simpledialog.askstring("Añadir canal", "URL de Kick o slug del canal:", parent=self.root)
        if raw is None:
            return
        try:
            url = normalize_kick_url(raw)
        except Exception as exc:
            messagebox.showerror(self._tr("URL inválida"), self._tr(str(exc)), parent=self.root)
            return
        if self._find_item_by_url(url) is not None:
            messagebox.showinfo(self._tr("Ya existe"), self._tr("Ese canal ya está en la cola"), parent=self.root)
            return
        slug = url.rstrip("/").split("/")[-1].strip().lower()
        linked_campaign = self._find_best_campaign_for_channel_slug(slug)
        self.queue_items.append(
            QueueItem(
                url=url,
                minutes_target=0,
                status="PENDING",
                campaign_id=(linked_campaign.id if linked_campaign else None),
                campaign_name=(linked_campaign.name if linked_campaign else None),
                category_id=(linked_campaign.category_id if linked_campaign else None),
                notes=(f"game={linked_campaign.game}" if linked_campaign else ""),
            )
        )
        self._refresh_queue_tree()
        self.status_var.set(f"Añadido {url}")

    def _find_best_campaign_for_channel_slug(self, slug: str) -> KickCampaign | None:
        slug_norm = (slug or "").strip().lower()
        if not slug_norm:
            return None
        candidates: list[KickCampaign] = []
        for campaign in self.campaigns:
            for channel in campaign.channels:
                if (channel.slug or "").strip().lower() == slug_norm:
                    candidates.append(campaign)
                    break
        if not candidates:
            return None
        non_expired = [campaign for campaign in candidates if not self._is_campaign_expired(campaign)]
        if non_expired:
            candidates = non_expired

        finished_tokens = {"claimed", "completed", "finished", "done", "expired", "ended"}
        active_tokens = {"in_progress", "active", "running", "ongoing"}

        def _score(campaign: KickCampaign) -> tuple[int, int]:
            status = str(campaign.progress_status or campaign.status or "").strip().lower()
            claimed = sum(1 for reward in campaign.rewards if reward.claimed)
            pending = max(0, len(campaign.rewards) - claimed)
            if status in active_tokens:
                phase = 0
            elif status in finished_tokens:
                phase = 2
            else:
                phase = 1
            return (phase, -pending)

        candidates.sort(key=_score)
        return candidates[0]

    def _on_queue_tree_right_click(self, event) -> str:
        row_id = self.queue_tree.identify_row(event.y)
        if row_id:
            self.queue_tree.selection_set(row_id)
            self.queue_tree.focus(row_id)
        try:
            self.queue_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.queue_menu.grab_release()
        return "break"

    def remove_selected_queue_items(self) -> None:
        selected = list(self.queue_tree.selection())
        if not selected:
            return
        for idx in sorted((int(iid) for iid in selected), reverse=True):
            if 0 <= idx < len(self.queue_items):
                self.queue_items.pop(idx)
        self._refresh_queue_tree()

    def move_selected_up(self) -> None:
        selected = [int(iid) for iid in self.queue_tree.selection()]
        if not selected:
            return
        for idx in sorted(selected):
            if idx <= 0 or idx >= len(self.queue_items):
                continue
            self.queue_items[idx - 1], self.queue_items[idx] = self.queue_items[idx], self.queue_items[idx - 1]
        self._refresh_queue_tree()
        for idx in [max(0, i - 1) for i in selected]:
            if idx < len(self.queue_items):
                self.queue_tree.selection_add(str(idx))

    def move_selected_down(self) -> None:
        selected = [int(iid) for iid in self.queue_tree.selection()]
        if not selected:
            return
        for idx in sorted(selected, reverse=True):
            if idx < 0 or idx >= len(self.queue_items) - 1:
                continue
            self.queue_items[idx + 1], self.queue_items[idx] = self.queue_items[idx], self.queue_items[idx + 1]
        self._refresh_queue_tree()
        for idx in [min(len(self.queue_items) - 1, i + 1) for i in selected]:
            if idx >= 0:
                self.queue_tree.selection_add(str(idx))

    def clear_finished_queue_items(self) -> None:
        before = len(self.queue_items)
        self.queue_items = [item for item in self.queue_items if item.status != "FINISHED" and not item.done]
        removed = before - len(self.queue_items)
        self._refresh_queue_tree()
        if removed:
            self.status_var.set(f"Eliminados {removed} elementos terminados")

    def open_selected_queue_channel(self) -> None:
        sel = self.queue_tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        if 0 <= idx < len(self.queue_items):
            webbrowser.open(self.queue_items[idx].url)

    def reset_selected_elapsed(self) -> None:
        for iid in self.queue_tree.selection():
            idx = int(iid)
            if 0 <= idx < len(self.queue_items):
                item = self.queue_items[idx]
                item.elapsed_seconds = 0
                item.status = "PENDING"
                item.notes = ""
        self._refresh_queue_tree()

    def change_channel_now(self) -> None:
        target_item: QueueItem | None = None
        selected = list(self.queue_tree.selection())
        if selected:
            try:
                idx = int(selected[0])
                if 0 <= idx < len(self.queue_items):
                    target_item = self.queue_items[idx]
            except Exception:
                target_item = None
        if target_item is None and self.worker is not None and self.worker.current_url:
            target_item = self._find_item_by_url(self.worker.current_url)
        if target_item is None:
            for item in self.queue_items:
                if item.status not in {"FINISHED", "EXPIRED"}:
                    target_item = item
                    break
        if target_item is None:
            self.post_log("No hay canales disponibles para cambiar.")
            return
        self._request_force_channel_switch(target_item.url)
        target_item.status = "RETRY"
        target_item.notes = "cambio manual solicitado"
        self._ui_rotate_item(target_item.url)
        self._refresh_queue_tree()
        self._ensure_queue_worker_running()
        self.post_log(f"Cambio manual: {target_item.slug} -> siguiente canal disponible")

    def start_queue(self, *, silent: bool = False) -> bool:
        if self.worker is not None and self.worker.is_alive():
            return True
        if not self.queue_items:
            if not silent:
                messagebox.showinfo(self._tr("Cola vacía"), self._tr("No hay canales disponibles."), parent=self.root)
            return False
        self.save_config()
        self.worker = QueueWorker(self)
        self.worker.start()
        if not silent:
            self.status_var.set(self._tr("Cola iniciada"))
        return True

    def stop_queue(self) -> None:
        if self.worker is None:
            return
        self.worker.stop()
        self.status_var.set(self._tr("Deteniendo cola..."))

    def _candidate_cookie_sources(self) -> list[str]:
        selected = (self.cookie_source_var.get().strip() or "chrome").lower()
        ordered = [selected, "chrome", "edge", "firefox"]
        result: list[str] = []
        for source in ordered:
            if source not in result:
                result.append(source)
        return result

    def open_login_browser(self) -> None:
        self.login_with_credentials()

    def login_with_credentials(self) -> None:
        if self._auto_login_running:
            return
        self.save_config()
        self._close_managed_driver("_login_driver")

        def task():
            self._auto_login_running = True
            self._dispatch("_ui_set_auto_login_state", True)
            assisted_ctx = None
            try:
                existing_token = self.browser.get_saved_session_token()
                if existing_token:
                    try:
                        existing_info = self.browser.get_session_status()
                    except Exception as exc:
                        self.post_log(f"Sesion guardada no verificable antes de login: {_fmt_exc(exc)}")
                        existing_info = {}
                    if str(existing_info.get("state") or "") == "logged_in":
                        self.post_log("Ya existe una sesion guardada valida. No hace falta iniciar de nuevo.")
                        self.post_session_status(existing_info)
                        return

                preferred = self.cookie_source_var.get().strip() or "chrome"
                self.post_log("Abriendo login de Kick (metodo unico)...")
                assisted_ctx = self.browser.start_assisted_login_browser(browser_hint=preferred)
                self.post_log("Completa el login en la ventana abierta. La app detectara y guardara las cookies sola.")
                login_info = self.browser.wait_for_assisted_login_session(assisted_ctx, timeout_seconds=600.0)
                cookies_count = int(login_info.get("cookies_count") or 0)
                self.post_log(f"Cookies capturadas automaticamente: {cookies_count}")

                provisional_info = {
                    "state": "logged_in",
                    "label": "Sesion iniciada (cookies capturadas)",
                }
                self.post_session_status(provisional_info)
                try:
                    status_info = self.browser.get_session_status()
                    state = str(status_info.get("state") or "")
                    if state == "logged_in":
                        self.post_session_status(status_info)
                    else:
                        self.post_log(
                            f"Sesion capturada, verificacion parcial: {status_info.get('label') or state}"
                        )
                except Exception as exc:
                    self.post_log(f"Sesion capturada, verificacion no disponible: {_fmt_exc(exc)}")
                self._dispatch("_ui_clear_login_password")
                self.post_log("Login completado correctamente.")
                self.post_log("Sesion guardada en el perfil local de la app (no deberia pedir login en cada inicio).")
            except Exception as exc:
                err = _fmt_exc(exc)
                self.post_log(f"Login no completado: {err}")
                self._dispatch("_ui_messagebox_error", "Iniciar sesion", err)
            finally:
                if assisted_ctx is not None:
                    try:
                        self.browser.stop_assisted_login_browser(assisted_ctx)
                    except Exception:
                        pass
                self._auto_login_running = False
                self._dispatch("_ui_set_auto_login_state", False)

        threading.Thread(target=task, daemon=True).start()

    def refresh_session_status(
        self,
        *,
        notify_if_relogin_needed: bool = False,
        auto_import_if_missing: bool = True,
    ) -> None:
        if self._session_check_running:
            return

        def task():
            self._session_check_running = True
            self.post_session_status({"state": "checking", "label": "comprobando..."})
            imported_source = None
            imported_count = 0
            had_saved_cookies = self.browser.has_saved_cookies()
            if auto_import_if_missing and not self.browser.get_saved_session_token():
                for source in self._candidate_cookie_sources():
                    try:
                        imported_count = self.browser.import_browser_cookies(source)
                        imported_source = source
                        break
                    except Exception as exc:
                        if "No Kick cookies found" in str(exc):
                            continue
                        self.post_log(f"No se pudo sincronizar cookies desde {source}: {_fmt_exc(exc)}")
            try:
                info = self.browser.get_session_status()
            except Exception as exc:
                info = {"state": "unknown", "label": f"error al comprobar: {exc}"}
            finally:
                self._session_check_running = False
            if imported_source:
                self.post_log(f"Cookies sincronizadas desde {imported_source}: {imported_count}")
            self.post_session_status(info)
            if notify_if_relogin_needed:
                state = str(info.get("state") or "")
                if had_saved_cookies and state in {"logged_out", "unknown"}:
                    label = str(info.get("label") or "sesion expirada o no valida")
                    self._dispatch(
                        "_ui_messagebox_error",
                        "Sesion caducada",
                        f"La sesion guardada no es valida ({label}). Debes iniciar sesion de nuevo.",
                    )

        threading.Thread(target=task, daemon=True).start()

    def save_interactive_cookies(self) -> None:
        source = self.cookie_source_var.get().strip() or "chrome"
        try:
            count = self.browser.import_browser_cookies(source)
            self.post_log(f"Cookies guardadas/importadas ({count}) desde {source}")
            self.refresh_session_status()
        except Exception as exc:
            messagebox.showerror(self._tr("Error"), self._tr(str(exc)), parent=self.root)

    def import_cookies_from_browser(self) -> None:
        source = self.cookie_source_var.get().strip() or "chrome"

        def task():
            try:
                count = self.browser.import_browser_cookies(source)
                self.post_log(f"Importadas {count} cookies desde {source}")
                self.refresh_session_status()
            except Exception as exc:
                self.post_log(f"Importación de cookies falló: {exc}")
                self._dispatch("_ui_messagebox_error", "Importar cookies", str(exc))

        threading.Thread(target=task, daemon=True).start()

    def open_drops_inventory_page(self) -> None:
        self._close_managed_driver("_inventory_driver")

        def task():
            driver = None
            try:
                driver = self.browser.create_visible_driver(profile_name="interactive-drops")
                self.browser.prime_session_with_cookies(driver)
                self.browser.open_drops_inventory(driver)
                self._dispatch("_ui_set_inventory_driver", driver)
                driver = None
                self.post_log("Página de Drops abierta")
            except Exception as exc:
                self.post_log(f"No se pudo abrir inventario de Drops: {exc}")
            finally:
                if driver is not None:
                    try:
                        self.browser.close_driver(driver)
                    except Exception:
                        pass

        threading.Thread(target=task, daemon=True).start()

    def auto_claim_now(self) -> None:
        def task():
            driver = getattr(self, "_inventory_driver", None)
            temp_driver = None
            try:
                if driver is None:
                    # Keep manual one-shot claim headless unless the user explicitly opened
                    # the inventory page (which provides a visible driver).
                    temp_driver = self.browser.create_helper_driver(profile_name="interactive-claim")
                    self.browser.prime_session_with_cookies(temp_driver)
                    driver = temp_driver
                clicked = self.browser.best_effort_claim_all(driver)
                self.post_log(f"Auto-claim manual: {clicked} click(s)")
            except Exception as exc:
                self.post_log(f"Auto-claim manual falló: {exc}")
            finally:
                if temp_driver is not None:
                    try:
                        self.browser.close_driver(temp_driver)
                    except Exception:
                        pass

        threading.Thread(target=task, daemon=True).start()

    def refresh_campaigns_only(self) -> None:
        if self._session_state != "logged_in":
            self.post_log("No se pueden cargar campanas sin sesion activa. Pulsa 'Iniciar sesion'.")
            self.refresh_session_status()
            return
        self._refresh_campaigns_worker(include_progress=False, silent=False)

    def refresh_campaigns_and_progress(self, *, silent: bool = False) -> None:
        if self._session_state != "logged_in":
            if not silent:
                self.post_log("No se pueden cargar campanas/progreso sin sesion activa. Pulsa 'Iniciar sesion'.")
            self.refresh_session_status()
            return
        self._refresh_campaigns_worker(include_progress=True, silent=silent)

    def _refresh_campaigns_worker(self, *, include_progress: bool, silent: bool) -> None:
        if self._refresh_campaigns_running:
            if not silent:
                self.post_log("Ya hay una actualización de campañas en curso")
            return
        def task():
            self._refresh_campaigns_running = True
            if not silent:
                self.post_log("Consultando campañas de Kick...")
            try:
                if include_progress:
                    campaigns_raw, progress_raw = self.browser.fetch_campaigns_and_progress()
                    campaigns = parse_campaigns_response(campaigns_raw)
                    progress = parse_progress_response(progress_raw)
                    merge_campaigns_with_progress(campaigns, progress)
                    self._dispatch("_ui_set_campaigns_and_progress", campaigns, progress)
                    if not silent:
                        self.post_log(f"Campañas: {len(campaigns)} | Progreso: {len(progress)}")
                else:
                    campaigns_raw = self.browser.fetch_campaigns()
                    campaigns = parse_campaigns_response(campaigns_raw)
                    self._dispatch("_ui_set_campaigns", campaigns)
                    if not silent:
                        self.post_log(f"Campañas cargadas: {len(campaigns)}")
            except Exception as exc:
                self.post_log(f"Error cargando campañas: {exc}")
                if not silent:
                    self._dispatch("_ui_messagebox_error", "Campañas", str(exc))
            finally:
                self._refresh_campaigns_running = False

        threading.Thread(target=task, daemon=True).start()

    def refresh_progress(self, *, silent: bool = False) -> None:
        if self._session_state != "logged_in":
            if not silent:
                self.post_log("No se puede cargar progreso: sesión no iniciada o expirada.")
            return
        if self._refresh_progress_running:
            return
        def task():
            self._refresh_progress_running = True
            if not silent:
                self.post_log("Consultando progreso de Drops...")
            try:
                progress_raw = self.browser.fetch_progress()
                progress = parse_progress_response(progress_raw)
                self._dispatch("_ui_set_progress", progress)
                if not silent:
                    self.post_log(f"Progreso cargado: {len(progress)} campañas")
            except Exception as exc:
                self.post_log(f"Error cargando progreso: {exc}")
                if not silent:
                    self._dispatch("_ui_messagebox_error", "Progreso", str(exc))
            finally:
                self._refresh_progress_running = False

        threading.Thread(target=task, daemon=True).start()

    def _ui_set_campaigns(self, campaigns: list[KickCampaign]) -> None:
        self.campaigns = campaigns
        self._last_campaigns_refresh_ts = time.time()
        self.campaign_map = {c.id: c for c in campaigns}
        self._refresh_campaign_tree()
        self._refresh_campaign_detail(None)
        self._refresh_settings_games_list()
        self._refresh_inventory_view()
        self._auto_queue_selected_games()
        self._refresh_queue_tree()
        self._ensure_queue_worker_running()

    def _ui_set_progress(self, progress: list[KickProgressCampaign]) -> None:
        self.progress = progress
        self._last_progress_refresh_ts = time.time()
        if self.campaigns:
            merge_campaigns_with_progress(self.campaigns, self.progress)
            self._refresh_campaign_tree()
            self._refresh_campaign_detail(self._selected_campaign())
        self._refresh_inventory_view()
        self._refresh_queue_tree()
        self._ensure_queue_worker_running()

    def _ui_set_campaigns_and_progress(
        self,
        campaigns: list[KickCampaign],
        progress: list[KickProgressCampaign],
    ) -> None:
        self.campaigns = campaigns
        self.progress = progress
        self._last_progress_refresh_ts = time.time()
        self._last_campaigns_refresh_ts = time.time()
        self.campaign_map = {c.id: c for c in campaigns}
        self._refresh_campaign_tree()
        self._refresh_campaign_detail(None)
        self._refresh_settings_games_list()
        self._refresh_inventory_view()
        self._auto_queue_selected_games()
        self._refresh_queue_tree()
        self._ensure_queue_worker_running()

    def _refresh_campaign_tree(self) -> None:
        # Tab Inventario en modo visual-only: no hay tabla interactiva de campañas.
        return

    def _refresh_progress_tree(self) -> None:
        # Progreso integrado en la vista de cola.
        self._refresh_queue_tree()

    def _selected_campaign(self) -> KickCampaign | None:
        return None

    def _on_campaign_select(self, _event=None) -> None:
        return

    @staticmethod
    def _campaign_live_tag(live: bool | None) -> str:
        if live is True:
            return "live_on"
        if live is False:
            return "live_off"
        return "live_unknown"

    @staticmethod
    def _campaign_live_viewers_text(live: bool | None, viewers: int) -> str:
        if live is True:
            return str(max(0, int(viewers)))
        return "-"

    def _probe_campaign_channels_live(self, campaign: KickCampaign, probe_token: int) -> None:
        rate_limited = False
        for channel in campaign.channels:
            if probe_token != self._campaign_live_probe_token:
                return
            slug = channel.slug
            now = time.time()
            cached = self._channel_live_cache.get(slug)
            if cached is not None and (now - cached[2]) < 120:
                self._dispatch(
                    "_ui_update_campaign_channel_live",
                    campaign.id,
                    slug,
                    cached[0],
                    cached[1],
                    probe_token,
                )
                continue
            if rate_limited:
                self._dispatch(
                    "_ui_update_campaign_channel_live",
                    campaign.id,
                    slug,
                    None,
                    0,
                    probe_token,
                )
                continue
            try:
                info = self.browser.channel_live_status(None, slug)
                live = bool(info.get("live", False))
                viewers = int(info.get("viewer_count") or 0)
            except Exception as exc:
                live = None
                viewers = 0
                if "429" in str(exc):
                    rate_limited = True
                    self.post_log("Rate limit consultando estado LIVE de canales de campaña. Se muestra estado desconocido.")
            self._channel_live_cache[slug] = (live, viewers, time.time())
            self._dispatch(
                "_ui_update_campaign_channel_live",
                campaign.id,
                slug,
                live,
                viewers,
                probe_token,
            )
            time.sleep(0.08)

    def _ui_update_campaign_channel_live(
        self,
        campaign_id: str,
        slug: str,
        live: bool | None,
        viewers: int,
        probe_token: int,
    ) -> None:
        return

    def _refresh_campaign_detail(self, campaign: KickCampaign | None) -> None:
        self._campaign_live_probe_token += 1
        self._campaign_channel_by_slug.clear()
        return

    def _clear_rewards_gallery(self) -> None:
        self._reward_card_image_labels.clear()
        return

    @staticmethod
    def _effective_reward_image_url(reward_url: str | None, campaign_image_url: str | None) -> str:
        reward = str(reward_url or "").strip()
        campaign_img = str(campaign_image_url or "").strip()
        return reward or campaign_img

    def _render_rewards_gallery(self, campaign: KickCampaign) -> None:
        return

    def open_selected_campaign_channel(self) -> None:
        messagebox.showinfo(self._tr("Inventario"), self._tr("Este tab es solo visual."), parent=self.root)

    def add_selected_campaign_channel_to_queue(self) -> None:
        messagebox.showinfo(self._tr("Inventario"), self._tr("Este tab es solo visual."), parent=self.root)

    def add_all_channels_from_selected_campaign(self) -> None:
        messagebox.showinfo(self._tr("Inventario"), self._tr("Este tab es solo visual."), parent=self.root)

    def _add_campaign_channel_to_queue(
        self,
        url: str,
        campaign: KickCampaign,
        *,
        silent: bool = False,
    ) -> bool:
        if self._find_item_by_url(url) is not None:
            if not silent:
                messagebox.showinfo(self._tr("Ya existe"), self._tr("Ese canal ya está en la cola"), parent=self.root)
            return False
        self.queue_items.append(
            QueueItem(
                url=url,
                minutes_target=0,
                status="PENDING",
                campaign_id=campaign.id,
                campaign_name=campaign.name,
                category_id=campaign.category_id,
                notes=f"game={campaign.game}",
            )
        )
        if not silent:
            self._refresh_queue_tree()
            self.status_var.set(f"Añadido {url} ({campaign.name})")
        return True


def main() -> None:
    if sys.platform == "win32":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass
    root = tk.Tk()
    _apply_window_icon(root)
    try:
        style = ttk.Style(root)
        available = set(tkfont.families(root))
        if "Segoe UI" in available:
            family = "Segoe UI"
        elif "Tahoma" in available:
            family = "Tahoma"
        else:
            family = "TkDefaultFont"
        default_font = tkfont.nametofont("TkDefaultFont")
        default_font.configure(family=family, size=10)
        text_font = tkfont.nametofont("TkTextFont")
        text_font.configure(family=family, size=10)
        heading_font = tkfont.nametofont("TkHeadingFont")
        heading_font.configure(family=family, size=10, weight="bold")
        root.option_add("*Font", default_font)
        style.configure(".", font=default_font)
        style.configure("Treeview.Heading", font=heading_font)
    except Exception:
        pass
    app = KickMinerApp(root, _app_base_dir())
    app.post_log("Aplicación iniciada")
    app.post_log("La app intentara restaurar la sesion guardada automaticamente al iniciar.")
    app.post_log("Si no hay sesion valida, pulsa 'Iniciar sesion' para autenticar de nuevo.")
    root.mainloop()


if __name__ == "__main__":
    main()
