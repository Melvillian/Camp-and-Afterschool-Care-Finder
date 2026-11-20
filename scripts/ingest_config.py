"""Shared configuration for the 2027 camp ingestion pipeline."""
import datetime as dt
import json
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parents[1] / "data" / "ingest_config.json"

REMINDER = ('REMINDER: the volunteer Google Sheet is still editable by anyone with the link. '
            'Restrict editing, set up the service-account key, then set "public_edit" to false '
            'in data/ingest_config.json.')


def load() -> dict:
    """Return the ingestion config from data/ingest_config.json."""
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def reminder_due(config: dict, today: dt.date) -> str | None:
    """Return the reminder message once the Sheet must be secured, else None."""
    if config["public_edit"] and today >= dt.date.fromisoformat(config["secure_by"]):
        return REMINDER
    return None
