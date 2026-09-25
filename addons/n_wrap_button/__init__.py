from __future__ import annotations

import os
import weakref

from aqt import gui_hooks, mw
from aqt.utils import tooltip

# Qt imports for Browser-level shortcut binding (so it works even when the focus is in
# the Browser's search bar / card list).
try:
    from aqt.qt import QShortcut, QKeySequence, Qt  # type: ignore
except Exception:  # pragma: no cover
    QShortcut = None  # type: ignore
    QKeySequence = None  # type: ignore
    Qt = None  # type: ignore

DEFAULT_SHORTCUT = "Ctrl+Alt+N"


def _get_config() -> dict:
    """Read add-on config safely."""
    try:
        cfg = mw.addonManager.getConfig(__name__)
        if isinstance(cfg, dict):
            return cfg
    except Exception:
        pass
    return {}


def _get_shortcut() -> str:
    """Return configured shortcut string.

    Set config "shortcut" to:
      - e.g. "Ctrl+Alt+N" (default)
      - "" / "none" / "disabled" / "off" to disable
    """
    cfg = _get_config()
    sc = cfg.get("shortcut", DEFAULT_SHORTCUT)
    if not isinstance(sc, str):
        sc = DEFAULT_SHORTCUT
    sc = sc.strip()
    if sc.lower() in ("", "none", "disabled", "off"):
        return ""
    return sc


def _adder(editor):
    return getattr(editor, "add_button", None) or getattr(editor, "addButton", None)


def _icon_path():
    try:
        here = os.path.dirname(__file__)
        p = os.path.join(here, "icons", "nwrap.svg")
        if os.path.exists(p):
            return p
    except Exception:
        pass
    return None


def _current_field(editor):
    # Newer builds: current_field; older: currentField
    if hasattr(editor, "current_field"):
        return editor.current_field
    return getattr(editor, "currentField", None)


def _js_wrap_script():
    # JS fallback that preserves selection: wraps selected text with [[n: ... ]]
    return r"""
        (function() {
          try {
            var sel = window.getSelection && window.getSelection();
            var text = sel ? sel.toString() : "";
            if (text && text.length > 0) {
              document.execCommand('insertText', false, '[[n: ' + text + ' ]]');
            } else {
              // No selection: insert empty template
              document.execCommand('insertText', false, '[[n:  ]]');
              // Try to place caret between the spaces (best-effort)
              try {
                var range = sel.getRangeAt(0);
                if (range && range.startContainer && range.startContainer.nodeType === 3) {
                  var node = range.startContainer;
                  var idx = (node.data || "").lastIndexOf('[[n:  ]]');
                  if (idx >= 0) {
                    var r = document.createRange();
                    r.setStart(node, idx + 5); // after '[[n: '
                    r.collapse(true);
                    sel.removeAllRanges();
                    sel.addRange(r);
                  }
                }
              } catch (e2) {}
            }
          } catch (e) {
            // Swallow errors; better to fail silently than crash Anki
          }
        })();
    """


def wrap_with_n(editor):
    # Ensure a field is focused (both old/new APIs)
    if _current_field(editor) is None:
        tooltip("まずフィールドを選択してください。")
        return

    try:
        # Prefer Editor.wrap if present (preserves rich selection)
        wrap_fn = getattr(editor, "wrap", None)
        if callable(wrap_fn):
            wrap_fn("[[n: ", " ]]")
            return
    except Exception:
        pass

    # JS fallback: doesn't drop the selected text
    try:
        editor.web.eval(_js_wrap_script())
    except Exception as e:
        tooltip(f"挿入に失敗しました: {e}")


def _make_tip(shortcut: str) -> str:
    base = "[[n: ... ]] を挿入"
    return f"{base} ({shortcut})" if shortcut else base


def on_setup_buttons(buttons, editor):
    adder = _adder(editor)
    if not adder:
        return

    shortcut = _get_shortcut()
    tip = _make_tip(shortcut)
    icon = _icon_path()

    # We keep the toolbar button, but bind the shortcut via editor_did_init_shortcuts
    # (so it can work even when the cursor is not inside a field).
    try:
        btn = adder(
            icon,
            "nwrap_insert",
            lambda e=editor: wrap_with_n(e),
            tip,
        )
    except TypeError:
        # Older signature expects a "keys" argument.
        btn = adder(icon, "nwrap_insert", lambda e=editor: wrap_with_n(e), tip, "")

    buttons.append(btn)


def on_setup_shortcuts(cuts, editor):
    shortcut = _get_shortcut()
    if not shortcut:
        return

    # The 3rd element True makes it work even when focus is outside a field.
    cuts.append((shortcut, lambda e=editor: wrap_with_n(e), True))

# --- Browser support ----------------------------------------------------

_browser_shortcuts = weakref.WeakKeyDictionary()

def _get_browser_editor(browser):
    # Anki's Browser typically exposes an Editor instance as `browser.editor`.
    ed = getattr(browser, "editor", None)
    if ed is not None:
        return ed
    # Some builds expose it on the form.
    form = getattr(browser, "form", None)
    ed = getattr(form, "editor", None) if form is not None else None
    if ed is not None:
        return ed
    # Fallback: common private attribute names.
    for name in ("_editor", "noteEditor", "note_editor"):
        ed = getattr(browser, name, None)
        if ed is not None:
            return ed
    return None

def _wrap_in_browser(browser):
    ed = _get_browser_editor(browser)
    if ed is None:
        tooltip("ブラウザ内のエディタが見つかりませんでした。")
        return
    wrap_with_n(ed)

def _bind_browser_shortcut(browser):
    """Bind shortcut to the Browser window (works even if focus isn't in a field)."""
    if QShortcut is None or QKeySequence is None:
        return

    # Remove existing shortcut if any
    old = _browser_shortcuts.get(browser)
    if old is not None:
        try:
            old.setParent(None)
            old.deleteLater()
        except Exception:
            pass
        try:
            del _browser_shortcuts[browser]
        except Exception:
            pass

    shortcut = _get_shortcut()
    if not shortcut:
        return

    try:
        sc = QShortcut(QKeySequence(shortcut), browser)
        # Make it active for the Browser and all its children (search box, table, etc.)
        if Qt is not None:
            sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        sc.activated.connect(lambda b=browser: _wrap_in_browser(b))
        _browser_shortcuts[browser] = sc
    except Exception:
        # Don't crash Anki on shortcut issues; user can still use the toolbar button.
        pass

def on_browser_did_init(browser):
    _bind_browser_shortcut(browser)

# Re-bind shortcuts when the add-on config changes (best-effort).
def _on_config_updated(*args, **kwargs):
    try:
        for br in list(_browser_shortcuts.keys()):
            _bind_browser_shortcut(br)
    except Exception:
        pass

try:
    mw.addonManager.setConfigUpdatedAction(__name__, _on_config_updated)
except Exception:
    pass


gui_hooks.editor_did_init_buttons.append(on_setup_buttons)
# Adds the keyboard shortcut (configurable via add-on config)
# NOTE: takes effect when opening an editor (Add / Edit / Browser editor).

gui_hooks.editor_did_init_shortcuts.append(on_setup_shortcuts)

# Bind the shortcut on Browser windows as well (so it works from the Browser list/search).
for _hook_name in ("browser_did_init", "browser_will_show"):
    try:
        getattr(gui_hooks, _hook_name).append(on_browser_did_init)
    except Exception:
        pass
