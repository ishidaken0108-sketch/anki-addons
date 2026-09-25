# -*- coding: utf-8 -*-
# Genuine Cloze Number in Browser + Column (v6, robust row fill)
#
# Changes in v6:
# - Accepts *any* hook signature for browser_did_fetch_row (3~5 args)
# - Finds table/row/active columns heuristically
# - If active columns list is unavailable, falls back to writing into the LAST visible cell
#   (useful on builds that don't pass active columns to the hook)
#
# Works on Anki 24.11 Qt5/PyQt5 ("ao") and newer.

from __future__ import annotations

from aqt import gui_hooks
from aqt.browser import Browser
from anki.cards import Card
from anki.consts import MODEL_CLOZE

# Qt imports (prefer Qt6, fall back to Qt5)
try:
    from PyQt6.QtWidgets import QLabel, QWidget, QHBoxLayout
    from PyQt6.QtCore import Qt
except Exception:  # pragma: no cover
    from PyQt5.QtWidgets import QLabel, QWidget, QHBoxLayout  # type: ignore
    from PyQt5.QtCore import Qt  # type: ignore

# Browser table API
try:
    from aqt.browser import Column
except Exception:
    Column = None  # type: ignore

import re

LABEL_OBJECT_NAME = "genuineClozeLabel__addon"
COLUMN_KEY = "genuineClozeNumber__addon"
CLOZE_RE = re.compile(r"\{\{c(\d+)::", flags=re.IGNORECASE)

# ---------- Label in search bar ----------

def _ensure_label(browser: Browser):
    lbl = browser.findChild(QLabel, LABEL_OBJECT_NAME)
    if lbl:
        return lbl
    parent = browser.form.searchEdit.parentWidget()
    layout = parent.layout()
    if layout is None:
        layout = QHBoxLayout(parent)
        parent.setLayout(layout)
    lbl = QLabel(parent)
    lbl.setObjectName(LABEL_OBJECT_NAME)
    try:
        lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)  # type: ignore[attr-defined]
    except Exception:
        try:
            lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)  # type: ignore[attr-defined]
        except Exception:
            pass
    lbl.setToolTip("このカードの『genuine』cloze番号（cN）を表示します。")
    lbl.setStyleSheet("margin-left: 12px; font-weight: 600;")
    layout.addWidget(lbl)
    return lbl

def _is_cloze(card: Card) -> bool:
    try:
        nt = card.note_type()
        return bool(nt and nt.get("type") == MODEL_CLOZE)
    except Exception:
        return False

def _update_label(browser: Browser) -> None:
    try:
        lbl = _ensure_label(browser)
        card = getattr(browser, "card", None)
        if not card:
            lbl.setText("Genuine Cloze #: -")
            return
        if _is_cloze(card):
            lbl.setText(f"Genuine Cloze #: {card.ord + 1}")
        else:
            lbl.setText("Genuine Cloze #: -")
    except Exception:
        try:
            lbl = _ensure_label(browser)
            lbl.setText("Genuine Cloze #: -")
        except Exception:
            pass

def _connect_selection_signal(browser: Browser) -> None:
    try:
        tv = browser.form.tableView
        sm = tv.selectionModel()
        if sm and not getattr(browser, "_genuine_cloze_sel_connected", False):
            sm.selectionChanged.connect(lambda *_: _update_label(browser))  # type: ignore[attr-defined]
            browser._genuine_cloze_sel_connected = True
    except Exception:
        pass

def _on_browser_ready(browser: Browser, *args, **kwargs) -> None:
    _ensure_label(browser)
    _connect_selection_signal(browser)
    _update_label(browser)

def _on_row_changed(browser: Browser, *args, **kwargs) -> None:
    _update_label(browser)

def _on_search(browser: Browser, *args, **kwargs) -> None:
    _update_label(browser)

def _on_sidebar_change(browser: Browser, *args, **kwargs) -> None:
    _update_label(browser)

# ---------- Column helpers ----------

def _note_cloze_numbers(note) -> list[int]:
    try:
        text = "\n".join(note.fields)  # type: ignore[attr-defined]
        return sorted({int(n) for n in CLOZE_RE.findall(text)})
    except Exception:
        return []

def _register_columns(columns_dict) -> None:
    if Column is None:
        return
    if COLUMN_KEY in columns_dict:
        return
    col = Column()
    # id/key/labels (set whatever exists on this build)
    for attr, val in (("key", COLUMN_KEY), ("id", COLUMN_KEY)):
        try:
            if hasattr(col, attr) and not getattr(col, attr, None):
                setattr(col, attr, val)
        except Exception:
            pass
    if hasattr(col, "cards_mode_label"):
        col.cards_mode_label = "Cloze # (genuine)"  # type: ignore[attr-defined]
    elif hasattr(col, "label"):
        col.label = "Cloze # (genuine)"
    if hasattr(col, "notes_mode_label"):
        col.notes_mode_label = "Cloze #s (genuine)"  # type: ignore[attr-defined]
    columns_dict[COLUMN_KEY] = col

def _active_iter(active_columns):
    """Yield (index, column_key) for both string keys or Column objects."""
    for idx, col in enumerate(active_columns):
        if isinstance(col, str):
            yield idx, col
        else:
            key = getattr(col, "key", None) or getattr(col, "id", None) or getattr(col, "label", None)
            yield idx, key

def _get_active_columns_from_table(table):
    # Try multiple access paths; different builds store it differently
    for name in ("_active_columns", "active_columns", "columns", "_columns", "column_manager", "_column_manager"):
        obj = getattr(table, name, None)
        if obj is None:
            continue
        # Direct list?
        if isinstance(obj, (list, tuple)):
            return obj
        # Has attribute that looks like list
        for subname in ("active", "active_columns", "_active", "visible"):
            sub = getattr(obj, subname, None)
            if isinstance(sub, (list, tuple)):
                return sub
            # method?
            try:
                if callable(sub):
                    v = sub()
                    if isinstance(v, (list, tuple)):
                        return v
            except Exception:
                pass
    return None

def _compute_value(table, item, is_notes_mode):
    # obtain card/note via table state helpers if possible
    card = None
    note = None
    try:
        card = table._state.get_card(item)
    except Exception:
        pass
    try:
        note = table._state.get_note(item)
    except Exception:
        pass
    if is_notes_mode:
        nums = _note_cloze_numbers(note) if note else []
        return ",".join(map(str, nums)) if nums else "-"
    else:
        if card and _is_cloze(card):
            return str(card.ord + 1)
        return "-"

def _fill_row_for_column(table, item, is_notes_mode, row, active_columns) -> None:
    val = _compute_value(table, item, is_notes_mode)
    wrote = False
    if active_columns:
        for idx, key in _active_iter(active_columns):
            if key == COLUMN_KEY and idx < len(row.cells):
                try:
                    row.cells[idx].text = val
                    wrote = True
                except Exception:
                    pass
    if not wrote and row.cells:
        # Last-resort: write into last cell (assumes our column is placed at the end)
        try:
            row.cells[-1].text = val
        except Exception:
            pass

def _on_browser_did_fetch_row(*args, **kwargs) -> None:
    # Accept a wide range of signatures (3~5 args)
    table = None
    item = None
    is_notes_mode = False
    row = None
    active = None

    # Heuristic assignment
    for a in args:
        if hasattr(a, "_state") and hasattr(a, "model"):
            table = a
        elif hasattr(a, "cells"):
            row = a
        elif isinstance(a, bool):
            is_notes_mode = a
        elif isinstance(a, (list, tuple)):
            active = a
        else:
            # could be "item"
            item = a if item is None else item

    if table is None and row is not None:
        table = getattr(row, "_table", None) or getattr(row, "table", None)

    if active is None and table is not None:
        active = _get_active_columns_from_table(table)

    if not (table and row is not None and item is not None):
        return

    _fill_row_for_column(table, item, bool(is_notes_mode), row, active)

# ---------- Hook registrations ----------

if hasattr(gui_hooks, "browser_did_init"):
    gui_hooks.browser_did_init.append(_on_browser_ready)
elif hasattr(gui_hooks, "browser_will_show"):
    gui_hooks.browser_will_show.append(_on_browser_ready)

if hasattr(gui_hooks, "browser_did_change_row"):
    gui_hooks.browser_did_change_row.append(_on_row_changed)
if hasattr(gui_hooks, "browser_did_search"):
    gui_hooks.browser_did_search.append(_on_search)
if hasattr(gui_hooks, "browser_sidebar_did_change_selection"):
    gui_hooks.browser_sidebar_did_change_selection.append(_on_sidebar_change)
if hasattr(gui_hooks, "browser_did_fetch_columns"):
    gui_hooks.browser_did_fetch_columns.append(_register_columns)
if hasattr(gui_hooks, "browser_did_fetch_row"):
    gui_hooks.browser_did_fetch_row.append(_on_browser_did_fetch_row)

# Sorting note:
# Your build lacks 'sorting' in Column proto, so clicking our header will show
# "This column cannot be sorted". Use the built-in "Card" column to sort by ord.

# End of addon
