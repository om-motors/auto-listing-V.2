"""Überwacht den Eingang-Ordner und startet die Pipeline automatisch.

Läuft dauerhaft (per launchd oder manuell):
    .venv/bin/python -m autolister.watcher

Logik: Sobald neue Dateien im Eingang auftauchen, wird gewartet, bis
SETTLE_SECONDS lang Ruhe ist (damit AirDrop/Uploads fertig sind), dann
läuft die komplette Pipeline über alle Produkte im Eingang.
"""
from __future__ import annotations

import logging
import threading
import time

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from . import config, pipeline

log = logging.getLogger("autolister")


class _Handler(FileSystemEventHandler):
    def __init__(self):
        self.last_event = 0.0
        self.dirty = False
        self.lock = threading.Lock()

    def on_any_event(self, event):
        name = getattr(event, "src_path", "") or ""
        if "/." in name or name.endswith(".DS_Store"):
            return
        with self.lock:
            self.last_event = time.time()
            self.dirty = True


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    config.ensure_dirs()
    handler = _Handler()
    observer = Observer()
    observer.schedule(handler, str(config.EINGANG), recursive=True)
    observer.start()
    log.info("Watcher läuft — beobachte %s", config.EINGANG)

    # Beim Start einmal alles Liegengebliebene verarbeiten
    if pipeline.find_product_groups(config.EINGANG):
        handler.dirty = True
        handler.last_event = time.time() - config.SETTLE_SECONDS

    processing = False
    try:
        while True:
            time.sleep(2)
            with handler.lock:
                ready = (handler.dirty and not processing
                         and time.time() - handler.last_event >= config.SETTLE_SECONDS)
                if ready:
                    handler.dirty = False
            if ready:
                processing = True
                try:
                    pipeline.process_all()
                finally:
                    processing = False
    except KeyboardInterrupt:
        pass
    finally:
        observer.stop()
        observer.join()


if __name__ == "__main__":
    main()
