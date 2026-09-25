# -*- coding: utf-8 -*-
"""
New Cards Created Order

Repositions Anki's new-card queue so new cards are introduced in note-created order.
Designed for Anki 25.09.x and recent 2.1+ releases.
"""

from __future__ import annotations

import time
from typing import Any, Iterable, Optional

from anki.utils import int_time
from aqt import gui_hooks, mw
from aqt.qt import QAction, QTimer
from aqt.utils import qconnect, showInfo, tooltip

ADDON_NAME = "New Cards Created Order"
MENU_TEXT = "新規カードを作成日順に並べ直す"

DEFAULT_CONFIG = {
    "auto_reorder_on_profile_open": True,
    "auto_reorder_after_sync": True,
    "auto_reorder_when_deck_browser_is_shown": True,
    "show_auto_tooltip": False,
    "oldest_first": True,
    "starting_due_number": 1,
    "include_suspended_and_buried_new_cards": True,
}

_last_auto_run = 0.0
_AUTO_RUN_MIN_INTERVAL_SECONDS = 20.0


def _config() -> dict[str, Any]:
    cfg = mw.addonManager.getConfig(__name__) if mw and mw.addonManager else None
    merged = dict(DEFAULT_CONFIG)
    if isinstance(cfg, dict):
        merged.update(cfg)
    return merged


def _collection_is_ready() -> bool:
    return bool(mw and getattr(mw, "col", None) and not getattr(mw.col, "db", None) is None)


def _safe_usn() -> int:
    try:
        return int(mw.col.usn())
    except Exception:
        return -1


def _mark_collection_modified() -> None:
    col = mw.col
    for method_name in ("setMod", "set_modified"):
        method = getattr(col, method_name, None)
        if callable(method):
            try:
                method()
                break
            except Exception:
                pass
    try:
        col.save()
    except Exception:
        pass


def _refresh_ui() -> None:
    try:
        if mw:
            mw.reset()
    except Exception:
        pass


def _new_card_rows(oldest_first: bool, include_hidden: bool) -> list[tuple[int, int]]:
    """Return (card_id, current_due) rows in desired introduction order."""
    direction = "asc" if oldest_first else "desc"

    queue_clause = "c.type = 0"
    if not include_hidden:
        # queue=0 is active new. Negative queues are suspended/buried.
        queue_clause = "c.type = 0 and c.queue = 0"

    # Notes.id is the timestamp Anki shows as the Created column in the browser.
    # c.ord keeps siblings from the same note in template order.
    sql = f"""
        select c.id, c.due
        from cards c
        join notes n on n.id = c.nid
        where {queue_clause}
          and c.odid = 0
        order by n.id {direction}, c.ord asc, c.id asc
    """
    return [(int(cid), int(due)) for cid, due in mw.col.db.all(sql)]


def reorder_new_cards_by_created_date(show_result: bool = True, automatic: bool = False) -> Optional[int]:
    """Reposition all new cards by note-created date.

    Returns the number of cards whose due position was changed.
    """
    global _last_auto_run

    if not _collection_is_ready():
        return None

    cfg = _config()
    if automatic:
        now = time.monotonic()
        if now - _last_auto_run < _AUTO_RUN_MIN_INTERVAL_SECONDS:
            return None
        _last_auto_run = now

    oldest_first = bool(cfg.get("oldest_first", True))
    include_hidden = bool(cfg.get("include_suspended_and_buried_new_cards", True))

    try:
        start = int(cfg.get("starting_due_number", 1))
    except Exception:
        start = 1
    if start < 0:
        start = 0

    rows = _new_card_rows(oldest_first=oldest_first, include_hidden=include_hidden)
    if not rows:
        if show_result:
            showInfo("新規カードはありません。")
        return 0

    usn = _safe_usn()
    mod = int_time()
    updates: list[tuple[int, int, int, int]] = []

    for offset, (cid, current_due) in enumerate(rows):
        new_due = start + offset
        if current_due != new_due:
            updates.append((new_due, usn, mod, cid))

    if updates:
        mw.col.db.executemany(
            "update cards set due = ?, usn = ?, mod = ? where id = ?",
            updates,
        )
        _mark_collection_modified()
        _refresh_ui()

    changed = len(updates)
    total = len(rows)
    if show_result:
        showInfo(f"新規カードを作成日順に並べ直しました。\n対象: {total}枚\n変更: {changed}枚")
    elif automatic and bool(cfg.get("show_auto_tooltip", False)) and changed:
        tooltip(f"{ADDON_NAME}: {changed}枚の新規カードを作成日順に並べ直しました。")

    return changed


def _run_manual() -> None:
    reorder_new_cards_by_created_date(show_result=True, automatic=False)


def _run_auto() -> None:
    reorder_new_cards_by_created_date(show_result=False, automatic=True)


def _schedule_auto_run(delay_ms: int = 1000) -> None:
    QTimer.singleShot(delay_ms, _run_auto)


def _on_profile_open() -> None:
    if _config().get("auto_reorder_on_profile_open", True):
        _schedule_auto_run(1500)


def _on_sync_finish(*args: Any, **kwargs: Any) -> None:
    if _config().get("auto_reorder_after_sync", True):
        _schedule_auto_run(1500)


def _on_deck_browser_render(*args: Any, **kwargs: Any) -> None:
    if _config().get("auto_reorder_when_deck_browser_is_shown", True):
        _schedule_auto_run(500)


def _add_menu_item() -> None:
    action = QAction(MENU_TEXT, mw)
    qconnect(action.triggered, _run_manual)
    mw.form.menuTools.addAction(action)


_add_menu_item()

gui_hooks.profile_did_open.append(_on_profile_open)

# Hook names have varied across Anki releases. Register the ones that exist.
if hasattr(gui_hooks, "sync_did_finish"):
    gui_hooks.sync_did_finish.append(_on_sync_finish)
if hasattr(gui_hooks, "deck_browser_will_render_content"):
    gui_hooks.deck_browser_will_render_content.append(_on_deck_browser_render)
elif hasattr(gui_hooks, "deck_browser_did_render"):
    gui_hooks.deck_browser_did_render.append(_on_deck_browser_render)
