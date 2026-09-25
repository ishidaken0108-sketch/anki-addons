from __future__ import annotations

import html
from typing import Any

from aqt import gui_hooks, mw

DEFAULT_CONFIG = {
    "history_count": 3,
    "show_tooltip": True,
    "box_min_width": 54,
    "box_height": 28,
    "font_size": 13,
    "gap": 6,
    "margin_bottom": 8,
    "colors": {
        "again": "#ffb3b3",
        "hard": "#ffd9a8",
        "good": "#b9f6ca",
        "easy": "#b3e5fc",
        "manual": "#ffffff",
        "border": "#cfcfcf",
        "text": "#222222",
    },
}

EASE_LABELS = {
    1: "Again",
    2: "Hard",
    3: "Good",
    4: "Easy",
    0: "Manual",
}

COLOR_KEYS = {
    1: "again",
    2: "hard",
    3: "good",
    4: "easy",
    0: "manual",
}


def _deep_merge(defaults: dict[str, Any], loaded: Any) -> dict[str, Any]:
    result = dict(defaults)
    if not isinstance(loaded, dict):
        return result
    for key, value in loaded.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def get_config() -> dict[str, Any]:
    config = mw.addonManager.getConfig(__name__)
    merged = _deep_merge(DEFAULT_CONFIG, config)
    merged["history_count"] = max(1, min(int(merged.get("history_count", 3)), 20))
    merged["box_min_width"] = max(32, int(merged.get("box_min_width", 54)))
    merged["box_height"] = max(18, int(merged.get("box_height", 28)))
    merged["font_size"] = max(9, int(merged.get("font_size", 13)))
    merged["gap"] = max(0, int(merged.get("gap", 6)))
    merged["margin_bottom"] = max(0, int(merged.get("margin_bottom", 8)))
    merged["show_tooltip"] = bool(merged.get("show_tooltip", True))
    return merged


def format_interval(ivl: int) -> str:
    if ivl == 0:
        return "0日"
    if ivl < 0:
        secs = abs(ivl)
        if secs < 60:
            return f"{secs}秒"
        mins = round(secs / 60)
        if mins < 60:
            return f"{mins}分"
        hours = round(mins / 60)
        if hours < 24:
            return f"{hours}時間"
        days = round(hours / 24)
        return f"{days}日"

    days = ivl
    if days < 30:
        return f"{days}日"
    if days < 365:
        months = days / 30.4375
        return f"{months:.1f}か月" if months < 10 else f"{round(months)}か月"
    years = days / 365.25
    return f"{years:.1f}年" if years < 10 else f"{round(years)}年"


def get_recent_revlog_entries(card_id: int, count: int) -> list[tuple[int, int, int]]:
    if not mw.col:
        return []
    return mw.col.db.all(
        "select ease, ivl, id from revlog where cid = ? order by id desc limit ?",
        card_id,
        count,
    )


def build_history_html(card_id: int) -> str:
    config = get_config()
    entries = list(reversed(get_recent_revlog_entries(card_id, config["history_count"])))
    if not entries:
        return ""

    colors = config["colors"]
    boxes: list[str] = []
    for ease, ivl, revlog_id in entries:
        label = EASE_LABELS.get(ease, f"Ease {ease}")
        color_key = COLOR_KEYS.get(ease, "manual")
        bg = colors.get(color_key, "#ffffff")
        interval_text = format_interval(ivl)
        title = ""
        if config["show_tooltip"]:
            # revlog.id is the review timestamp in ms.
            from datetime import datetime
            dt = datetime.fromtimestamp(revlog_id / 1000)
            title = f' title="{html.escape(dt.strftime("%Y-%m-%d %H:%M"))} / {html.escape(label)} / {html.escape(interval_text)}"'
        boxes.append(
            f'<div class="rih-box"{title}>'
            f'{html.escape(interval_text)}'
            f'</div>'
            .replace('class="rih-box"', f'class="rih-box {html.escape(color_key)}"')
        )

    style = f"""
    <style>
      .rih-wrap {{
        display: flex;
        flex-wrap: wrap;
        gap: {config['gap']}px;
        align-items: center;
        justify-content: center;
        margin: 0 0 {config['margin_bottom']}px 0;
      }}
      .rih-box {{
        min-width: {config['box_min_width']}px;
        height: {config['box_height']}px;
        line-height: {config['box_height']}px;
        padding: 0 8px;
        text-align: center;
        border: 1px solid {html.escape(colors.get('border', '#cfcfcf'))};
        border-radius: 7px;
        color: {html.escape(colors.get('text', '#222222'))};
        font-size: {config['font_size']}px;
        font-weight: 600;
        box-sizing: border-box;
      }}
      .rih-box.again {{ background: {html.escape(colors.get('again', '#ffb3b3'))}; }}
      .rih-box.hard {{ background: {html.escape(colors.get('hard', '#ffd9a8'))}; }}
      .rih-box.good {{ background: {html.escape(colors.get('good', '#b9f6ca'))}; }}
      .rih-box.easy {{ background: {html.escape(colors.get('easy', '#b3e5fc'))}; }}
      .rih-box.manual {{ background: {html.escape(colors.get('manual', '#ffffff'))}; }}
    </style>
    """
    return style + '<div class="rih-wrap">' + ''.join(boxes) + '</div>'


def inject_history(html_text: str, card, context: str) -> str:
    if context not in ("reviewQuestion", "reviewAnswer"):
        return html_text
    try:
        history_html = build_history_html(card.id)
        if not history_html:
            return html_text
        return history_html + html_text
    except Exception as exc:
        print(f"[review_interval_history] failed to render history: {exc}")
        return html_text


gui_hooks.card_will_show.append(inject_history)
