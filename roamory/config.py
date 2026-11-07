"""Roamory configuration with explicit, non-destructive legacy compatibility."""
import os
from pathlib import Path

# These four identifiers are data/config compatibility, not active branding.
LEGACY_DB_ENV = "TRAVELMIND_DB"
LEGACY_LOG_ENV = "TRAVELMIND_LOG_FILE"
LEGACY_DB_PATH = Path("data/travelmind.sqlite3")
LEGACY_TIMELINE_NAMESPACE = "travelmind"


def database_path(path=None):
    if path:
        return Path(path)
    configured = os.getenv("ROAMORY_DB") or os.getenv(LEGACY_DB_ENV)
    if configured:
        return Path(configured)
    current = Path("data/roamory.sqlite3")
    # Existing installations keep using the original file, including journals
    # and image BLOBs. Do not silently copy, rename or rewrite a live database.
    return current if current.exists() or not LEGACY_DB_PATH.exists() else LEGACY_DB_PATH


def log_path():
    return Path(os.getenv("ROAMORY_LOG_FILE") or os.getenv(LEGACY_LOG_ENV) or "data/backend.log")
