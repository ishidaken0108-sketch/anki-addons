from __future__ import annotations

import ctypes
import json
import platform
import struct
import subprocess
import uuid
from ctypes import wintypes
from typing import Any, Callable

from anki.collection import Config
from aqt import gui_hooks, mw
import aqt.reviewer as reviewer_module
from aqt.qt import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QSpinBox,
    QTimer,
    QVBoxLayout,
)

from .logic import (
    FLAG_NAMES,
    PAUSE_FLAG,
    add_capped,
    auto_flag_for_elapsed,
    display_seconds,
    format_remaining,
    initial_bonus_seconds,
    is_pause_flag,
    config_with_migration,
    normalized_config,
    seconds_for_flag,
    subtract_award,
    wrong_answer_seconds,
)

ADDON_PACKAGE = mw.addonManager.addonFromModule(__name__)
TICK_MILLISECONDS = 250
TICK_SECONDS = TICK_MILLISECONDS / 1000.0
DISPLAY_ELEMENT_ID = "review-time-bank-display"
FLAG_AWARD_ELEMENT_ID = "review-time-bank-flag-award"


class ReviewTimeBankController:
    def __init__(self) -> None:
        raw_config = mw.addonManager.getConfig(ADDON_PACKAGE)
        self._config_cache, config_changed = config_with_migration(raw_config)
        if config_changed:
            try:
                mw.addonManager.writeConfig(ADDON_PACKAGE, self._config_cache)
            except Exception:
                pass
        self.remaining_seconds = 0.0
        self.initial_bonus_available = True
        self.review_ready = False
        self.addon_changed_mute = False
        self.mute_enforcement_active = False
        self.pending_answer_card_id: int | None = None
        self.pending_answer_flag: int | None = None
        self.pending_answer_elapsed_seconds: float | None = None
        self.answer_awards_by_undo_step: dict[int, float] = {}
        self.last_display_second: int | None = None
        self.last_display_paused: bool | None = None
        self.last_flag_award_key: tuple[int, int, int] | None = None
        self.fullscreen_restore_mode: str | None = None
        self.fullscreen_restore_geometry: Any = None

        self.tick_timer = QTimer(mw)
        self.tick_timer.setSingleShot(False)
        self.tick_timer.setInterval(TICK_MILLISECONDS)
        try:
            from aqt.qt import Qt

            self.tick_timer.setTimerType(Qt.TimerType.PreciseTimer)
        except Exception:
            pass
        self.tick_timer.timeout.connect(self._on_tick)
        self.tick_timer.start()

        self.enforce_timer = QTimer(mw)
        self.enforce_timer.setSingleShot(False)
        self.enforce_timer.timeout.connect(self._on_enforce_mute)

        gui_hooks.reviewer_did_show_question.append(self._on_show_question)
        gui_hooks.reviewer_did_show_answer.append(self._on_show_answer)
        gui_hooks.reviewer_will_answer_card.append(self._on_will_answer_card)
        gui_hooks.reviewer_did_answer_card.append(self._on_did_answer_card)
        gui_hooks.reviewer_will_end.append(self._on_review_end)
        gui_hooks.state_did_change.append(self._on_state_changed)
        gui_hooks.state_did_undo.append(self._on_undo)
        gui_hooks.profile_will_close.append(self._on_profile_close)
        gui_hooks.profile_did_open.append(self._on_profile_open)

    def config(self) -> dict[str, Any]:
        return self._config_cache

    def apply_config(self) -> None:
        raw_config = mw.addonManager.getConfig(ADDON_PACKAGE)
        self._config_cache, config_changed = config_with_migration(raw_config)
        if config_changed:
            try:
                mw.addonManager.writeConfig(ADDON_PACKAGE, self._config_cache)
            except Exception:
                pass
        config = self.config()
        maximum = int(config["max_timer_seconds"])
        if maximum > 0:
            self.remaining_seconds = min(self.remaining_seconds, float(maximum))
        self._restart_enforcement_interval_if_needed()
        self.last_display_second = None
        self.last_display_paused = None
        self.last_flag_award_key = None
        self._update_display(force=True)
        self._update_flag_award_display(force=True)
        self._sync_mute_state()

    def _is_review_open(self) -> bool:
        return mw.state == "review" and self.review_ready

    def _current_card_flag(self) -> int:
        reviewer = getattr(mw, "reviewer", None)
        card = getattr(reviewer, "card", None)
        if card is None:
            return 0
        try:
            return int(card.user_flag())
        except Exception:
            return 0

    def _current_card(self) -> Any | None:
        reviewer = getattr(mw, "reviewer", None)
        return getattr(reviewer, "card", None)

    @staticmethod
    def _is_new_card(card: Any) -> bool:
        if card is None:
            return False
        # CardType.NEW is 0. queue==0 is retained as a compatibility
        # fallback for add-ons or older scheduler-facing card objects.
        try:
            if int(card.type) == 0:
                return True
        except Exception:
            pass
        try:
            return int(card.queue) == 0
        except Exception:
            return False

    def _current_pause_reason(self) -> str | None:
        if not self._is_review_open():
            return None
        card = self._current_card()
        if card is None:
            return None
        try:
            if is_pause_flag(int(card.user_flag())):
                return "purple"
        except Exception:
            pass
        if self._is_new_card(card):
            return "new"
        return None

    def _is_current_card_paused(self) -> bool:
        return self._current_pause_reason() is not None

    def _apply_initial_bonus_if_eligible(self, card: Any) -> None:
        if not self.initial_bonus_available:
            return
        try:
            flag = int(card.user_flag())
        except Exception:
            flag = 0
        # Purple cards are fully exempt. Preserve the startup bonus for the
        # first non-purple card instead of consuming it on an exempt card.
        if is_pause_flag(flag):
            return
        self.initial_bonus_available = False
        config = self.config()
        base = seconds_for_flag(config, flag)
        bonus = initial_bonus_seconds(base, float(config["initial_bonus_multiplier"]))
        self._add_time(bonus)

    def _on_tick(self) -> None:
        config = self.config()
        if not config["enabled"]:
            self._stop_mute_enforcement(unmute=True)
            return

        if not self._is_review_open():
            self._stop_mute_enforcement(unmute=True)
            return

        if self._is_current_card_paused():
            self._stop_mute_enforcement(unmute=True)
            self._update_display()
            self._update_flag_award_display()
            return

        if self.remaining_seconds > 0:
            self.remaining_seconds = max(0.0, self.remaining_seconds - TICK_SECONDS)

        self._update_display()
        self._update_flag_award_display()
        self._sync_mute_state()

    def _on_show_question(self, card: Any) -> None:
        config = self.config()
        if not config["enabled"]:
            self.review_ready = True
            self._stop_mute_enforcement(unmute=True)
            self._schedule_display_update()
            return

        self._apply_initial_bonus_if_eligible(card)

        self.review_ready = True
        self._schedule_display_update()
        self._schedule_flag_award_update()
        self._sync_mute_state()

    def _on_show_answer(self, card: Any) -> None:
        self.review_ready = True
        self._schedule_display_update()
        self._schedule_flag_award_update()
        self._sync_mute_state()

    def on_current_flag_changed(self) -> None:
        if mw.state != "review":
            return
        reviewer = getattr(mw, "reviewer", None)
        card = getattr(reviewer, "card", None)
        if card is None:
            return
        # If the first displayed card was purple, the startup bonus was
        # deferred. Apply it when that card becomes a normal timed card.
        self._apply_initial_bonus_if_eligible(card)
        self.last_display_second = None
        self.last_display_paused = None
        self.last_flag_award_key = None
        self._schedule_display_update()
        self._schedule_flag_award_update()
        self._sync_mute_state()

    def _on_will_answer_card(
        self,
        ease_tuple: tuple[bool, int],
        reviewer: Any,
        card: Any,
    ) -> tuple[bool, int]:
        proceed = bool(ease_tuple[0])
        if proceed:
            self.pending_answer_card_id = int(card.id)
            try:
                elapsed_seconds = max(0.0, float(card.time_taken(capped=False)) / 1000.0)
            except Exception:
                elapsed_seconds = 0.0
            self.pending_answer_elapsed_seconds = elapsed_seconds

            try:
                flag = int(card.user_flag())
            except Exception:
                flag = 0

            # Unflagged cards are classified at the moment the answer button
            # is pressed. Mutating the in-memory card before Anki builds the
            # answer operation makes the flag part of the same undo entry.
            if flag == 0:
                automatic_flag = auto_flag_for_elapsed(self.config(), elapsed_seconds)
                if automatic_flag is not None:
                    try:
                        card.flags = (int(card.flags) & ~0b111) | int(automatic_flag)
                        flag = int(automatic_flag)
                    except Exception:
                        pass

            self.pending_answer_flag = flag
        else:
            self._clear_pending_answer()
        return ease_tuple

    def _on_did_answer_card(self, reviewer: Any, card: Any, ease: int) -> None:
        config = self.config()
        card_id = int(card.id)
        if self.pending_answer_card_id == card_id and self.pending_answer_flag is not None:
            flag = self.pending_answer_flag
        else:
            # Fallback for another add-on invoking the did-answer hook directly.
            flag = int(card.user_flag())
        self._clear_pending_answer()

        if not config["enabled"]:
            return

        if is_pause_flag(flag):
            self._update_display(force=True)
            self._sync_mute_state()
            return

        requested = seconds_for_flag(config, flag) + wrong_answer_seconds(config, ease)
        actual = self._add_time(requested)
        if actual > 0:
            undo_step = self._current_undo_step()
            if undo_step is not None:
                self.answer_awards_by_undo_step[undo_step] = (
                    self.answer_awards_by_undo_step.get(undo_step, 0.0) + actual
                )
                self._trim_undo_records()

    def on_set_due_succeeded(self, flag: int) -> None:
        """Add the current flag award after Set Due Date completes."""
        config = self.config()
        if not config["enabled"]:
            return
        if is_pause_flag(flag):
            return
        actual = self._add_time(seconds_for_flag(config, flag))
        if actual <= 0:
            return
        undo_step = self._current_undo_step()
        if undo_step is not None:
            self.answer_awards_by_undo_step[undo_step] = (
                self.answer_awards_by_undo_step.get(undo_step, 0.0) + actual
            )
            self._trim_undo_records()

    def _on_undo(self, changes: Any) -> None:
        try:
            undo_step = int(changes.counter)
        except (AttributeError, TypeError, ValueError):
            return

        actual = self.answer_awards_by_undo_step.pop(undo_step, None)
        if actual is None:
            return

        self.remaining_seconds = subtract_award(self.remaining_seconds, actual)
        self.last_display_second = None
        self.last_display_paused = None
        self._update_display(force=True)
        self._sync_mute_state()

    def _on_review_end(self, *args: Any) -> None:
        self.review_ready = False
        self._clear_pending_answer()
        self._stop_mute_enforcement(unmute=True)

    def _on_state_changed(self, new_state: Any, old_state: Any) -> None:
        # state_did_change fires after Reviewer.show(), so on entry the
        # reviewer_did_show_question hook has already marked review_ready.
        if str(new_state) != "review":
            self.review_ready = False
            self._clear_pending_answer()
            self._stop_mute_enforcement(unmute=True)

    def _on_profile_open(self, *args: Any) -> None:
        self.remaining_seconds = 0.0
        self.initial_bonus_available = True
        self.review_ready = False
        self.addon_changed_mute = False
        self.mute_enforcement_active = False
        self.pending_answer_card_id = None
        self.pending_answer_flag = None
        self.pending_answer_elapsed_seconds = None
        self.answer_awards_by_undo_step.clear()
        self.last_display_second = None
        self.last_display_paused = None
        self.last_flag_award_key = None
        self.fullscreen_restore_mode = None
        self.fullscreen_restore_geometry = None
        self.enforce_timer.stop()
        if not self.tick_timer.isActive():
            self.tick_timer.start()

    def _on_profile_close(self, *args: Any) -> None:
        self.tick_timer.stop()
        self._stop_mute_enforcement(unmute=True)
        self.answer_awards_by_undo_step.clear()
        self._clear_pending_answer()

    def _add_time(self, requested_seconds: float) -> float:
        config = self.config()
        maximum = int(config["max_timer_seconds"])
        after, actual = add_capped(self.remaining_seconds, requested_seconds, maximum)
        self.remaining_seconds = after
        if self.remaining_seconds > 0:
            self._stop_mute_enforcement(unmute=True)
        self.last_display_second = None
        self.last_display_paused = None
        self._update_display(force=True)
        return actual

    def _current_undo_step(self) -> int | None:
        try:
            status = mw.col.undo_status()
            step = int(status.last_step)
            return step if step > 0 else None
        except Exception:
            return None

    def _trim_undo_records(self) -> None:
        # The values are session-only. Keeping the newest 500 protects against
        # unbounded growth during exceptionally long sessions.
        if len(self.answer_awards_by_undo_step) <= 500:
            return
        oldest = sorted(self.answer_awards_by_undo_step)[:-500]
        for step in oldest:
            self.answer_awards_by_undo_step.pop(step, None)

    def _clear_pending_answer(self) -> None:
        self.pending_answer_card_id = None
        self.pending_answer_flag = None
        self.pending_answer_elapsed_seconds = None

    def _sync_mute_state(self) -> None:
        config = self.config()
        if not config["enabled"] or not self._is_review_open():
            self._stop_mute_enforcement(unmute=True)
            return

        if self._is_current_card_paused():
            self._stop_mute_enforcement(unmute=True)
            return

        if self.remaining_seconds <= 0:
            self._start_mute_enforcement()
        else:
            self._stop_mute_enforcement(unmute=True)

    def _start_mute_enforcement(self) -> None:
        if not self.mute_enforcement_active:
            self.mute_enforcement_active = True
            self._capture_fullscreen_restore_state()
            self._ensure_fullscreen()
            self._mute_if_needed()
            self._restart_enforcement_interval_if_needed()
        else:
            self._ensure_fullscreen()
            if not self.enforce_timer.isActive():
                self._restart_enforcement_interval_if_needed()

    def _restart_enforcement_interval_if_needed(self) -> None:
        if not self.mute_enforcement_active:
            self.enforce_timer.stop()
            return
        interval = float(self.config()["enforce_mute_every_seconds"])
        interval_ms = max(500, int(round(interval * 1000)))
        if self.enforce_timer.interval() != interval_ms or not self.enforce_timer.isActive():
            self.enforce_timer.start(interval_ms)

    def _stop_mute_enforcement(self, *, unmute: bool) -> None:
        self.mute_enforcement_active = False
        self.enforce_timer.stop()
        self._restore_window_state()
        if unmute:
            self._unmute_if_addon_changed()

    def _on_enforce_mute(self) -> None:
        if not self.mute_enforcement_active or not self._is_review_open():
            self._stop_mute_enforcement(unmute=True)
            return
        if self._is_current_card_paused():
            self._stop_mute_enforcement(unmute=True)
            return
        if self.remaining_seconds > 0:
            self._stop_mute_enforcement(unmute=True)
            return
        self._ensure_fullscreen()
        self._mute_if_needed()

    def _capture_fullscreen_restore_state(self) -> None:
        if self.fullscreen_restore_mode is not None:
            return
        try:
            self.fullscreen_restore_geometry = mw.saveGeometry()
        except Exception:
            self.fullscreen_restore_geometry = None
        try:
            if mw.isFullScreen():
                self.fullscreen_restore_mode = "fullscreen"
            elif mw.isMinimized():
                self.fullscreen_restore_mode = "minimized"
            elif mw.isMaximized():
                self.fullscreen_restore_mode = "maximized"
            else:
                self.fullscreen_restore_mode = "normal"
        except Exception:
            self.fullscreen_restore_mode = "normal"

    def _ensure_fullscreen(self) -> None:
        try:
            if mw.isFullScreen():
                return
            mw.showFullScreen()
            mw.raise_()
            mw.activateWindow()
        except Exception:
            pass

    def _restore_window_state(self) -> None:
        mode = self.fullscreen_restore_mode
        geometry = self.fullscreen_restore_geometry
        self.fullscreen_restore_mode = None
        self.fullscreen_restore_geometry = None
        if mode is None:
            return
        try:
            if mode == "fullscreen":
                mw.showFullScreen()
                return
            mw.showNormal()
            if geometry is not None:
                try:
                    mw.restoreGeometry(geometry)
                except Exception:
                    pass
            if mode == "maximized":
                mw.showMaximized()
            elif mode == "minimized":
                mw.showMinimized()
        except Exception:
            pass

    def _mute_if_needed(self) -> None:
        state = get_system_mute()
        if state is True:
            return
        if set_system_mute(True):
            # If the state was known to be unmuted, or could not be queried,
            # this call is the one that changed it.
            self.addon_changed_mute = True

    def _unmute_if_addon_changed(self) -> None:
        if not self.addon_changed_mute:
            return
        if set_system_mute(False):
            self.addon_changed_mute = False

    def _schedule_display_update(self) -> None:
        self.last_display_second = None
        self.last_display_paused = None
        self._update_display(force=True)
        try:
            mw.progress.single_shot(50, lambda: self._update_display(force=True))
            mw.progress.single_shot(150, lambda: self._update_display(force=True))
        except Exception:
            pass

    def _update_display(self, *, force: bool = False) -> None:
        if mw.state != "review":
            return
        second = display_seconds(self.remaining_seconds)
        paused = self._is_current_card_paused()
        if (
            not force
            and self.last_display_second == second
            and self.last_display_paused == paused
        ):
            return
        self.last_display_second = second
        self.last_display_paused = paused

        reviewer = getattr(mw, "reviewer", None)
        bottom = getattr(reviewer, "bottom", None)
        web = getattr(bottom, "web", None)
        if web is None:
            return

        pause_reason = self._current_pause_reason()
        if pause_reason == "purple":
            pause_text = "（紫フラグ：停止中）"
        elif pause_reason == "new":
            pause_text = "（新規カード：停止中）"
        else:
            pause_text = ""
        text = f"持ち時間 {format_remaining(self.remaining_seconds)}{pause_text}"
        js = f"""
(function() {{
  const elementId = {json.dumps(DISPLAY_ELEMENT_ID)};
  let node = document.getElementById(elementId);
  if (!node) {{
    node = document.createElement('span');
    node.id = elementId;
    node.style.marginLeft = '10px';
    node.style.padding = '2px 8px';
    node.style.borderRadius = '5px';
    node.style.whiteSpace = 'nowrap';
    node.style.fontVariantNumeric = 'tabular-nums';
    node.style.fontWeight = '600';
    node.style.opacity = '0.9';

    const timeNode = document.getElementById('time');
    if (timeNode && timeNode.parentElement) {{
      timeNode.insertAdjacentElement('afterend', node);
    }} else {{
      node.style.position = 'fixed';
      node.style.right = '12px';
      node.style.bottom = '8px';
      document.body.appendChild(node);
    }}
  }}
  node.textContent = {json.dumps(text)};
}})();
"""
        try:
            web.eval(js)
        except Exception:
            pass


    def _schedule_flag_award_update(self) -> None:
        self.last_flag_award_key = None
        self._update_flag_award_display(force=True)
        try:
            mw.progress.single_shot(50, lambda: self._update_flag_award_display(force=True))
            mw.progress.single_shot(150, lambda: self._update_flag_award_display(force=True))
        except Exception:
            pass

    def _update_flag_award_display(self, *, force: bool = False) -> None:
        if mw.state != "review":
            return
        reviewer = getattr(mw, "reviewer", None)
        card = getattr(reviewer, "card", None)
        web = getattr(reviewer, "web", None)
        if card is None or web is None:
            return
        try:
            flag = int(card.user_flag())
        except Exception:
            flag = 0

        automatic_flag: int | None = None
        if flag == 0:
            try:
                elapsed_seconds = max(
                    0.0, float(card.time_taken(capped=False)) / 1000.0
                )
            except Exception:
                elapsed_seconds = 0.0
            automatic_flag = auto_flag_for_elapsed(self.config(), elapsed_seconds)
            if automatic_flag is None:
                seconds = 0
                text = "自動判定なし"
            else:
                seconds = seconds_for_flag(self.config(), automatic_flag)
                color_name = FLAG_NAMES.get(automatic_flag, "")
                text = f"自動 {color_name} +{seconds}秒"
        else:
            seconds = seconds_for_flag(self.config(), flag)
            text = "停止・加算なし" if is_pause_flag(flag) else f"+{seconds}秒"

        key = (flag, automatic_flag or -1, seconds)
        if not force and self.last_flag_award_key == key:
            return
        self.last_flag_award_key = key
        js = f"""
(function() {{
  const elementId = {json.dumps(FLAG_AWARD_ELEMENT_ID)};
  const value = {json.dumps(text)};
  const anchor = document.getElementById('_flag');
  let node = document.getElementById(elementId);
  if (!node) {{
    node = document.createElement('span');
    node.id = elementId;
    node.style.position = 'fixed';
    node.style.zIndex = '2147483647';
    node.style.top = '8px';
    node.style.right = '38px';
    node.style.padding = '2px 7px';
    node.style.borderRadius = '5px';
    node.style.whiteSpace = 'nowrap';
    node.style.fontVariantNumeric = 'tabular-nums';
    node.style.fontWeight = '600';
    node.style.fontSize = '13px';
    node.style.lineHeight = '1.4';
    node.style.opacity = '0.9';
    node.style.pointerEvents = 'none';
    node.style.background = 'var(--canvas, rgba(255,255,255,0.82))';
    node.style.color = 'var(--fg, inherit)';
    if (anchor && anchor.parentNode) {{
      anchor.insertAdjacentElement('afterend', node);
    }} else {{
      document.body.appendChild(node);
    }}
  }}
  node.textContent = value;

  function placeNextToFlag() {{
    const flagNode = document.getElementById('_flag');
    if (!flagNode || flagNode.hidden) {{
      node.style.top = '8px';
      node.style.right = '38px';
      node.style.left = 'auto';
      return;
    }}
    const rect = flagNode.getBoundingClientRect();
    if (!rect.width && !rect.height) return;
    node.style.top = Math.max(4, rect.top + (rect.height - node.offsetHeight) / 2) + 'px';
    node.style.right = Math.max(4, window.innerWidth - rect.left + 6) + 'px';
    node.style.left = 'auto';
  }}

  placeNextToFlag();
  window.requestAnimationFrame(placeNextToFlag);
}})();
"""
        try:
            web.eval(js)
        except Exception:
            pass


class SettingsDialog(QDialog):
    def __init__(self, controller: ReviewTimeBankController) -> None:
        super().__init__(mw)
        self.controller = controller
        self.setWindowTitle("Review Time Bank Mute 設定")
        self.setMinimumWidth(470)

        config = controller.config()
        layout = QVBoxLayout(self)
        explanation = QLabel(
            "レビュー画面を開いている間だけ持ち時間が減少します。"
            "カード回答時と「期日を変更」の完了時に、フラグに対応した秒数が加算されます。"
            "Again（誤答）の場合は、さらに誤答加算が追加されます。"
            "紫フラグのカードでは持ち時間を停止し、回答・誤答・期日変更でも時間を加算しません。"
            "新規カードの表示中も持ち時間を停止しますが、回答時の時間は通常どおり加算します。"
            "フラグなしカードは、回答時の実経過時間を超える最小の設定秒数のフラグへ自動分類されます。"
            "時間切れ中はAnkiをフルスクリーンにし、時間が復活すると元の表示へ戻します。"
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        form = QFormLayout()
        layout.addLayout(form)

        self.enabled = QCheckBox("機能を有効にする")
        self.enabled.setChecked(bool(config["enabled"]))
        form.addRow("有効化", self.enabled)

        self.maximum = QSpinBox()
        self.maximum.setRange(0, 36000)
        self.maximum.setSuffix(" 秒")
        self.maximum.setSpecialValueText("上限なし")
        self.maximum.setValue(int(config["max_timer_seconds"]))
        form.addRow("タイマー上限", self.maximum)

        self.initial_multiplier = QDoubleSpinBox()
        self.initial_multiplier.setRange(0.0, 10.0)
        self.initial_multiplier.setDecimals(2)
        self.initial_multiplier.setSingleStep(0.1)
        self.initial_multiplier.setSuffix(" 倍")
        self.initial_multiplier.setValue(float(config["initial_bonus_multiplier"]))
        form.addRow("起動後の初回カード倍率", self.initial_multiplier)

        self.wrong_answer = QSpinBox()
        self.wrong_answer.setRange(0, 36000)
        self.wrong_answer.setSuffix(" 秒")
        self.wrong_answer.setValue(int(config["wrong_answer_seconds"]))
        form.addRow("誤答（Again）の追加時間", self.wrong_answer)

        self.enforce_interval = QDoubleSpinBox()
        self.enforce_interval.setRange(0.5, 60.0)
        self.enforce_interval.setDecimals(1)
        self.enforce_interval.setSingleStep(0.5)
        self.enforce_interval.setSuffix(" 秒")
        self.enforce_interval.setValue(float(config["enforce_mute_every_seconds"]))
        form.addRow("再ミュート間隔", self.enforce_interval)

        flag_heading = QLabel("カード回答時の追加時間")
        flag_heading.setStyleSheet("font-weight: 600; margin-top: 8px;")
        layout.addWidget(flag_heading)

        flag_form = QFormLayout()
        layout.addLayout(flag_form)
        self.flag_inputs: dict[int, QSpinBox] = {}
        for flag, name in FLAG_NAMES.items():
            spin = QSpinBox()
            spin.setRange(0, 36000)
            spin.setSuffix(" 秒")
            spin.setValue(seconds_for_flag(config, flag))
            label = name
            if flag == PAUSE_FLAG:
                spin.setValue(0)
                spin.setEnabled(False)
                label = "紫（停止・加算なし）"
            self.flag_inputs[flag] = spin
            flag_form.addRow(label, spin)

        note = QLabel(
            "タイマー上限の初期値は180秒です。0秒にすると上限なしになります。"
            "初回カードの表示時には、そのカードの設定秒数×倍率が加算されます。"
            "最初のカードが紫の場合、初回ボーナスは最初の紫以外のカードまで保留されます。"
            "新規カードの表示中はタイマーが停止し、回答すると通常どおり時間が加算されます。"
            "レビュー画面のフラグ横には、現在のフラグで加算される秒数を表示します。"
            "フラグなしの場合は、経過時間に応じて現時点で自動付与される色と秒数を表示します。"
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def saved_config(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled.isChecked(),
            "max_timer_seconds": int(self.maximum.value()),
            "config_version": 4,
            "initial_bonus_multiplier": float(self.initial_multiplier.value()),
            "wrong_answer_seconds": int(self.wrong_answer.value()),
            "enforce_mute_every_seconds": float(self.enforce_interval.value()),
            "flag_seconds": {
                str(flag): (0 if flag == PAUSE_FLAG else int(spin.value()))
                for flag, spin in self.flag_inputs.items()
            },
        }


def show_settings_dialog() -> bool:
    dialog = SettingsDialog(_CONTROLLER)
    result = dialog.exec()
    if result != int(QDialog.DialogCode.Accepted):
        return False
    mw.addonManager.writeConfig(ADDON_PACKAGE, dialog.saved_config())
    _CONTROLLER.apply_config()
    return True


def set_system_mute(mute: bool) -> bool:
    system = platform.system()
    if system == "Windows":
        return _set_windows_mute(mute)
    if system == "Darwin":
        return _run_command(
            ["osascript", "-e", f"set volume output muted {'true' if mute else 'false'}"]
        )
    if system == "Linux":
        return _set_linux_mute(mute)
    return False


def get_system_mute() -> bool | None:
    system = platform.system()
    if system == "Windows":
        return _get_windows_mute()
    if system == "Darwin":
        output = _capture_command(
            ["osascript", "-e", "output muted of (get volume settings)"]
        )
        if output is None:
            return None
        lowered = output.strip().lower()
        if lowered == "true":
            return True
        if lowered == "false":
            return False
        return None
    if system == "Linux":
        return _get_linux_mute()
    return None


def _set_linux_mute(mute: bool) -> bool:
    state = "1" if mute else "0"
    amixer_state = "mute" if mute else "unmute"
    commands = [
        ["pactl", "set-sink-mute", "@DEFAULT_SINK@", state],
        ["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", state],
        ["amixer", "-D", "pulse", "sset", "Master", amixer_state],
        ["amixer", "sset", "Master", amixer_state],
    ]
    return any(_run_command(command) for command in commands)


def _get_linux_mute() -> bool | None:
    output = _capture_command(["pactl", "get-sink-mute", "@DEFAULT_SINK@"])
    if output is not None:
        lowered = output.lower()
        if "yes" in lowered:
            return True
        if "no" in lowered:
            return False

    output = _capture_command(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"])
    if output is not None:
        return "[muted]" in output.lower()

    for command in (["amixer", "-D", "pulse", "get", "Master"], ["amixer", "get", "Master"]):
        output = _capture_command(list(command))
        if output is None:
            continue
        lowered = output.lower()
        if "[off]" in lowered:
            return True
        if "[on]" in lowered:
            return False
    return None


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def from_string(cls, value: str) -> "GUID":
        data = uuid.UUID(value).bytes_le
        data1, data2, data3 = struct.unpack("<IHH", data[:8])
        data4 = (ctypes.c_ubyte * 8).from_buffer_copy(data[8:])
        return cls(data1, data2, data3, data4)


CLSID_MMDeviceEnumerator = GUID.from_string("{BCDE0395-E52F-467C-8E3D-C4579291692E}")
IID_IMMDeviceEnumerator = GUID.from_string("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
IID_IAudioEndpointVolume = GUID.from_string("{5CDF2C82-841E-4546-9722-0CF74078229A}")
CLSCTX_INPROC_SERVER = 0x1
CLSCTX_ALL = 0x17
HRESULT = ctypes.c_long
RPC_E_CHANGED_MODE = 0x80010106


def _get_vtable_functions(interface_ptr: ctypes.c_void_p) -> ctypes.Array:
    return ctypes.cast(interface_ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents


def _release_com_object(interface_ptr: ctypes.c_void_p) -> None:
    if not interface_ptr:
        return
    vtable = _get_vtable_functions(interface_ptr)
    release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtable[2])
    release(interface_ptr)


def _with_windows_endpoint(callback: Callable[[ctypes.Array, ctypes.c_void_p], Any]) -> Any:
    ole32 = ctypes.windll.ole32
    should_uninitialize = False
    enumerator = ctypes.c_void_p()
    device = ctypes.c_void_p()
    endpoint = ctypes.c_void_p()
    try:
        hr = int(ole32.CoInitialize(None))
        hr_unsigned = ctypes.c_ulong(hr).value
        if hr >= 0:
            should_uninitialize = True
        elif hr_unsigned != RPC_E_CHANGED_MODE:
            return None

        ole32.CoCreateInstance.argtypes = [
            ctypes.POINTER(GUID),
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(GUID),
            ctypes.POINTER(ctypes.c_void_p),
        ]
        ole32.CoCreateInstance.restype = HRESULT

        hr = ole32.CoCreateInstance(
            ctypes.byref(CLSID_MMDeviceEnumerator),
            None,
            CLSCTX_INPROC_SERVER,
            ctypes.byref(IID_IMMDeviceEnumerator),
            ctypes.byref(enumerator),
        )
        if hr != 0 or not enumerator:
            return None

        enum_vtable = _get_vtable_functions(enumerator)
        get_default_audio_endpoint = ctypes.WINFUNCTYPE(
            HRESULT,
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_void_p),
        )(enum_vtable[4])
        hr = get_default_audio_endpoint(enumerator, 0, 1, ctypes.byref(device))
        if hr != 0 or not device:
            return None

        device_vtable = _get_vtable_functions(device)
        activate = ctypes.WINFUNCTYPE(
            HRESULT,
            ctypes.c_void_p,
            ctypes.POINTER(GUID),
            wintypes.DWORD,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        )(device_vtable[3])
        hr = activate(
            device,
            ctypes.byref(IID_IAudioEndpointVolume),
            CLSCTX_ALL,
            None,
            ctypes.byref(endpoint),
        )
        if hr != 0 or not endpoint:
            return None

        endpoint_vtable = _get_vtable_functions(endpoint)
        return callback(endpoint_vtable, endpoint)
    except Exception:
        return None
    finally:
        _release_com_object(endpoint)
        _release_com_object(device)
        _release_com_object(enumerator)
        if should_uninitialize:
            try:
                ole32.CoUninitialize()
            except Exception:
                pass


def _set_windows_mute(mute: bool) -> bool:
    def call(vtable: ctypes.Array, endpoint: ctypes.c_void_p) -> bool:
        set_mute = ctypes.WINFUNCTYPE(
            HRESULT,
            ctypes.c_void_p,
            wintypes.BOOL,
            ctypes.c_void_p,
        )(vtable[14])
        return set_mute(endpoint, wintypes.BOOL(1 if mute else 0), None) == 0

    return bool(_with_windows_endpoint(call))


def _get_windows_mute() -> bool | None:
    def call(vtable: ctypes.Array, endpoint: ctypes.c_void_p) -> bool | None:
        muted = wintypes.BOOL()
        get_mute = ctypes.WINFUNCTYPE(
            HRESULT,
            ctypes.c_void_p,
            ctypes.POINTER(wintypes.BOOL),
        )(vtable[15])
        if get_mute(endpoint, ctypes.byref(muted)) != 0:
            return None
        return bool(muted.value)

    result = _with_windows_endpoint(call)
    return result if isinstance(result, bool) else None


def _subprocess_kwargs() -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "check": False,
        "capture_output": True,
        "text": True,
        "timeout": 5,
    }
    if platform.system() == "Windows":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return kwargs


def _run_command(command: list[str]) -> bool:
    try:
        completed = subprocess.run(command, **_subprocess_kwargs())
        return completed.returncode == 0
    except Exception:
        return False


def _capture_command(command: list[str]) -> str | None:
    try:
        completed = subprocess.run(command, **_subprocess_kwargs())
        if completed.returncode != 0:
            return None
        return completed.stdout
    except Exception:
        return None


def _install_reviewer_patches() -> None:
    original_set_due_dialog = reviewer_module.set_due_date_dialog
    if not getattr(original_set_due_dialog, "_review_time_bank_wrapped", False):
        def set_due_dialog_with_award(*args: Any, **kwargs: Any) -> Any:
            op = original_set_due_dialog(*args, **kwargs)
            if op is None or mw.state != "review":
                return op
            reviewer = getattr(mw, "reviewer", None)
            card = getattr(reviewer, "card", None)
            if card is None:
                return op
            if kwargs.get("config_key") != Config.String.SET_DUE_REVIEWER:
                return op
            card_ids = kwargs.get("card_ids")
            if isinstance(card_ids, (list, tuple, set)):
                try:
                    if int(card.id) not in {int(card_id) for card_id in card_ids}:
                        return op
                except Exception:
                    return op
            try:
                flag = int(card.user_flag())
            except Exception:
                flag = 0
            previous_success = getattr(op, "_success", None)

            def success(result: Any) -> None:
                if previous_success is not None:
                    previous_success(result)
                _CONTROLLER.on_set_due_succeeded(flag)

            op.success(success)
            return op

        setattr(set_due_dialog_with_award, "_review_time_bank_wrapped", True)
        reviewer_module.set_due_date_dialog = set_due_dialog_with_award

    original_rev_html = reviewer_module.Reviewer.revHtml
    if not getattr(original_rev_html, "_review_time_bank_wrapped", False):
        def rev_html_with_flag_seconds(reviewer: Any) -> str:
            html = original_rev_html(reviewer)
            if FLAG_AWARD_ELEMENT_ID in html:
                return html
            marker = '<div id="_flag" hidden>&#x2691;</div>'
            addition = (
                marker
                + f'<span id="{FLAG_AWARD_ELEMENT_ID}" '
                + 'style="position:fixed;top:8px;right:38px;z-index:2147483647;'
                + 'padding:2px 7px;border-radius:5px;white-space:nowrap;'
                + 'font-variant-numeric:tabular-nums;font-weight:600;font-size:13px;'
                + 'line-height:1.4;opacity:.9;pointer-events:none"></span>'
            )
            if marker in html:
                return html.replace(marker, addition, 1)
            return html

        setattr(rev_html_with_flag_seconds, "_review_time_bank_wrapped", True)
        reviewer_module.Reviewer.revHtml = rev_html_with_flag_seconds

    original_update_flag_icon = reviewer_module.Reviewer._update_flag_icon
    if not getattr(original_update_flag_icon, "_review_time_bank_wrapped", False):
        def update_flag_icon_with_seconds(reviewer: Any) -> Any:
            result = original_update_flag_icon(reviewer)
            if reviewer is getattr(mw, "reviewer", None):
                _CONTROLLER.on_current_flag_changed()
            return result

        setattr(update_flag_icon_with_seconds, "_review_time_bank_wrapped", True)
        reviewer_module.Reviewer._update_flag_icon = update_flag_icon_with_seconds


_CONTROLLER = ReviewTimeBankController()
_install_reviewer_patches()
mw.addonManager.setConfigAction(ADDON_PACKAGE, show_settings_dialog)
