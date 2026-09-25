from aqt import mw, gui_hooks
from aqt.qt import QAction, qconnect
from aqt.utils import tooltip

ADDON_NAME = "期日プリセット（レビュー画面・8ボタン）"

# Optional newer API (Anki 24.11+)
try:
    from aqt.operations.scheduling import set_due_date as _op_set_due_date
except Exception:
    _op_set_due_date = None

BTN_COUNT = 8

def _get_config():
    default = {
        "buttons": [{"label": "期日を 4! に設定", "due": "4!"} for _ in range(BTN_COUNT)],
        "add_separator": True
    }
    conf = mw.addonManager.getConfig(__name__) or {}
    for k, v in default.items():
        if k not in conf:
            conf[k] = v
    btns = conf.get("buttons", [])
    if not isinstance(btns, list):
        btns = []
    # fill/truncate to 8
    while len(btns) < BTN_COUNT:
        btns.append({"label": "期日を 4! に設定", "due": "4!"})
    if len(btns) > BTN_COUNT:
        btns = btns[:BTN_COUNT]
    conf["buttons"] = btns
    return conf

def _after_success(due_str: str):
    tooltip(f"期日を {due_str} に設定しました。")
    try:
        mw.reset()  # advance to next card
    except Exception:
        pass

def _apply_due(due_str: str):
    reviewer = mw.reviewer
    card = reviewer.card if reviewer else None
    if not card:
        return
    cid = card.id

    if _op_set_due_date is not None:
        op = _op_set_due_date(parent=mw, card_ids=[cid], days=due_str, shift=False)
        op.success(lambda _: _after_success(due_str))
        op.run_in_background()
        return

    # Legacy fallback (24.06 など)
    try:
        from aqt.operations import QueryOp
    except Exception as e:
        tooltip(f"このAnkiでは自動設定に対応できませんでした: {e}")
        return

    def _runner(col):
        try:
            return col.sched.set_due_date([cid], due_str, False)
        except Exception:
            try:
                return col.set_due_date([cid], due_str, False)
            except Exception:
                return 0

    try:
        QueryOp(parent=mw, op=_runner, success=lambda _: _after_success(due_str)).run_in_background()
    except TypeError:
        QueryOp(parent=mw, op=_runner).success(lambda _: _after_success(due_str)).run_in_background()

def _extract_menu(*args):
    # Supports: (menu), (menu, reviewer), (reviewer, menu)
    for a in args:
        if a is None:
            continue
        if hasattr(a, "addAction") and hasattr(a, "addSeparator"):
            return a
    return None

def _add_menu_items(*args):
    menu = _extract_menu(*args)
    if menu is None:
        return
    conf = _get_config()
    if conf.get("add_separator", True):
        try:
            menu.addSeparator()
        except Exception:
            pass
    for item in conf["buttons"]:
        label = item.get("label") or f"期日を {item.get('due', '4!')} に設定"
        due = item.get("due", "4!")
        act = QAction(label, menu)
        qconnect(act.triggered, lambda _, d=due: _apply_due(d))
        try:
            menu.addAction(act)
        except Exception:
            pass

gui_hooks.reviewer_will_show_context_menu.append(_add_menu_items)
