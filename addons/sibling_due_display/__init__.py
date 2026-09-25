# -*- coding: utf-8 -*-
"""兄弟カード期日表示 (Sibling Due Display)

レビュー画面内に、同一ノート由来の「兄弟カード」の“次回表示タイミング(期日)”だけを
強調して表示します。

想定: Anki 24.06+ (Qt5)
"""

from __future__ import annotations

import html
import re
import time
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from aqt import mw, gui_hooks

ADDON_NAME = "兄弟カード期日表示"


# -----------------------------
# Config
# -----------------------------

def _default_config() -> Dict[str, Any]:
    return {
        "enabled": True,
        "position": "bottom",  # "bottom" or "top"
        "show_on_question": True,
        "show_on_answer": True,
        "max_siblings": 10,
        "show_when_no_siblings": False,
        "show_prediction": True,
        "debug": False,
    }


def _get_config() -> Dict[str, Any]:
    cfg = _default_config()
    try:
        user_cfg = mw.addonManager.getConfig(__name__) or {}
        if isinstance(user_cfg, dict):
            cfg.update(user_cfg)
    except Exception:
        pass
    return cfg


def _debug_tip(msg: str) -> None:
    cfg = _get_config()
    if not cfg.get("debug", False):
        return
    try:
        from aqt.utils import tooltip

        tooltip(f"{ADDON_NAME}: {msg}")
    except Exception:
        # last resort: stdout
        print(f"{ADDON_NAME}: {msg}")


# -----------------------------
# Core: sibling extraction
# -----------------------------

def _is_reviewer_active() -> bool:
    try:
        return bool(getattr(mw, "reviewer", None)) and getattr(mw, "state", "") == "review"
    except Exception:
        return False


def _get_sibling_cards(card) -> List[Any]:
    """Return other cards with the same nid (excluding the current card)."""
    if not mw.col:
        return []

    try:
        cids = mw.col.db.list("select id from cards where nid=? order by ord", card.nid)
    except Exception as e:
        _debug_tip(f"sibling query failed: {e}")
        return []

    out = []
    for cid in cids:
        if cid == card.id:
            continue
        try:
            out.append(mw.col.get_card(cid))
        except Exception:
            continue
    return out


# -----------------------------
# Due formatting (due only)
# -----------------------------

def _sched_today_date() -> date:
    """Return the date that matches Anki's scheduler 'today' (respects day rollover)."""
    if mw.col:
        try:
            sched = mw.col.sched
            cutoff = getattr(sched, "day_cutoff", None)
            if cutoff is None:
                cutoff = getattr(sched, "dayCutoff", None)
            if callable(cutoff):
                cutoff = cutoff()
            if cutoff:
                # 'day_cutoff' is the Unix timestamp when the day rolls (next boundary).
                # We want the *start* of the current scheduler day, not the end.
                # (Otherwise the calendar date can appear +1 when the day boundary is after midnight.)
                from datetime import datetime

                cutoff_ts = int(cutoff)
                # Safety: some builds may report milliseconds.
                if cutoff_ts > 20_000_000_000:
                    cutoff_ts //= 1000

                start_ts = cutoff_ts - 86400
                # +1 keeps us safely inside the day in case start_ts is exactly at the boundary.
                return datetime.fromtimestamp(start_ts + 1).date()
        except Exception:
            pass
    return date.today()


def _fmt_date_from_delta_days(delta_days: int, base: Optional[date] = None) -> str:
    if base is None:
        base = _sched_today_date()
    return (base + timedelta(days=delta_days)).isoformat()


def _fmt_learning_delta(seconds: int) -> str:
    if seconds <= 0:
        return "0m"
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 24 * 3600:
        h = seconds // 3600
        m = (seconds % 3600) // 60
        return f"{h}h{m:02d}m" if m else f"{h}h"
    # 1日以上先は日付に寄せる
    days = (seconds + 86399) // 86400
    return f"{_fmt_date_from_delta_days(int(days))}"


def _date_with_offset_html(date_str: str, delta_days: int) -> str:
    """Return safe HTML: date (big) + offset (small)."""
    ds = html.escape(date_str)
    if delta_days == 0:
        off = "（今日）"
    elif delta_days > 0:
        off = f"（{delta_days}日後）"
    else:
        off = f"（{abs(delta_days)}日前）"
    return f"{ds}<span class='sdd-offset'>{html.escape(off)}</span>"


def _due_html(c) -> str:
    """Return a concise due HTML (escaped) with day offset when date-based."""
    q = getattr(c, "queue", None)

    # Suspended / buried
    if q == -1:
        return html.escape("停止")
    if q == -2:
        return html.escape("埋め")

    # New cards
    if q == 0:
        return html.escape("New")

    # Learning / Relearning
    if q in (1, 3):
        try:
            due_ts = int(getattr(c, "due", 0))
            # safety: some environments may store ms
            if due_ts > 20_000_000_000:
                due_ts //= 1000
            delta = due_ts - int(time.time())
            # 1日以上先は日付(+何日後)で表示
            if delta >= 24 * 3600:
                days = int((delta + 86399) // 86400)
                return _date_with_offset_html(_fmt_date_from_delta_days(days), days)
            return html.escape(_fmt_learning_delta(delta))
        except Exception:
            return html.escape("学習中")

    # Review
    if q == 2:
        if not mw.col:
            return html.escape("?")
        try:
            due_day = int(getattr(c, "due", 0))
            today_attr = getattr(mw.col.sched, "today", 0)
            today = int(today_attr() if callable(today_attr) else today_attr)
            delta_days = due_day - today
            return _date_with_offset_html(_fmt_date_from_delta_days(delta_days), delta_days)
        except Exception:
            return html.escape("?")

    # Fallback
    return html.escape(str(getattr(c, "due", "?")))



# -----------------------------
# Prediction: if next answer is Hard / Good (Review queue only)
# -----------------------------

def _deck_conf_for_card(c) -> Dict[str, Any]:
    """Best-effort deck config lookup that works across multiple Anki versions."""
    if not mw.col:
        return {}
    decks = mw.col.decks
    did = getattr(c, "did", None)
    if did is None:
        return {}
    try:
        # Most add-ons rely on confForDid
        if hasattr(decks, "confForDid"):
            conf = decks.confForDid(did)
            if isinstance(conf, dict):
                return conf
    except Exception:
        pass
    try:
        # Newer name in some builds
        if hasattr(decks, "config_dict_for_deck_id"):
            conf = decks.config_dict_for_deck_id(did)
            if isinstance(conf, dict):
                return conf
    except Exception:
        pass
    try:
        # Fallback: deck -> config id -> config
        deck = decks.get(did)
        if isinstance(deck, dict) and "conf" in deck:
            cid = deck.get("conf")
            if hasattr(decks, "get_config") and cid is not None:
                conf = decks.get_config(cid)
                if isinstance(conf, dict):
                    return conf
    except Exception:
        pass
    return {}


def _rev_conf_for_card(c) -> Dict[str, Any]:
    conf = _deck_conf_for_card(c)
    if not isinstance(conf, dict):
        return {}
    # Common: conf["rev"] holds review settings
    rev = conf.get("rev")
    if isinstance(rev, dict):
        return rev
    # Some builds: nested under "review"
    rev = conf.get("review")
    if isinstance(rev, dict):
        return rev
    return conf


def _constrained_ivl(ivl: float, rev_conf: Dict[str, Any], prev: int) -> int:
    """Mimics Anki's constrained interval logic (without fuzz)."""
    try:
        ivl_fct = float(rev_conf.get("ivlFct", 1) or 1)
    except Exception:
        ivl_fct = 1.0
    try:
        max_ivl = int(rev_conf.get("maxIvl", 36500) or 36500)
    except Exception:
        max_ivl = 36500

    out = int(ivl * ivl_fct)
    out = max(out, prev + 1, 1)
    out = min(out, max_ivl)
    return int(out)


def _predict_hard_good_due(c) -> Optional[Dict[str, Any]]:
    """Return predicted due info for review cards, or None if unsupported."""
    if not mw.col:
        return None
    q = getattr(c, "queue", None)
    if q != 2:
        return None

    try:
        due_day = int(getattr(c, "due", 0))
        today_attr = getattr(mw.col.sched, "today", 0)
        today = int(today_attr() if callable(today_attr) else today_attr)
    except Exception:
        return None

    # When the next review happens
    review_day = due_day if due_day > today else today

    # Days late affects Good (and Easy) in SM-2 scheduler.
    delay = max(0, today - due_day) if due_day <= today else 0

    try:
        ivl = int(getattr(c, "ivl", 0) or 0)
    except Exception:
        ivl = 0
    ivl = max(ivl, 1)

    try:
        factor = int(getattr(c, "factor", 2500) or 2500)
    except Exception:
        factor = 2500
    if factor <= 0:
        factor = 2500

    fct = factor / 1000.0

    rev_conf = _rev_conf_for_card(c)
    try:
        hard_factor = float(rev_conf.get("hardFactor", 1.2) or 1.2)
    except Exception:
        hard_factor = 1.2

    hard_min = ivl if hard_factor > 1 else 0

    # Predicted next intervals (days), approximating Anki's SM-2 (v2/v3 style) without fuzz.
    hard_ivl = _constrained_ivl(ivl * hard_factor, rev_conf, hard_min)
    good_ivl = _constrained_ivl((ivl + (delay // 2)) * fct, rev_conf, hard_ivl)

    hard_due_day = review_day + hard_ivl
    good_due_day = review_day + good_ivl

    hard_from_today = hard_due_day - today
    good_from_today = good_due_day - today

    return {
        "hard_days": int(hard_from_today),
        "good_days": int(good_from_today),
        "hard_date": _fmt_date_from_delta_days(int(hard_from_today)),
        "good_date": _fmt_date_from_delta_days(int(good_from_today)),
    }


def _predict_html(c) -> str:
    cfg = _get_config()
    if not cfg.get("show_prediction", True):
        return ""
    info = _predict_hard_good_due(c)
    if not info:
        return ""
    # Compact: show predicted calendar date + (n日後)
    hd = int(info["hard_days"])
    gd = int(info["good_days"])
    # safety
    if hd <= 0 or gd <= 0:
        return ""

    htxt = f"{info['hard_date']}（約{hd}日後）"
    gtxt = f"{info['good_date']}（約{gd}日後）"
    return (
        "<span class='sdd-pred'>"
        f"難: <span class='sdd-predv'>{html.escape(htxt)}</span>"
        " / "
        f"普: <span class='sdd-predv'>{html.escape(gtxt)}</span>"
        "</span>"
    )
# -----------------------------
# HTML rendering + injection
# -----------------------------

def _badge_html(label: str, due_html: str, pred_html: str = "") -> str:
    # label: 1/2/3... (template order)
    # due_html: already escaped / safe HTML (may contain <span class='sdd-offset'>...)
    # pred_html: already escaped / safe HTML (may contain spans), may be empty
    label = html.escape(label)
    return (
        "<span class='sdd-badge'>"
        f"<span class='sdd-label'>{label}</span>"
        "<span class='sdd-body'>"
        f"<span class='sdd-due'>{due_html}</span>"
        f"{pred_html}"
        "</span>"
        "</span>"
    )



def _build_block_html(card) -> str:
    cfg = _get_config()
    siblings = _get_sibling_cards(card)
    if not siblings and not cfg.get("show_when_no_siblings", False):
        return ""

    max_sibs = int(cfg.get("max_siblings", 10) or 10)
    siblings = siblings[: max(0, max_sibs)]

    badges: List[str] = []
    for c in siblings:
        try:
            ord_idx = int(getattr(c, "ord", 0)) + 1
        except Exception:
            ord_idx = 0
        due = _due_html(c)
        pred = _predict_html(c)
        badges.append(_badge_html(str(ord_idx) if ord_idx else "?", due, pred))

    if not badges:
        badges_html = "<span class='sdd-none'>兄弟カードなし</span>"
    else:
        badges_html = "".join(badges)

    # Style is embedded but scoped by unique classes to reduce conflicts.
    return f"""<!-- SDD START -->
<div id="sibling-due-display" class="sdd-wrap">
  <style>
    .sdd-wrap {{ margin: 10px 0 0; padding: 8px 10px; border: 1px solid rgba(0,0,0,0.18);
                border-radius: 10px; background: rgba(0,0,0,0.03); }}
    .sdd-title {{ font-size: 12px; opacity: .75; margin-bottom: 6px; }}
    .sdd-badges {{ display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }}
    .sdd-badge {{ display: inline-flex; align-items: flex-start; gap: 8px;
                 padding: 6px 10px; border-radius: 999px;
                 border: 1px solid rgba(0,0,0,0.18); background: rgba(255,255,255,0.6); }}
    .sdd-label {{ font-size: 11px; opacity: .75; min-width: 1.2em; text-align: right; }}
    .sdd-body  {{ display: flex; flex-direction: column; line-height: 1.1; }}
    .sdd-due   {{ font-size: 18px; font-weight: 800; letter-spacing: .2px; }}
    .sdd-offset {{ font-size: 12px; font-weight: 700; opacity: .75; margin-left: 6px; }}
    .sdd-pred  {{ font-size: 11px; font-weight: 700; opacity: .75; margin-top: 3px; }}
    .sdd-predv {{ font-weight: 800; }}
    .sdd-none  {{ font-size: 12px; opacity: .75; }}
  </style>
  <div class="sdd-title">兄弟カードの期日（難/普の次回予測:概算）</div>
  <div class="sdd-badges">{badges_html}</div>
</div>
<!-- SDD END -->
"""


def _inject(html_in: str, block: str, position: str) -> str:
    if not block:
        return html_in

    # Insert before </body> if possible.
    lower = html_in.lower()
    idx = lower.rfind("</body>")
    if idx != -1:
        if position == "top":
            # Insert right after <body...>
            bidx = lower.find("<body")
            if bidx != -1:
                close = lower.find(">", bidx)
                if close != -1:
                    return html_in[: close + 1] + block + html_in[close + 1 :]
        # default: bottom
        return html_in[:idx] + block + html_in[idx:]

    # Fallback: just append
    return html_in + block


def _maybe_inject_siblings(html_in: str, card, kind: Optional[str]) -> str:
    cfg = _get_config()
    if not cfg.get("enabled", True):
        return html_in

    # Only in reviewer
    if not _is_reviewer_active():
        return html_in

    # Respect kind filter if provided
    if isinstance(kind, str):
        k = kind.lower()
        if "question" in k and not cfg.get("show_on_question", True):
            return html_in
        if "answer" in k and not cfg.get("show_on_answer", True):
            return html_in
        # Avoid other contexts (preview, browser, etc.)
        if not k.startswith("review"):
            return html_in

    try:
        html_str = str(html_in)
        # Remove previously injected block (prevents duplicates)
        html_str = re.sub(r"<!-- SDD START -->.*?<!-- SDD END -->", "", html_str, flags=re.DOTALL)
        block = _build_block_html(card)
        return _inject(html_str, block, str(cfg.get("position", "bottom")))
    except Exception as e:
        _debug_tip(f"inject failed: {e}")
        return html_in


# -----------------------------
# Hooks
# -----------------------------

def _on_card_will_show(html_in: str, card, kind: str, *args, **kwargs) -> str:
    return _maybe_inject_siblings(html_in, card, kind)


def _on_reviewer_will_show_question(html_in: str, card, *args, **kwargs) -> str:
    return _maybe_inject_siblings(html_in, card, "reviewQuestion")


def _on_reviewer_will_show_answer(html_in: str, card, *args, **kwargs) -> str:
    return _maybe_inject_siblings(html_in, card, "reviewAnswer")


def _init() -> None:
    # Prefer the newer generic hook if present.
    if hasattr(gui_hooks, "card_will_show"):
        gui_hooks.card_will_show.append(_on_card_will_show)
    else:
        # Fallback for older versions
        if hasattr(gui_hooks, "reviewer_will_show_question"):
            gui_hooks.reviewer_will_show_question.append(_on_reviewer_will_show_question)
        if hasattr(gui_hooks, "reviewer_will_show_answer"):
            gui_hooks.reviewer_will_show_answer.append(_on_reviewer_will_show_answer)


_init()
