"""数据来源一行字（主窗口和浮窗共用），文字都已转义。"""
from datetime import datetime, timedelta, timezone
from html import escape


def _bjt(value, fmt="%m-%d"):
    try:
        stamp = datetime.fromisoformat(str(value))
        if stamp.tzinfo is not None:
            return stamp.astimezone(timezone(timedelta(hours=8))).strftime(fmt)
    except (TypeError, ValueError):
        pass
    return ""


def data_status_html(d):
    if not d.get("dataset"):
        parts = ["没有统计数据"]
    else:
        parts = ([d["dataset_mode_label"]] if d.get("dataset_mode_label") else []) + [f"{d.get('dataset_patch')} 版本"]
        matches = d.get("dataset_matches")
        if isinstance(matches, int):
            parts.append(f"{matches / 10000:.0f} 万局")
        day = _bjt(d.get("dataset_snapshot"))
        if day:
            parts.append(f"截至 {day}")
        if d.get("patch") and d.get("dataset_patch") and d["patch"] != d["dataset_patch"]:
            parts.append(f"游戏已更新到 {d['patch']}，统计数据还没更新")
    if d.get("event_fallback") and d.get("event_label"):
        parts.append(f"{d['event_label']}的数据还没出，先用常规")
    if d.get("error"):
        parts.append("官方资料获取失败")
    return escape(" · ".join(parts))
