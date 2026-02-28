from __future__ import annotations

import sys
import logging
from pathlib import Path
from logging.handlers import RotatingFileHandler
from multiprocessing import freeze_support

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


def _init_ssl() -> None:
    try:
        import truststore  # type: ignore
    except Exception:
        return
    try:
        truststore.inject_into_ssl()
    except Exception:
        pass


def _init_logging() -> None:
    base = Path(__file__).resolve().parent.parent
    logs_dir = base / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / "app.log"
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    # Avoid duplicating handlers on re-entry.
    has_app_log_handler = any(
        isinstance(h, RotatingFileHandler) and getattr(h, "baseFilename", "").endswith("app.log")
        for h in root.handlers
    )
    if not has_app_log_handler:
        # Keep only logs from the current app run.
        try:
            log_path.write_text("", encoding="utf-8")
        except Exception:
            pass
        file_handler = RotatingFileHandler(
            log_path,
            maxBytes=2_000_000,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)


def _init_excepthook() -> None:
    def _hook(exc_type, exc_value, exc_tb):
        logging.getLogger("KickDrops").exception(
            "Unhandled exception",
            exc_info=(exc_type, exc_value, exc_tb),
        )
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = _hook


if __name__ == "__main__":
    freeze_support()
    _init_logging()
    _init_excepthook()
    _init_ssl()
    from kick_app import main

    main()
