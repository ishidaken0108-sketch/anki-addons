from __future__ import annotations

import math
from typing import Any

FLAG_NAMES: dict[int, str] = {
    0: "フラグなし",
    1: "赤",
    2: "オレンジ",
    3: "緑",
    4: "青",
    5: "ピンク",
    6: "水色",
    7: "紫",
}

PAUSE_FLAG = 7

# 過去バージョンの初期値。ユーザーが初期値のまま使用している場合だけ、
# 段階的に新しい初期値へ自動移行します。手動変更済みの値は維持します。
V5_DEFAULT_FLAG_SECONDS: dict[str, int] = {
    "0": 10,
    "1": 60,
    "2": 40,
    "3": 30,
    "4": 20,
    "5": 15,
    "6": 10,
    "7": 5,
}

V6_DEFAULT_FLAG_SECONDS: dict[str, int] = {
    "0": 10,
    "1": 120,
    "2": 60,
    "3": 50,
    "4": 40,
    "5": 30,
    "6": 20,
    "7": 10,
}

DEFAULT_FLAG_SECONDS: dict[str, int] = {
    "0": 10,
    "1": 120,
    "2": 90,
    "3": 60,
    "4": 40,
    "5": 30,
    "6": 20,
    "7": 0,
}

CONFIG_VERSION = 4

DEFAULT_CONFIG: dict[str, Any] = {
    "config_version": CONFIG_VERSION,
    "enabled": True,
    "max_timer_seconds": 180,
    "initial_bonus_multiplier": 1.5,
    "wrong_answer_seconds": 20,
    "enforce_mute_every_seconds": 3.0,
    "flag_seconds": dict(DEFAULT_FLAG_SECONDS),
}


def config_with_migration(saved: Any) -> tuple[dict[str, Any], bool]:
    """Return normalized config and whether it should be written back.

    Untouched defaults are migrated in stages. Customized flag values are
    deliberately preserved.
    """
    changed = False
    if isinstance(saved, dict):
        source: dict[str, Any] = dict(saved)
    else:
        source = {}
        changed = True

    version = _bounded_int(source.get("config_version"), 0, 0, 9999)

    if version < 2:
        saved_flags = source.get("flag_seconds")
        if _flag_map_matches(saved_flags, V5_DEFAULT_FLAG_SECONDS):
            source["flag_seconds"] = dict(V6_DEFAULT_FLAG_SECONDS)
        if "wrong_answer_seconds" not in source:
            source["wrong_answer_seconds"] = DEFAULT_CONFIG["wrong_answer_seconds"]
        version = 2
        source["config_version"] = version
        changed = True

    if version < CONFIG_VERSION:
        saved_flags = source.get("flag_seconds")
        if version < 3 and _flag_map_matches(saved_flags, V6_DEFAULT_FLAG_SECONDS):
            source["flag_seconds"] = dict(DEFAULT_FLAG_SECONDS)
        elif isinstance(saved_flags, dict):
            migrated_flags = dict(saved_flags)
            migrated_flags[str(PAUSE_FLAG)] = 0
            source["flag_seconds"] = migrated_flags
        else:
            source["flag_seconds"] = dict(DEFAULT_FLAG_SECONDS)
        source["config_version"] = CONFIG_VERSION
        changed = True

    normalized = normalized_config(source)
    if normalized != source:
        changed = True
    return normalized, changed


def normalized_config(saved: Any) -> dict[str, Any]:
    config: dict[str, Any] = {
        "config_version": CONFIG_VERSION,
        "enabled": DEFAULT_CONFIG["enabled"],
        "max_timer_seconds": DEFAULT_CONFIG["max_timer_seconds"],
        "initial_bonus_multiplier": DEFAULT_CONFIG["initial_bonus_multiplier"],
        "wrong_answer_seconds": DEFAULT_CONFIG["wrong_answer_seconds"],
        "enforce_mute_every_seconds": DEFAULT_CONFIG["enforce_mute_every_seconds"],
        "flag_seconds": dict(DEFAULT_FLAG_SECONDS),
    }
    if not isinstance(saved, dict):
        return config

    config["enabled"] = bool(saved.get("enabled", config["enabled"]))
    config["max_timer_seconds"] = _bounded_int(
        saved.get("max_timer_seconds"), int(config["max_timer_seconds"]), 0, 36000
    )
    config["initial_bonus_multiplier"] = _bounded_float(
        saved.get("initial_bonus_multiplier"),
        float(config["initial_bonus_multiplier"]),
        0.0,
        10.0,
    )
    config["wrong_answer_seconds"] = _bounded_int(
        saved.get("wrong_answer_seconds"),
        int(config["wrong_answer_seconds"]),
        0,
        36000,
    )
    config["enforce_mute_every_seconds"] = _bounded_float(
        saved.get("enforce_mute_every_seconds"),
        float(config["enforce_mute_every_seconds"]),
        0.5,
        60.0,
    )

    saved_flags = saved.get("flag_seconds")
    if isinstance(saved_flags, dict):
        for flag in range(8):
            key = str(flag)
            config["flag_seconds"][key] = _bounded_int(
                saved_flags.get(key), DEFAULT_FLAG_SECONDS[key], 0, 36000
            )
    config["flag_seconds"][str(PAUSE_FLAG)] = 0
    return config


def seconds_for_flag(config: dict[str, Any], flag: Any) -> int:
    try:
        flag_number = int(flag)
    except (TypeError, ValueError):
        flag_number = 0
    if flag_number not in FLAG_NAMES:
        flag_number = 0
    if flag_number == PAUSE_FLAG:
        return 0
    flags = config.get("flag_seconds", {})
    fallback = DEFAULT_FLAG_SECONDS[str(flag_number)]
    value = flags.get(str(flag_number), fallback) if isinstance(flags, dict) else fallback
    return _bounded_int(value, fallback, 0, 36000)


def is_pause_flag(flag: Any) -> bool:
    try:
        return int(flag) == PAUSE_FLAG
    except (TypeError, ValueError):
        return False


def auto_flag_for_elapsed(config: dict[str, Any], elapsed_seconds: float) -> int | None:
    """Choose a flag for an unflagged card from its answer duration.

    Purple is excluded because it is the pause/exempt flag. The smallest
    configured flag duration strictly greater than the elapsed duration is
    selected. If the duration exceeds every configured threshold, the flag
    with the largest threshold is selected.
    """
    try:
        elapsed = max(0.0, float(elapsed_seconds))
    except (TypeError, ValueError):
        elapsed = 0.0

    candidates: list[tuple[int, int]] = []
    for flag in range(1, PAUSE_FLAG):
        seconds = seconds_for_flag(config, flag)
        if seconds > 0:
            candidates.append((seconds, flag))
    if not candidates:
        return None

    candidates.sort(key=lambda item: (item[0], item[1]))
    for seconds, flag in candidates:
        if float(seconds) > elapsed:
            return flag
    return max(candidates, key=lambda item: (item[0], -item[1]))[1]


def wrong_answer_seconds(config: dict[str, Any], ease: Any) -> int:
    """Again（評価1）のときだけ誤答加算を返す。"""
    try:
        ease_number = int(ease)
    except (TypeError, ValueError):
        return 0
    if ease_number != 1:
        return 0
    return _bounded_int(
        config.get("wrong_answer_seconds"),
        int(DEFAULT_CONFIG["wrong_answer_seconds"]),
        0,
        36000,
    )


def initial_bonus_seconds(base_seconds: int, multiplier: float) -> int:
    """Round upward so a fractional bonus never disadvantages the user."""
    return max(0, int(math.ceil(max(0, base_seconds) * max(0.0, multiplier))))


def add_capped(current: float, requested: float, maximum: int) -> tuple[float, float]:
    current = max(0.0, float(current))
    requested = max(0.0, float(requested))
    after = current + requested
    if maximum > 0:
        after = min(after, float(maximum))
    actual = max(0.0, after - current)
    return after, actual


def subtract_award(current: float, actual_award: float) -> float:
    return max(0.0, float(current) - max(0.0, float(actual_award)))


def display_seconds(remaining: float) -> int:
    if remaining <= 0:
        return 0
    return int(math.ceil(remaining - 1e-9))


def format_remaining(remaining: float) -> str:
    total = display_seconds(remaining)
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def _flag_map_matches(value: Any, expected: dict[str, int]) -> bool:
    if not isinstance(value, dict):
        return False
    for key, expected_value in expected.items():
        try:
            actual = int(value.get(key))
        except (TypeError, ValueError):
            return False
        if actual != expected_value:
            return False
    return True


def _bounded_int(value: Any, fallback: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = fallback
    return min(maximum, max(minimum, number))


def _bounded_float(value: Any, fallback: float, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = fallback
    return min(maximum, max(minimum, number))
