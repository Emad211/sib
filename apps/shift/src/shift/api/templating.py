"""Jinja2 environment for the server-rendered login, enrollment and device pages."""

import json
from pathlib import Path

from fastapi.templating import Jinja2Templates

from shift.domain.shift_events.base import kinds

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def outbox_labels_json() -> str:
    """Persian names of the queueable event types, so a held record never shows a raw type id."""
    labels = {kind.event_type: kind.label_fa for kind in kinds()}
    text = json.dumps(labels, ensure_ascii=False, separators=(",", ":"))
    return text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


templates.env.globals["outbox_labels_json"] = outbox_labels_json
