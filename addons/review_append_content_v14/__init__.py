from __future__ import annotations

import json
import traceback
from pathlib import Path
from typing import Any

from aqt import gui_hooks, mw
from aqt.reviewer import Reviewer, ReviewerBottomBar
from aqt.utils import tooltip

from .formatter import append_list_item, format_mnemonics, is_effectively_empty

COMMAND = "review_append_content"
BUTTON_ID = "review-append-content-button"
TOAST_ID = "review-append-content-toast"

_manual_added_card_ids: set[int] = set()
_was_new_before_answer: dict[int, bool] = {}


def _config() -> dict[str, Any]:
    raw = mw.addonManager.getConfig(__name__) or {}
    return {
        "target_tag": raw.get("targetTag", raw.get("target_tag", "復習追加先")),
        "source_content_field": raw.get(
            "sourceContentField", raw.get("source_content_field", "Content")
        ),
        "target_content_field": raw.get(
            "targetContentField", raw.get("target_content_field", "Content")
        ),
        "mnemonics_field": raw.get(
            "mnemonicsField", raw.get("mnemonics_field", "Mnemonics")
        ),
        "button_label": raw.get("buttonLabel", raw.get("button_label", "復習追加")),
        "auto_add_new_again": bool(
            raw.get("autoAddNewAgain", raw.get("auto_add_new_again", True))
        ),
    }


def _log(message: str) -> None:
    try:
        folder = Path(__file__).resolve().parent / "user_files"
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / "review_append_debug.log").open("a", encoding="utf-8") as fh:
            fh.write(message.rstrip() + "\n")
    except Exception:
        pass


def _show_toast(reviewer: Reviewer | None, message: str, ok: bool) -> None:
    if reviewer is None:
        tooltip(message, parent=mw, period=2500)
        return

    # Display in the review bottom bar, fixed to the lower-right. No modal window.
    script = f"""
(() => {{
  const old = document.getElementById({json.dumps(TOAST_ID)});
  if (old) old.remove();
  const toast = document.createElement('div');
  toast.id = {json.dumps(TOAST_ID)};
  toast.textContent = {json.dumps(message)};
  toast.style.position = 'fixed';
  toast.style.right = '12px';
  toast.style.bottom = '7px';
  toast.style.zIndex = '99999';
  toast.style.padding = '6px 10px';
  toast.style.borderRadius = '6px';
  toast.style.fontSize = '12px';
  toast.style.whiteSpace = 'nowrap';
  toast.style.background = {json.dumps('rgba(34, 139, 94, 0.92)' if ok else 'rgba(180, 55, 55, 0.94)')};
  toast.style.color = 'white';
  toast.style.boxShadow = '0 2px 8px rgba(0,0,0,.25)';
  document.body.appendChild(toast);
  setTimeout(() => {{ if (toast.isConnected) toast.remove(); }}, 2400);
}})();
"""
    try:
        reviewer.bottom.web.eval(script)
    except Exception:
        tooltip(message, parent=mw, period=2500)


def _note_has_field(note: Any, field_name: str) -> bool:
    try:
        return field_name in note
    except Exception:
        try:
            note[field_name]
            return True
        except Exception:
            return False


def _build_item_from_card(card: Any) -> tuple[bool, str, str]:
    cfg = _config()
    note = card.note()
    content_field = cfg["source_content_field"]
    mnemonics_field = cfg["mnemonics_field"]

    if not _note_has_field(note, content_field):
        return False, "", f"元カードに {content_field} フィールドがありません"

    content = note[content_field].strip()
    if is_effectively_empty(content):
        return False, "", f"元カードの {content_field} が空です"

    item = content

    if _note_has_field(note, mnemonics_field):
        raw_mnemonics = note[mnemonics_field]
        if not is_effectively_empty(raw_mnemonics):
            formatted = format_mnemonics(raw_mnemonics)
            if formatted:
                item += "<br>Mnemonics<br>" + formatted

    return True, item, ""


def _find_target_note() -> tuple[bool, Any | None, str]:
    cfg = _config()
    tag = str(cfg["target_tag"]).strip()
    if not tag:
        return False, None, "追加先タグが設定されていません"

    escaped = tag.replace('"', r'\"')
    try:
        note_ids = list(mw.col.find_notes(f'tag:"{escaped}"'))
    except Exception as exc:
        return False, None, f"タグ検索に失敗しました: {exc}"

    if len(note_ids) == 0:
        return False, None, f"タグ「{tag}」の追加先が見つかりません"
    if len(note_ids) > 1:
        return False, None, f"タグ「{tag}」の追加先が {len(note_ids)} 件あります"

    return True, mw.col.get_note(note_ids[0]), ""


def _append_from_card(card: Any) -> tuple[bool, str]:
    ok, item_html, err = _build_item_from_card(card)
    if not ok:
        return False, err

    ok, target_note, err = _find_target_note()
    if not ok or target_note is None:
        return False, err

    cfg = _config()
    target_field = cfg["target_content_field"]
    if not _note_has_field(target_note, target_field):
        return False, f"追加先に {target_field} フィールドがありません"

    before = target_note[target_field]
    after = append_list_item(before, item_html)
    if after == before:
        return False, "追加先HTMLを変更できませんでした"

    try:
        target_note[target_field] = after
        mw.col.update_note(target_note)
    except Exception as exc:
        _log("update_note failed:\n" + traceback.format_exc())
        return False, f"保存に失敗しました: {exc}"

    return True, "追加完了"


def _manual_add(reviewer: Reviewer) -> None:
    card = reviewer.card
    if card is None:
        _show_toast(reviewer, "復習追加：失敗 - カードがありません", False)
        return

    cid = int(card.id)
    if cid in _manual_added_card_ids:
        _show_toast(reviewer, "復習追加：このカードは追加済み", True)
        return

    try:
        ok, msg = _append_from_card(card)
    except Exception as exc:
        _log("manual add failed:\n" + traceback.format_exc())
        _show_toast(reviewer, f"復習追加：失敗 - {exc}", False)
        return

    if ok:
        _manual_added_card_ids.add(cid)
        _show_toast(reviewer, "復習追加：追加完了", True)
    else:
        _show_toast(reviewer, f"復習追加：失敗 - {msg}", False)


def _bridge_message(
    handled: tuple[bool, Any], message: str, context: Any
) -> tuple[bool, Any]:
    if message != COMMAND:
        return handled

    reviewer: Reviewer | None = None
    if isinstance(context, ReviewerBottomBar):
        reviewer = context.reviewer
    elif isinstance(context, Reviewer):
        reviewer = context

    if reviewer is None:
        return handled

    _manual_add(reviewer)
    return (True, None)


def _inject_button(reviewer: Reviewer, answer_side: bool) -> None:
    cfg = _config()
    label = str(cfg["button_label"])
    selector = 'button[data-ease="1"]' if answer_side else '#ansbut'

    script = f"""
(() => {{
  const buttonId = {json.dumps(BUTTON_ID)};
  const label = {json.dumps(label)};
  const targetSelector = {json.dumps(selector)};

  function place(tries) {{
    const target = document.querySelector(targetSelector);
    if (!target) {{
      if (tries > 0) setTimeout(() => place(tries - 1), 30);
      return;
    }}

    const old = document.getElementById(buttonId);
    if (old) old.remove();

    const button = document.createElement('button');
    button.id = buttonId;
    button.type = 'button';
    button.textContent = label;
    button.style.marginRight = '6px';
    button.onclick = () => pycmd({json.dumps(COMMAND)});
    target.parentNode.insertBefore(button, target);
  }}

  place(20);
}})();
"""
    reviewer.bottom.web.eval(script)


def _on_show_question(card: Any) -> None:
    reviewer = getattr(mw, "reviewer", None)
    if isinstance(reviewer, Reviewer) and reviewer.card is not None:
        _inject_button(reviewer, answer_side=False)


def _on_show_answer(card: Any) -> None:
    reviewer = getattr(mw, "reviewer", None)
    if isinstance(reviewer, Reviewer) and reviewer.card is not None:
        _inject_button(reviewer, answer_side=True)


def _will_answer(
    ease_tuple: tuple[bool, int], reviewer: Reviewer, card: Any
) -> tuple[bool, int]:
    try:
        # Capture before scheduling changes the card state.
        _was_new_before_answer[int(card.id)] = int(card.type) == 0
    except Exception:
        _was_new_before_answer[int(card.id)] = int(getattr(card, "queue", -1)) == 0
    return ease_tuple


def _did_answer(reviewer: Reviewer, card: Any, ease: int) -> None:
    cid = int(card.id)
    was_new = _was_new_before_answer.pop(cid, False)
    manually_added = cid in _manual_added_card_ids

    try:
        if _config()["auto_add_new_again"] and was_new and int(ease) == 1:
            if manually_added:
                # User already pressed the explicit button on this card.
                pass
            else:
                try:
                    ok, msg = _append_from_card(card)
                except Exception as exc:
                    _log("automatic add failed:\n" + traceback.format_exc())
                    _show_toast(reviewer, f"復習追加：失敗 - {exc}", False)
                else:
                    if ok:
                        _show_toast(reviewer, "復習追加：追加完了", True)
                    else:
                        _show_toast(reviewer, f"復習追加：失敗 - {msg}", False)
    finally:
        _manual_added_card_ids.discard(cid)


gui_hooks.webview_did_receive_js_message.append(_bridge_message)
gui_hooks.reviewer_did_show_question.append(_on_show_question)
gui_hooks.reviewer_did_show_answer.append(_on_show_answer)
gui_hooks.reviewer_will_answer_card.append(_will_answer)
gui_hooks.reviewer_did_answer_card.append(_did_answer)
