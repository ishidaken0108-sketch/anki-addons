from __future__ import annotations

import html
import json
import re
from typing import Any

import aqt.reviewer
from aqt import gui_hooks, mw
from aqt.qt import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QKeySequence,
    QKeySequenceEdit,
    QLabel,
    QPushButton,
    QShortcut,
    QTableWidget,
    QVBoxLayout,
    Qt,
)
from aqt.utils import showWarning, tooltip

from .presets import apply_preset, has_preset, preset_choices, preset_name


ADDON_NAME = "Review Word Capture"
NOTE_TYPE_NAME = "Enhanced Cloze 2.1 v2"
CONTENT_FIELD = "Content"
CLOZE99_FIELD = "Cloze99"
SOURCE_FIELD = "RWC_SourceCardId"
DEFAULT_SHORTCUT = "Ctrl+Shift+W"

_shortcut: QShortcut | None = None


def _config() -> dict[str, Any]:
    cfg = mw.addonManager.getConfig(__name__) or {}
    cfg.setdefault("deck_with_colon", "")
    cfg.setdefault("deck_without_colon", "")
    cfg.setdefault("shortcut", DEFAULT_SHORTCUT)
    if "detailed_rules" not in cfg:
        cfg["detailed_rules"] = []

    # v11 briefly shipped one pre-populated example rule. Remove that exact
    # seed so detailed rules consistently start empty. User-created rules with
    # any different value are left untouched.
    v11_seed = [
        {
            "enabled": True,
            "source_deck": "漫画英訳",
            "colon_condition": "with_colon",
            "destination_deck": "自主的",
            "preset": "content_image1_note_image1",
        }
    ]
    if cfg.get("detailed_rules") == v11_seed:
        cfg["detailed_rules"] = []
        try:
            mw.addonManager.writeConfig(__name__, cfg)
        except Exception:
            pass
    return cfg


def _all_decks() -> list[tuple[str, int]]:
    if not mw.col:
        return []
    try:
        decks = [(x.name, int(x.id)) for x in mw.col.decks.all_names_and_ids()]
    except Exception:
        decks = [(str(x["name"]), int(x["id"])) for x in mw.col.decks.all()]
    return sorted(decks, key=lambda x: x[0].casefold())


def _deck_id_from_config(value: Any) -> int | None:
    """Accept both current ID-based config and older name-based config."""
    if not mw.col or value in (None, ""):
        return None

    try:
        did = int(value)
        deck = mw.col.decks.get(did)
        if deck:
            return did
    except (TypeError, ValueError):
        pass
    except Exception:
        pass

    name = str(value)
    try:
        deck = mw.col.decks.by_name(name)
        if deck:
            return int(deck["id"])
    except Exception:
        pass
    return None


def _deck_id_by_name(name: str) -> int | None:
    if not mw.col or not name:
        return None
    try:
        deck = mw.col.decks.by_name(name)
        return int(deck["id"]) if deck else None
    except Exception:
        return None


def _card_source_deck_id(card: Any) -> int | None:
    """Use the original deck while reviewing from a filtered deck."""
    try:
        odid = int(getattr(card, "odid", 0) or 0)
        return odid if odid else int(card.did)
    except Exception:
        return None


def _deck_name(deck_id: int | None) -> str:
    if not mw.col or deck_id is None:
        return ""
    try:
        deck = mw.col.decks.get(deck_id)
        return str(deck.get("name", "")) if deck else ""
    except Exception:
        return ""


def _rule_deck_id(value: Any) -> int | None:
    return _deck_id_from_config(value)


def _colon_condition_matches(condition: str, has_colon: bool) -> bool:
    if condition == "both":
        return True
    if condition == "with_colon":
        return has_colon
    if condition == "without_colon":
        return not has_colon
    return False


def _matching_detailed_rule(source_card: Any, has_colon: bool, cfg: dict[str, Any]) -> tuple[int, dict[str, Any]] | None:
    source_did = _card_source_deck_id(source_card)
    if source_did is None:
        return None

    rules = cfg.get("detailed_rules", [])
    if not isinstance(rules, list):
        return None

    for index, rule in enumerate(rules):
        if not isinstance(rule, dict) or not bool(rule.get("enabled", True)):
            continue
        rule_source_did = _rule_deck_id(rule.get("source_deck"))
        if rule_source_did != source_did:
            continue
        condition = str(rule.get("colon_condition", "both"))
        if not _colon_condition_matches(condition, has_colon):
            continue
        return index, rule
    return None


def _default_destination(has_colon: bool, cfg: dict[str, Any]) -> int | None:
    key = "deck_with_colon" if has_colon else "deck_without_colon"
    return _deck_id_from_config(cfg.get(key))


def _route_for_card(
    source_card: Any, has_colon: bool, cfg: dict[str, Any]
) -> tuple[int | None, str | None, str | None]:
    """Return destination deck, processing preset ID, and routing error."""
    fallback = _default_destination(has_colon, cfg)
    matched = _matching_detailed_rule(source_card, has_colon, cfg)
    if not matched:
        return fallback, None, None

    index, rule = matched
    did = _rule_deck_id(rule.get("destination_deck"))
    if did is None:
        return None, None, f"詳細ルール {index + 1} の追加先デッキが見つかりません。"

    preset_id = str(rule.get("preset", "none")).strip() or "none"
    if not has_preset(preset_id):
        return None, None, f"詳細ルール {index + 1} の処理プリセット「{preset_name(preset_id)}」が見つかりません。"
    return did, preset_id, None


def _ensure_shortcut() -> None:
    global _shortcut

    if _shortcut is not None:
        try:
            _shortcut.setParent(None)
            _shortcut.deleteLater()
        except Exception:
            pass
        _shortcut = None

    sequence = str(_config().get("shortcut", DEFAULT_SHORTCUT)).strip()
    if not sequence:
        return

    _shortcut = QShortcut(QKeySequence(sequence), mw)
    _shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
    _shortcut.activated.connect(_capture_selected_text)


def _capture_selected_text() -> None:
    if not mw.col or mw.state != "review" or not mw.reviewer or not mw.reviewer.card:
        return

    js = "window.getSelection ? window.getSelection().toString() : ''"
    mw.reviewer.web.evalWithCallback(js, _on_selection_received)


def _on_selection_received(value: Any) -> None:
    if not isinstance(value, str):
        return

    selected = value.strip()
    if not selected:
        tooltip("新規カード追加：文字列が選択されていません")
        return

    selected = re.sub(r"\s+", " ", selected).strip()
    _add_selected_text(selected)


def _split_colon(text: str) -> tuple[bool, str, str]:
    match = re.search(r"[:：]", text)
    if not match:
        return False, text.strip(), ""
    return True, text[: match.start()].strip(), text[match.end() :].strip()


def _content_for_selection(text: str) -> tuple[bool, str]:
    has_colon, left, right = _split_colon(text)

    if has_colon and left and right:
        word = html.escape(left, quote=False)
        meaning = html.escape(right, quote=False)
        return True, f"{word}<br><br>{{{{c1::{meaning}}}}}"

    return has_colon, html.escape(text, quote=False)


def _add_selected_text(selected: str) -> None:
    if not mw.col or not mw.reviewer or not mw.reviewer.card:
        return

    cfg = _config()
    source_card = mw.reviewer.card
    has_colon, content = _content_for_selection(selected)
    deck_id, preset_id, routing_error = _route_for_card(source_card, has_colon, cfg)

    if routing_error:
        showWarning(f"{ADDON_NAME}\n\n{routing_error}")
        return

    if deck_id is None:
        label = "コロンあり" if has_colon else "コロンなし"
        showWarning(
            f"{ADDON_NAME}\n\n{label}の保存先デッキが設定されていません。\n"
            "アドオンの設定から保存先デッキを指定してください。"
        )
        return

    model = mw.col.models.by_name(NOTE_TYPE_NAME)
    if not model:
        showWarning(f"ノートタイプ「{NOTE_TYPE_NAME}」が見つかりません。")
        return

    source_card_id = int(source_card.id)

    try:
        field_names = set(mw.col.models.field_names(model))
        if CONTENT_FIELD not in field_names:
            raise KeyError(CONTENT_FIELD)

        if SOURCE_FIELD not in field_names:
            _ensure_source_field(model)
            model = mw.col.models.by_name(NOTE_TYPE_NAME)
            if not model:
                raise RuntimeError("ノートタイプの再読み込みに失敗しました。")
            field_names = set(mw.col.models.field_names(model))

        note = mw.col.new_note(model)
        note[CONTENT_FIELD] = content

        if CLOZE99_FIELD in field_names:
            note[CLOZE99_FIELD] = "" if "{{c1::" in content else "{{c1::.}}"

        # A detailed rule can attach one code-defined processing preset.
        if preset_id:
            apply_preset(preset_id, source_card, note, field_names)

        note[SOURCE_FIELD] = str(source_card_id)
        mw.col.add_note(note, deck_id)

    except Exception as exc:
        showWarning(f"新規カードの追加に失敗しました。\n\n{exc}")
        return

    display = selected if len(selected) <= 60 else selected[:57] + "..."
    tooltip(f"新規カード追加：{display}")


def _ensure_source_field(model: dict[str, Any] | None = None) -> None:
    if not mw.col:
        return

    model = model or mw.col.models.by_name(NOTE_TYPE_NAME)
    if not model:
        return
    if any(f.get("name") == SOURCE_FIELD for f in model.get("flds", [])):
        return

    field = mw.col.models.new_field(SOURCE_FIELD)
    mw.col.models.add_field(model, field)
    mw.col.models.save(model)


def _source_id(card: Any) -> int | None:
    try:
        note = card.note()
        model = note.note_type()
        if SOURCE_FIELD not in mw.col.models.field_names(model):
            return None
        raw = note[SOURCE_FIELD].strip()
        if not raw:
            return None
        return int(raw)
    except Exception:
        return None


def _add_source_button(text: str, card: Any, kind: str) -> str:
    if kind not in ("reviewQuestion", "reviewAnswer"):
        return text
    if not _source_id(card):
        return text

    button = r"""
<div id="rwc-source-card-control" style="margin-top:28px;text-align:center;">
  <button type="button" onclick="pycmd('rwc_show_source_card')"
    style="font-size:14px;padding:6px 12px;cursor:pointer;">
    作成元を表示
  </button>
</div>
"""
    return text + button


def _on_js_message(handled: tuple[bool, Any], message: str, context: Any) -> tuple[bool, Any]:
    if message != "rwc_show_source_card" or not isinstance(context, aqt.reviewer.Reviewer):
        return handled

    _show_source_card(context)
    return (True, None)


def _prepared_card_side(raw: str) -> str:
    try:
        return mw.prepare_card_text_for_display(raw)
    except Exception:
        return raw


def _show_source_card(reviewer: aqt.reviewer.Reviewer) -> None:
    if not mw.col or not reviewer.card:
        return

    source_id = _source_id(reviewer.card)
    if not source_id:
        tooltip("作成元カード情報がありません")
        return

    try:
        source_card = mw.col.get_card(source_id)
        question = _prepared_card_side(source_card.question())
        answer = _prepared_card_side(source_card.answer())
    except Exception:
        tooltip("作成元カードが見つかりません（削除された可能性があります）")
        return

    q_json = json.dumps(question, ensure_ascii=False)
    a_json = json.dumps(answer, ensure_ascii=False)

    js = f"""
(() => {{
  const old = document.getElementById('rwc-source-overlay');
  if (old) old.remove();

  const overlay = document.createElement('div');
  overlay.id = 'rwc-source-overlay';
  overlay.style.cssText = [
    'position:fixed','inset:0','z-index:2147483647',
    'background:rgba(0,0,0,.55)','display:flex',
    'align-items:center','justify-content:center','padding:24px',
    'box-sizing:border-box'
  ].join(';');

  const panel = document.createElement('div');
  panel.style.cssText = [
    'width:min(960px,96vw)','height:min(820px,90vh)',
    'background:var(--canvas,#fff)','color:var(--fg,#111)',
    'border-radius:10px','box-shadow:0 12px 42px rgba(0,0,0,.35)',
    'display:flex','flex-direction:column','overflow:hidden'
  ].join(';');

  const bar = document.createElement('div');
  bar.style.cssText = 'display:flex;align-items:center;gap:8px;padding:10px 12px;border-bottom:1px solid rgba(128,128,128,.35);';

  const title = document.createElement('strong');
  title.textContent = '作成元カード';
  title.style.marginRight = 'auto';

  const frontBtn = document.createElement('button');
  frontBtn.textContent = '表面';
  const backBtn = document.createElement('button');
  backBtn.textContent = '裏面';
  const closeBtn = document.createElement('button');
  closeBtn.textContent = '閉じる';

  for (const b of [frontBtn, backBtn, closeBtn]) {{
    b.type = 'button';
    b.style.cssText = 'font-size:14px;padding:5px 10px;cursor:pointer;';
  }}

  const frame = document.createElement('iframe');
  frame.setAttribute('sandbox', 'allow-scripts allow-same-origin');
  frame.style.cssText = 'border:0;width:100%;flex:1;background:white;';

  const front = {q_json};
  const back = {a_json};
  const show = (side) => {{
    frame.srcdoc = side === 'back' ? back : front;
    frontBtn.disabled = side === 'front';
    backBtn.disabled = side === 'back';
  }};

  frontBtn.onclick = () => show('front');
  backBtn.onclick = () => show('back');
  closeBtn.onclick = () => overlay.remove();
  overlay.onclick = (ev) => {{ if (ev.target === overlay) overlay.remove(); }};
  overlay.addEventListener('keydown', (ev) => {{ if (ev.key === 'Escape') overlay.remove(); }});
  overlay.tabIndex = -1;

  bar.append(title, frontBtn, backBtn, closeBtn);
  panel.append(bar, frame);
  overlay.append(panel);
  document.body.append(overlay);
  overlay.focus();
  show('front');
}})();
"""
    reviewer.web.eval(js)


def _show_config() -> None:
    if not mw.col:
        return

    cfg = _config()
    dlg = QDialog(mw)
    dlg.setWindowTitle(ADDON_NAME + " 設定")
    dlg.resize(980, 620)
    root = QVBoxLayout(dlg)

    standard_group = QGroupBox("標準設定", dlg)
    standard_form = QFormLayout(standard_group)
    colon_combo = QComboBox(dlg)
    plain_combo = QComboBox(dlg)
    decks = _all_decks()

    colon_combo.addItem("未設定", "")
    plain_combo.addItem("未設定", "")
    for name, did in decks:
        colon_combo.addItem(name, str(did))
        plain_combo.addItem(name, str(did))

    def select_combo(combo: QComboBox, configured: Any) -> None:
        did = _deck_id_from_config(configured)
        if did is None:
            combo.setCurrentIndex(0)
            return
        idx = combo.findData(str(did))
        combo.setCurrentIndex(idx if idx >= 0 else 0)

    select_combo(colon_combo, cfg.get("deck_with_colon"))
    select_combo(plain_combo, cfg.get("deck_without_colon"))

    key_edit = QKeySequenceEdit(QKeySequence(str(cfg.get("shortcut", DEFAULT_SHORTCUT))), dlg)
    standard_form.addRow("「: / ：」を含む場合の標準保存先", colon_combo)
    standard_form.addRow("「: / ：」を含まない場合の標準保存先", plain_combo)
    standard_form.addRow("追加ショートカット", key_edit)
    root.addWidget(standard_group)

    rules_group = QGroupBox("詳細ルール（上から順に最初に一致したルールを使用）", dlg)
    rules_layout = QVBoxLayout(rules_group)
    help_label = QLabel(
        "追加元デッキ × コロン条件ごとに、追加先と処理プリセットを上書きします。"
        "一致しなければ上の標準設定を使います。",
        rules_group,
    )
    help_label.setWordWrap(True)
    rules_layout.addWidget(help_label)

    table = QTableWidget(0, 5, rules_group)
    table.setHorizontalHeaderLabels(["有効", "追加元", "コロン条件", "追加先", "処理プリセット"])
    header = table.horizontalHeader()
    header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
    for col in (1, 3, 4):
        header.setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch)
    header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
    table.verticalHeader().setVisible(False)
    rules_layout.addWidget(table)

    def deck_combo(value: Any = "") -> QComboBox:
        combo = QComboBox(table)
        combo.addItem("未設定", "")
        for name, did in decks:
            combo.addItem(name, str(did))
        did = _deck_id_from_config(value)
        if did is not None:
            idx = combo.findData(str(did))
            if idx >= 0:
                combo.setCurrentIndex(idx)
            else:
                combo.addItem(f"見つかりません ({value})", str(value))
                combo.setCurrentIndex(combo.count() - 1)
        elif value not in (None, ""):
            combo.addItem(f"見つかりません ({value})", str(value))
            combo.setCurrentIndex(combo.count() - 1)
        return combo

    def condition_combo(value: str = "both") -> QComboBox:
        combo = QComboBox(table)
        combo.addItem(": あり", "with_colon")
        combo.addItem(": なし", "without_colon")
        combo.addItem("両方", "both")
        idx = combo.findData(value)
        combo.setCurrentIndex(idx if idx >= 0 else 2)
        return combo

    def processing_combo(value: str = "none") -> QComboBox:
        combo = QComboBox(table)
        for name, preset_id in preset_choices():
            combo.addItem(name, preset_id)
        idx = combo.findData(value)
        if idx >= 0:
            combo.setCurrentIndex(idx)
        else:
            combo.addItem(f"見つかりません ({value})", value)
            combo.setCurrentIndex(combo.count() - 1)
        return combo

    def add_rule_row(rule: dict[str, Any] | None = None) -> None:
        rule = rule or {}
        row = table.rowCount()
        table.insertRow(row)

        enabled = QCheckBox(table)
        enabled.setChecked(bool(rule.get("enabled", True)))
        enabled_box = QHBoxLayout()
        enabled_box.setContentsMargins(0, 0, 0, 0)
        enabled_box.setAlignment(Qt.AlignmentFlag.AlignCenter)
        enabled_holder = QLabel(table)
        # QLabel cannot host a layout reliably in all Qt versions; use a plain QWidget.
        from aqt.qt import QWidget
        holder = QWidget(table)
        holder.setLayout(enabled_box)
        enabled_box.addWidget(enabled)
        table.setCellWidget(row, 0, holder)

        table.setCellWidget(row, 1, deck_combo(rule.get("source_deck", "")))
        table.setCellWidget(row, 2, condition_combo(str(rule.get("colon_condition", "both"))))
        table.setCellWidget(row, 3, deck_combo(rule.get("destination_deck", "")))
        table.setCellWidget(row, 4, processing_combo(str(rule.get("preset", "none"))))

    existing_rules = cfg.get("detailed_rules", [])
    if isinstance(existing_rules, list):
        for rule in existing_rules:
            if isinstance(rule, dict):
                add_rule_row(rule)

    controls = QHBoxLayout()
    add_btn = QPushButton("＋ ルールを追加", rules_group)
    delete_btn = QPushButton("選択行を削除", rules_group)
    up_btn = QPushButton("↑ 上へ", rules_group)
    down_btn = QPushButton("↓ 下へ", rules_group)
    controls.addWidget(add_btn)
    controls.addWidget(delete_btn)
    controls.addWidget(up_btn)
    controls.addWidget(down_btn)
    controls.addStretch(1)
    rules_layout.addLayout(controls)

    def swap_rows(a: int, b: int) -> None:
        if a < 0 or b < 0 or a >= table.rowCount() or b >= table.rowCount():
            return
        widgets_a = [table.cellWidget(a, c) for c in range(table.columnCount())]
        widgets_b = [table.cellWidget(b, c) for c in range(table.columnCount())]
        # Extract values, then rebuild. Moving live cell widgets between rows can
        # delete/reparent them unexpectedly under Qt.
        def values(widgets: list[Any]) -> dict[str, Any]:
            holder, source, condition, dest, processing = widgets
            check = holder.findChild(QCheckBox)
            return {
                "enabled": bool(check.isChecked()) if check else True,
                "source_deck": source.currentData() if isinstance(source, QComboBox) else "",
                "colon_condition": condition.currentData() if isinstance(condition, QComboBox) else "both",
                "destination_deck": dest.currentData() if isinstance(dest, QComboBox) else "",
                "preset": processing.currentData() if isinstance(processing, QComboBox) else "none",
            }
        va, vb = values(widgets_a), values(widgets_b)
        for row, val in ((a, vb), (b, va)):
            table.removeCellWidget(row, 0)
            for col in range(1, 5):
                table.removeCellWidget(row, col)
            # Populate without changing row count.
            enabled = QCheckBox(table)
            enabled.setChecked(bool(val.get("enabled", True)))
            from aqt.qt import QWidget
            holder = QWidget(table)
            lay = QHBoxLayout(holder)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lay.addWidget(enabled)
            table.setCellWidget(row, 0, holder)
            table.setCellWidget(row, 1, deck_combo(val.get("source_deck", "")))
            table.setCellWidget(row, 2, condition_combo(str(val.get("colon_condition", "both"))))
            table.setCellWidget(row, 3, deck_combo(val.get("destination_deck", "")))
            table.setCellWidget(row, 4, processing_combo(str(val.get("preset", "none"))))
        table.selectRow(b)

    add_btn.clicked.connect(lambda: add_rule_row())

    def delete_selected() -> None:
        rows = sorted({idx.row() for idx in table.selectedIndexes()}, reverse=True)
        if not rows and table.currentRow() >= 0:
            rows = [table.currentRow()]
        for row in rows:
            table.removeRow(row)
    delete_btn.clicked.connect(delete_selected)
    up_btn.clicked.connect(lambda: swap_rows(table.currentRow(), table.currentRow() - 1))
    down_btn.clicked.connect(lambda: swap_rows(table.currentRow(), table.currentRow() + 1))

    root.addWidget(rules_group, 1)

    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
        parent=dlg,
    )
    root.addWidget(buttons)
    buttons.accepted.connect(dlg.accept)
    buttons.rejected.connect(dlg.reject)

    if dlg.exec() != QDialog.DialogCode.Accepted:
        return

    detailed_rules: list[dict[str, Any]] = []
    for row in range(table.rowCount()):
        holder = table.cellWidget(row, 0)
        enabled_widget = holder.findChild(QCheckBox) if holder else None
        source = table.cellWidget(row, 1)
        condition = table.cellWidget(row, 2)
        dest = table.cellWidget(row, 3)
        processing = table.cellWidget(row, 4)
        rule = {
            "enabled": bool(enabled_widget.isChecked()) if enabled_widget else True,
            "source_deck": source.currentData() if isinstance(source, QComboBox) else "",
            "colon_condition": condition.currentData() if isinstance(condition, QComboBox) else "both",
            "destination_deck": dest.currentData() if isinstance(dest, QComboBox) else "",
            "preset": processing.currentData() if isinstance(processing, QComboBox) else "none",
        }
        if rule["enabled"] and (not rule["source_deck"] or not rule["destination_deck"]):
            showWarning(f"詳細ルール {row + 1}：追加元と追加先を選択してください。")
            return
        if rule["enabled"] and not has_preset(str(rule["preset"])):
            showWarning(f"詳細ルール {row + 1}：有効な処理プリセットを選択してください。")
            return
        detailed_rules.append(rule)

    cfg["deck_with_colon"] = colon_combo.currentData() or ""
    cfg["deck_without_colon"] = plain_combo.currentData() or ""
    cfg["shortcut"] = key_edit.keySequence().toString() or DEFAULT_SHORTCUT
    cfg["detailed_rules"] = detailed_rules
    cfg.pop("profiles", None)
    mw.addonManager.writeConfig(__name__, cfg)
    _ensure_shortcut()
    tooltip("Review Word Capture：設定を保存しました")


def _on_profile_open() -> None:
    try:
        _ensure_source_field()
    except Exception as exc:
        print(f"[{ADDON_NAME}] Could not ensure source field: {exc}")
    _ensure_shortcut()


mw.addonManager.setConfigAction(__name__, _show_config)
gui_hooks.profile_did_open.append(_on_profile_open)
gui_hooks.card_will_show.append(_add_source_button)
gui_hooks.webview_did_receive_js_message.append(_on_js_message)
