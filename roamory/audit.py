"""Small rotating backend audit log; never record credentials or request headers."""
import json
import logging
from logging.handlers import RotatingFileHandler
from threading import Lock
from .config import log_path

_lock = Lock()
_logger = None

def audit(event, **fields):
    global _logger
    with _lock:
        if _logger is None:
            path = log_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            logger = logging.getLogger("roamory.audit")
            logger.setLevel(logging.INFO)
            logger.propagate = False
            handler = RotatingFileHandler(path, maxBytes=2_000_000, backupCount=2, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
            logger.addHandler(handler)
            _logger = logger
        _logger.info(json.dumps({"event": event, **fields}, ensure_ascii=False))
