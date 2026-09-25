from __future__ import annotations

import datetime as _dt
from html import escape

from aqt import gui_hooks

WARNING_TEXT = "⚠ 作成から24時間以内の新規カードです"
WARNING_STYLE = (
    "margin: 0 0 12px 0;"
    "padding: 10px 14px;"
    "border-radius: 10px;"
    "background: #c62828;"
    "color: #ffffff;"
    "font-weight: 700;"
    "font-size: 18px;"
    "text-align: center;"
    "box-shadow: 0 2px 8px rgba(0, 0, 0, 0.2);"
)
WARN_WITHIN_HOURS = 24


def _is_new_card(card) -> bool:
    try:
        # new card: type=0, queue=0 in current Anki.
        return int(card.type) == 0 or int(card.queue) == 0
    except Exception:
        return False


def _created_at_from_card(card):
    try:
        # Card ID is based on creation timestamp in milliseconds.
        ts_ms = int(card.id)
        return _dt.datetime.fromtimestamp(ts_ms / 1000)
    except Exception:
        return None


def _is_within_warn_window(card) -> bool:
    created_at = _created_at_from_card(card)
    if created_at is None:
        return False

    now = _dt.datetime.now()
    age = now - created_at
    return _dt.timedelta(0) <= age < _dt.timedelta(hours=WARN_WITHIN_HOURS)


def _should_warn(card, kind: str) -> bool:
    if kind not in ("reviewQuestion", "reviewAnswer"):
        return False
    if not _is_new_card(card):
        return False
    return _is_within_warn_window(card)


def _warning_html() -> str:
    return (
        f'<div id="ishida-today-created-warning" style="{escape(WARNING_STYLE, quote=True)}">'
        f'{escape(WARNING_TEXT)}'
        '</div>'
    )


def _prepend_warning(text: str, card, kind: str) -> str:
    if _should_warn(card, kind):
        return _warning_html() + text
    return text


gui_hooks.card_will_show.append(_prepend_warning)
