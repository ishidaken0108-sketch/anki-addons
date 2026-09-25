# -*- coding: utf-8 -*-
"""Browser Sidebar: Collapse all but Tags (default)

When the Browser opens, collapses top-level sidebar sections
(e.g. Decks, Notetypes, Flags, etc.), and expands only Tags.

No configuration/options by design.
"""

from __future__ import annotations

from typing import Optional

import time

from aqt import gui_hooks
from aqt.qt import QTimer, Qt, QModelIndex


def _display_role():
    # Qt5: Qt.DisplayRole
    # Qt6: Qt.ItemDataRole.DisplayRole
    return Qt.ItemDataRole.DisplayRole if hasattr(Qt, "ItemDataRole") else Qt.DisplayRole


DISPLAY_ROLE = _display_role()


def _is_tags_label(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    tl = t.lower()
    # common UI labels
    if tl in ("tags", "tag"):
        return True
    # Japanese
    if "タグ" in t:
        return True
    # Chinese
    if "标签" in t or "標籤" in t:
        return True
    # Korean
    if "태그" in t:
        return True
    return False


def _find_sidebar_tree(browser) -> Optional[object]:
    # Newer Anki has browser.sidebarTree, or browser.sidebar.tree, or browser.form.sidebarTree.
    tree = getattr(browser, "sidebarTree", None)
    if tree is not None:
        return tree

    sidebar = getattr(browser, "sidebar", None)
    if sidebar is not None:
        tree = getattr(sidebar, "tree", None)
        if tree is not None:
            return tree

    form = getattr(browser, "form", None)
    if form is not None:
        tree = getattr(form, "sidebarTree", None)
        if tree is not None:
            return tree

    return None


def _safe_str(val) -> str:
    try:
        return str(val)
    except Exception:
        return ""


def _sidebar_item_label(model, idx: QModelIndex) -> str:
    """Best-effort label for a sidebar row (works across Anki/Qt versions)."""
    # Preferred: model data
    try:
        lbl = model.data(idx, DISPLAY_ROLE)
        if lbl is not None:
            s = _safe_str(lbl).strip()
            if s:
                return s
    except Exception:
        pass

    # Fallback: internal pointer fields/methods
    try:
        item = idx.internalPointer()
    except Exception:
        item = None

    if item is not None:
        for attr in ("name", "full_name", "label", "title"):
            try:
                v = getattr(item, attr, None)
                if isinstance(v, str) and v.strip():
                    return v.strip()
            except Exception:
                pass
        for meth in ("text", "display", "display_text"):
            try:
                fn = getattr(item, meth, None)
                if callable(fn):
                    v = fn()
                    if isinstance(v, str) and v.strip():
                        return v.strip()
            except Exception:
                pass

    return ""


def _looks_like_tags_item(model, idx: QModelIndex) -> bool:
    """Identify the top-level Tags section without relying on UI language."""
    # 1) Try internal pointer metadata/class name first
    try:
        item = idx.internalPointer()
    except Exception:
        item = None

    if item is not None:
        # Class name heuristic (often Tag/Tags*Item)
        try:
            cls = item.__class__.__name__.lower()
            if "tag" in cls:
                return True
        except Exception:
            pass

        # Enum/field heuristic
        for attr in ("item_type", "kind", "node_type", "type"):
            try:
                v = getattr(item, attr, None)
            except Exception:
                continue
            if v is None:
                continue
            try:
                # Enum-like
                name = getattr(v, "name", None)
                if isinstance(name, str) and "TAG" in name.upper():
                    return True
            except Exception:
                pass
            s = _safe_str(v).lower()
            if "tag" in s or "タグ" in s or "标签" in s or "標籤" in s or "태그" in s:
                return True

    # 2) Fallback to displayed label
    return _is_tags_label(_sidebar_item_label(model, idx))


def _apply_default_collapse(browser) -> bool:
    """Apply default sidebar state.

    Returns True when the Tags section was found and expanded.
    We only mark the browser instance as "done" when we successfully
    expand Tags; this avoids a race where the model exists but labels
    haven't populated yet.
    """

    # Stop once Tags has been successfully expanded.
    if getattr(browser, "_bs_collapse_done", False):
        return True

    tree = _find_sidebar_tree(browser)
    if tree is None:
        return False

    model = tree.model()
    if model is None:
        return False

    root = QModelIndex()
    try:
        row_count = model.rowCount(root)
    except Exception:
        return False

    found_tags = False

    for row in range(row_count):
        idx = model.index(row, 0, root)
        try:
            if _looks_like_tags_item(model, idx):
                found_tags = True
                try:
                    tree.setExpanded(idx, True)
                except Exception:
                    tree.expand(idx)
            else:
                try:
                    tree.setExpanded(idx, False)
                except Exception:
                    tree.collapse(idx)
        except Exception:
            # if something goes wrong with a specific node, ignore it
            pass

    # Mark done only if Tags was found; otherwise keep retrying.
    if found_tags:
        browser._bs_collapse_done = True
    return found_tags


def _setup_and_apply(browser) -> None:
    """Install one-time handlers and attempt to apply the default state.

    We retry a few times because Anki may restore sidebar state after the
    Browser window is created, which can override our first expand/collapse.
    """

    # Only set up once per browser instance.
    if getattr(browser, "_bs_collapse_setup", False):
        return
    browser._bs_collapse_setup = True
    browser._bs_collapse_attempts = 0
    browser._bs_collapse_start = time.monotonic()

    # Staggered retries (ms) to survive late sidebar refresh/state restore.
    retry_delays = [0, 200, 600, 1200, 2000, 3500, 5000]

    def try_apply():
        # Re-apply for a short window after the Browser is shown, because
        # Anki may restore/refresh the sidebar state shortly afterwards.
        start = getattr(browser, "_bs_collapse_start", None)
        if start is not None and (time.monotonic() - start) > 6.0:
            return

        browser._bs_collapse_attempts += 1
        _apply_default_collapse(browser)

    for d in retry_delays:
        QTimer.singleShot(d, try_apply)

    # Also retry on key model signals if available.
    tree = _find_sidebar_tree(browser)
    if tree is None:
        return
    model = tree.model()
    if model is None:
        return

    def _safe_connect(signal_name: str):
        sig = getattr(model, signal_name, None)
        if sig is None:
            return
        try:
            sig.connect(lambda *args, **kwargs: QTimer.singleShot(0, try_apply))
        except Exception:
            pass

    # These often fire when the sidebar rebuilds.
    _safe_connect("modelReset")
    _safe_connect("layoutChanged")
    _safe_connect("rowsInserted")


def _on_browser_will_show(browser) -> None:
    _setup_and_apply(browser)


def _on_browser_did_show(browser) -> None:
    # In some versions, did_show is the first time the sidebar has finished restoring.
    _setup_and_apply(browser)


gui_hooks.browser_will_show.append(_on_browser_will_show)

# browser_did_show may not exist on very old versions; guard for safety.
if hasattr(gui_hooks, "browser_did_show"):
    gui_hooks.browser_did_show.append(_on_browser_did_show)
