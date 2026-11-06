"""Small rotating backend audit log; never record credentials or request headers."""
import json
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
from threading import Lock

_lock = Lock()
_logger = None

def audit(event, **fields):
    global _logger
    with _lock:
        if _logger is None:
            path = Path(os.getenv("TRAVELMIND_LOG_FILE", "data/backend.log"))
            path.parent.mkdir(parents=True, exist_ok=True)
            logger = logging.getLogger("travelmind.audit")
            logger.setLevel(logging.INFO)
            logger.propagate = False
            handler = RotatingFileHandler(path, maxBytes=2_000_000, backupCount=2, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
            logger.addHandler(handler)
            _logger = logger
        _logger.info(json.dumps({"event": event, **fields}, ensure_ascii=False))
