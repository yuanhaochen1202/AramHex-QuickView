"""加载画面中，按本局视觉核对的位置显示英雄统计胜率。"""
from __future__ import annotations

import copy
import math

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen

from ..vision import window as win
from .overlay import _Layer, win_color


class LoadingLayer(_Layer):
    """只在已核对的卡片名称上方画胜率条；不预设双方上下位置。"""
    name = "加载英雄胜率"

    def __init__(self):
        super().__init__()
        self.snapshot = {}
        self._signature = None
        self._badges = []
        self._unit = 1.0

    @staticmethod
    def rows(roster):
        """缺失的槽位保持空白状态，不复用前一帧或上一局的英雄。"""
        teams = (roster or {}).get("teams") or []
        if len(teams) != 2:
            return []
        out = []
        for team in teams:
            slots = {}
            for member in team.get("members") or []:
                slot = member.get("slot")
                if type(slot) is int and 0 <= slot < 5 and slot not in slots:
                    slots[slot] = member
            out.append((str(team.get("label") or "队伍"), [slots.get(i) for i in range(5)]))
        return out

    @staticmethod
    def member_text(member):
        if not member or not member.get("hero_id"):
            return "—", "", QColor(143, 152, 169)
        name = member.get("name") or member.get("nick") or "未知英雄"
        if member.get("is_local"):
            name += " · 我"
        rate = member.get("win")
        if type(rate) not in (int, float) or not math.isfinite(rate) or not 0 <= rate <= 1:
            return name, "暂无数据", QColor(143, 152, 169)
        return name, f"{rate * 100:.1f}%", win_color(rate)

    @staticmethod
    def badges(snap, rect):
        roster = snap.get("loading_roster") or {}
        positions = snap.get("loading_cards") or {}
        if (rect is None or not roster.get("game_key")
                or positions.get("game_key") != roster.get("game_key")
                or positions.get("rect") != [rect.left, rect.top, rect.width, rect.height]):
            return []
        members = {str(m["hero_id"]): m for _, row in LoadingLayer.rows(roster)
                   for m in row if m and m.get("hero_id")}
        out = []
        for card in positions.get("cards") or []:
            member = members.get(str(card.get("hero_id")))
            box = card.get("box")
            if not member or not isinstance(box, (list, tuple)) or len(box) != 4:
                continue
            if any(type(v) not in (int, float) or not math.isfinite(v) for v in box):
                continue
            x0, y0, x1, y1 = map(round, box)
            if not (0 <= x0 < x1 <= rect.width and 0 <= y0 < y1 <= rect.height):
                continue
            out.append((member, win.Rect(rect.left + x0, rect.top + y0, x1-x0, y1-y0)))
        return out

    def update_from(self, snap, rect, hwnd=0):
        badges = self.badges(snap, rect)
        if not badges:
            self.snapshot, self._badges, self._signature = {}, [], None
            self.hide()
            return
        self.snapshot, self._badges = snap, badges
        self._unit = min(rect.width / 1920, rect.height / 1080)
        # 只覆盖已显示标签的包围区域，透明部分不画底色、不截获鼠标。
        left = min(b.left for _, b in badges) - 2
        top = min(b.top for _, b in badges) - 2
        right = max(b.right for _, b in badges) + 2
        bottom = max(b.bottom for _, b in badges) + 2
        target = win.Rect(left, top, right-left, bottom-top)
        self._place(target, hwnd, target_h=rect.height)
        signature = (badges, self.origin.x(), self.origin.y(), self._unit, self._scale(), self.size())
        if signature != self._signature:
            self._signature = copy.deepcopy(signature)
            self.update()

    def paintEvent(self, event):
        if not self._badges:
            self._publish([], 1)
            return
        unit = self._unit
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.scale(unit / self._scale(), unit / self._scale())
        drawn = []
        for member, box in self._badges:
            r = QRect(round((box.left-self.origin.x()) / unit),
                      round((box.top-self.origin.y()) / unit),
                      round(box.width / unit), round(box.height / unit))
            _, rate, color = self.member_text(member)
            border = QColor(color)
            border.setAlpha(165)
            p.setPen(QPen(border, 1))
            p.setBrush(QColor(13, 19, 29, 220))
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 6, 6)
            rate_font = QFont("Microsoft YaHei UI")
            rate_font.setPixelSize(24 if rate != "暂无数据" else 16)
            rate_font.setBold(True)
            p.setFont(rate_font)
            p.setPen(color)
            p.drawText(r.adjusted(5, 0, -5, 0), Qt.AlignCenter, rate)
            drawn.append(r)
        p.end()
        self._publish(drawn, unit)
