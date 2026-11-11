"""选英雄界面（大乱斗）的头像位置与读取。只读屏幕像素，不接入客户端。

客户端界面按窗口等比缩放，所以头像位置用窗口比例保存，1080p / 2K / 4K 通用。
- bench：顶部"可选英雄"预选栏（最多 10 个）；
- cards：开局"选择你的英雄"阶段中间的 2–3 张英雄卡，读卡片下方的英雄称号；
- team：左侧我方 5 个召唤师。队友头像显示的是所选皮肤，头像比对可能认不出，
  所以优先用每行的英雄称号文字（如"卡牌大师"）识别，头像比对兜底；自己那一行称号是金色字。
"""

from __future__ import annotations

import logging
import time

import cv2
import numpy as np
from rapidfuzz import fuzz

from .matcher import normalize
from .portraits import IconBank

log = logging.getLogger(__name__)

# 以 1920×1080 客户区为基准量取（国服 2026-09 客户端实机截图），存成比例。
REF_W, REF_H = 1920, 1080


def _r(x0, y0, x1, y1):
    return (x0 / REF_W, y0 / REF_H, x1 / REF_W, y1 / REF_H)


# (x0, y0, x1, y1)，相对客户端窗口客户区。
LAYOUT: dict[str, list[tuple[float, float, float, float]]] = {
    # 顶部"可用"预选栏：10 个方形头像，边长 72，间距 88
    "bench": [_r(528 + 88 * i, 16, 600 + 88 * i, 88) for i in range(10)],
    # 左侧我方 5 人的圆形头像，直径约 86，行距 120
    "team": [_r(85, 160 + 120 * i, 171, 246 + 120 * i) for i in range(5)],
}
# 预选栏左边的"可用"两个字：只有选英雄界面有，用来确认当前界面，避免在大厅等界面误标
MARKER_BOX = _r(420, 28, 500, 64)
MARKER_TEXT = "可用"
# 外服客户端这两处字更长（如「Available Champions」「使用するチャンピオンをピックしてください」），框往外放宽
MARKER_BOX_WIDE = _r(140, 24, 515, 68)
# 开局"选择你的英雄"：标题、卡片称号所在的横条、卡片上下边和半宽
CARD_TITLE_BOX = _r(780, 155, 1140, 220)
CARD_TITLE_TEXT = "选择你的英雄"
CARD_TITLE_BOX_WIDE = _r(520, 150, 1400, 225)
CARD_NAME_BAND = _r(430, 600, 1490, 690)
CARD_TOP, CARD_BOTTOM, CARD_HALF_W = 270 / REF_H, 718 / REF_H, 138 / REF_W
# 我方每行的英雄名文字区域：自己那一行是金色字
NAME_BOXES = [_r(188, 176 + 120 * i, 330, 206 + 120 * i) for i in range(5)]


class CardOpening:
    """倒计时初见标题后短时勤取新帧；未读出任何卡时，多给淡入动画几次机会。"""
    BURST_S = 1.4
    FIRST_READ_BURST_S = 4.5
    TITLE_GAP_S = 1.25
    POLL_S = 0.15
    FALLBACK_COOLDOWN_S = 2.0

    def __init__(self):
        self.started = None
        self.missing_since = None
        self.next_full = 0.0
        self.visible = self.used_full = self.complete = False
        self.last_ids = ()
        self.seen_slots = 2
        self.reported_first = False
        self.pair = None
        self.pair_since = 0.0

    def begin_frame(self):
        self.visible = self.used_full = False

    def saw_title(self):
        now = time.monotonic()
        if self.missing_since is not None and now - self.missing_since > self.TITLE_GAP_S:
            # missed_title 已清掉上一组名字；现在也结束上次的首批计时与卡数判断。
            self.started = None
            self.reported_first = False
            self.seen_slots = 2
        self.missing_since = None
        self.visible = True
        if self.started is None:
            self.started = now
            self.next_full = self.started + self.BURST_S

    def fast_retry(self):
        now = time.monotonic()
        recent_title = self.visible or (self.missing_since is not None and now - self.missing_since <= self.TITLE_GAP_S)
        budget = self.BURST_S if self.last_ids else self.FIRST_READ_BURST_S
        return (recent_title and not self.complete and self.started is not None and now < self.started + budget)

    def observe(self, cards, expected):
        ids = tuple(c["hero_id"] for c in cards)
        self.last_ids = ids
        now = time.monotonic()
        # 三个称号位置已经出现过时，不能因为某栏一帧变暗又把它判断成二选一。
        self.seen_slots = max(self.seen_slots, min(3, expected), min(3, len(ids)))
        enough = len(ids) >= max(expected, self.seen_slots) and len(set(ids)) == len(ids)
        # 有两个名字时，第三张可能还在淡入。两张的胜率照常先出，只延后金框。
        pair = ids if enough and len(ids) == 2 else None
        if pair != self.pair:
            self.pair, self.pair_since = pair, now
        self.complete = (enough and len(ids) == 3) or (
            pair is not None and now - self.pair_since >= 0.4
            and self.started is not None and now >= self.started + self.BURST_S)

    def published(self, cards):
        if not cards or self.reported_first or self.started is None:
            return
        self.reported_first = True
        log.info("选人首抽：初见标题后 %.0f 毫秒发布首批 %d 张，有胜率 %d 张",
                 (time.monotonic() - self.started) * 1000, len(cards),
                 sum(card.get("win") is not None for card in cards))


class Memo:
    """按小块画面缓存识别结果：和上次识别时的画面比，没变就直接用上次的结果。
    选人界面大部分时间是静止的，这样可以扫得很勤，而几乎不多占 CPU。

    keep(结果) 为假的结果（没认出来）不复用：卡片和文字是淡入的，淡入到六七成时
    缩略图已经和最终画面差不多，却还认不出字；缓存下来就会一直"认不出"，直到画面大变。

    结果要等连续两次画面一样才复用：换英雄时名字淡出淡入，半透明那一帧可能还读成旧英雄，
    缓存下来以后最终画面和它差得不多，就会一直显示旧英雄。

    缩略图按原比例缩到 ROWS 行高，比较逐像素差的 99 分位：名字是细长横条，换一个字只改动
    一小块；以前压成 12×12 再比平均差，同字数的两个名字（如寒冰射手→暗夜猎手）会被当成没变。"""
    ROWS = 24    # 缩略图行高；宽按原比例，最多 8 倍
    TOL = 12.0   # 逐像素差 99 分位在这以内算没变（换一个字也有 90 以上，截图噪声约 1）
    LIMIT = 200  # 一种尺寸约 20 个位置；超过就是窗口大小换过很多次

    def __init__(self):
        self._d: dict = {}
        self.seen = {}  # 诊断日志用：同样的情况只记一次
        self.opening = CardOpening()

    def _sig(self, crop):
        h, w = crop.shape[:2]
        cols = max(1, min(self.ROWS * 8, round(self.ROWS * w / max(1, h))))
        return cv2.resize(crop, (cols, self.ROWS), interpolation=cv2.INTER_AREA).astype(np.float32)

    def __call__(self, key, crop, fn, keep=None):
        if crop is None or crop.size == 0:
            return fn(crop)
        sig = self._sig(crop)
        key = (key, crop.shape)
        hit = self._d.get(key)
        same = hit is not None and float(np.percentile(np.abs(hit[0] - sig), 99)) < self.TOL
        if same and hit[2] and (keep is None or keep(hit[1])):
            return hit[1]
        val = fn(crop)
        if len(self._d) >= self.LIMIT and key not in self._d:
            self._d.clear()  # 选人中途反复拖窗口大小：每个尺寸一套缓存，到上限就从头存
        self._d[key] = (sig, val, same)  # 和上次画面一样才算静止，下次可以直接复用
        return val

    def leave_cards(self):
        if self.opening.started is not None:
            self.opening = CardOpening()
            self.forget_card_readings()

    def forget_card_readings(self):
        for key in list(self._d):
            if key[0] in ("card_title", "card_band"):
                del self._d[key]
        self.opening.last_ids = ()
        self.opening.pair = None
        self.opening.complete = False

    def missed_title(self):
        opening = self.opening
        if opening.started is None:
            return
        now = time.monotonic()
        if opening.missing_since is None:
            opening.missing_since = now
            self.forget_card_readings()  # 只留起始时间；不把旧名字带到下一张图
        elif now - opening.missing_since > opening.TITLE_GAP_S:
            self.leave_cards()

    def same_card_view(self, old, new):
        if old.shape != new.shape:
            return False
        for box in (CARD_TITLE_BOX_WIDE, CARD_NAME_BAND):
            a, b = self._sig(_crop(old, box)), self._sig(_crop(new, box))
            if float(np.percentile(np.abs(a - b), 99)) >= self.TOL:
                return False
        return True


def _plain(key, crop, fn, keep=None):
    return fn(crop)


def boxes(kind: str, width: int, height: int) -> list[tuple[int, int, int, int]]:
    rects = NAME_BOXES if kind == "team_name" else LAYOUT.get(kind, [])
    return [(int(x0 * width), int(y0 * height), int(x1 * width), int(y1 * height))
            for x0, y0, x1, y1 in rects]


def is_champ_select(img, recognize, memo=None, marker: str = MARKER_TEXT) -> bool:
    """recognize(小块图) → (文字, 置信度)。读到"可用"（外服是那种语言的字）才算在选英雄界面。"""
    box = MARKER_BOX if marker == MARKER_TEXT else MARKER_BOX_WIDE
    txt, sc = (memo or _plain)("marker", _crop(img, box), recognize, _has_text(marker))
    return _has_text(marker)((txt, sc))


def _has_text(want):
    """读到的字里有 want。长的字（外服）允许认错一两个字母、只读到一部分。"""
    key = normalize(want)

    def ok(r):
        got = normalize(r[0] or "")
        if r[1] < 0.6 or not got:
            return False
        if key in got:
            return True
        return len(key) >= 6 and len(got) >= 4 and fuzz.partial_ratio(key, got) >= 85
    return ok


def _crop(img, box):
    h, w = img.shape[:2]
    x0, y0, x1, y1 = box
    return img[int(y0 * h):int(y1 * h), int(x0 * w):int(x1 * w)]


def text_spans(band) -> list[tuple[int, int]]:
    """卡片称号横条 → 每段浅色文字的 (x0, x1)。称号是米白字、卡底是深紫色；
    卡片边框是细竖线，按宽度过滤掉。"""
    if band is None or band.size == 0:
        return []
    h, w = band.shape[:2]
    light = band.min(axis=2) > 150  # 米白字三个通道都亮；金色花纹蓝通道暗
    cols = light.sum(axis=0) >= max(2, h // 30)
    gap, min_w, pad = max(4, w // 50), max(12, w // 25), max(4, w // 100)
    spans, start, last = [], None, None
    for x, on in enumerate(cols):
        if on:
            if start is None:
                start = x
            elif x - last > gap:
                spans.append((start, last))
                start = x
            last = x
    if start is not None:
        spans.append((start, last))
    return [(max(0, a - pad), min(w, b + 1 + pad)) for a, b in spans if b - a >= min_w]


def caption_detail(band, weak=False) -> bool:
    """有邻近的文字笔画才值得做慢检测。渐变、长条边框也有很大明暗差。"""
    if band is None or not band.size:
        return False
    h, w = band.shape[:2]
    height = min(48, h)
    width = max(2, round(w * height / h))
    gray = cv2.resize(band[:, :, 1], (width, height), interpolation=cv2.INTER_AREA)
    # 逐行数亮笔画的左缘，同一段逐渐变亮只算一次。排除贯穿大半条带的竖框。
    rising = np.diff(gray.astype(np.int16), axis=1) >= (2 if weak else 4)
    starts = rising.copy()
    starts[:, 1:] &= ~rising[:, :-1]
    mask = starts.astype(np.uint8)
    vertical_stroke = np.ones((max(3, (height // 3) * 2 + 1), 1), np.uint8)
    vertical = cv2.morphologyEx(mask, cv2.MORPH_OPEN, vertical_stroke)
    mask &= ~vertical
    span = min(mask.shape[1], height * 2)
    sums = np.pad(np.cumsum(mask, axis=1, dtype=np.int32), ((0, 0), (1, 0)))
    busy = (sums[:, span:] - sums[:, :-span]) >= (2 if weak else 4)
    return bool(np.any(busy.sum(axis=0) >= max(2, height // 12)))


def read_cards(img, recognize, detect, name_to_hero, origin: tuple[int, int] = (0, 0),
               memo=None, title_text: str = CARD_TITLE_TEXT, opening: CardOpening | None = None,
               allow_full: bool = True) -> list[dict]:
    """开局英雄卡（二选一/三选一）。不在这个阶段时返回 []。
    detect(图) → 文字行列表（带 box）；name_to_hero(文字) → 英雄 id 或 None。"""
    memo = memo or _plain
    box = CARD_TITLE_BOX if title_text == CARD_TITLE_TEXT else CARD_TITLE_BOX_WIDE
    title = memo("card_title", _crop(img, box), recognize, _has_text(title_text))
    seen = getattr(memo, "seen", {})
    if not _has_text(title_text)(title):
        seen.pop("title", None)
        seen.pop("cards", None)
        return []
    if not seen.get("title"):
        seen["title"] = True
        log.info("选英雄：看到「%s」", title_text)
    if opening is not None:
        opening.saw_title()
    h, w = img.shape[:2]
    bx0 = int(CARD_NAME_BAND[0] * w)

    def lines(band):
        # 先切出浅色称号逐段认字。实时首抽先给动画几次新帧；高成本的整条检测
        # 只在有画面内容、已有的部分结果已经返给 UI 后，作为限频兜底。
        out = []
        for x0, x1 in text_spans(band):
            t, c = recognize(band[:, x0:x1])
            out.append((t, c, x0, x1))
        expected = max(opening.seen_slots if opening is not None else 2, len(out))
        quick_ids = valid_ids(out)
        hits = len(quick_ids)
        if allow_full and hits < expected and (opening is None or (
                time.monotonic() >= opening.next_full
                and (not quick_ids or quick_ids == opening.last_ids)
                and (quick_ids or caption_detail(
                    band, weak=time.monotonic() - opening.started >= opening.FIRST_READ_BURST_S)))):
            if opening is not None:
                opening.used_full = True
            try:
                full = [(ln.text, ln.score, ln.box[0], ln.box[2]) for ln in detect(band)]
            finally:
                if opening is not None:
                    opening.next_full = time.monotonic() + opening.FALLBACK_COOLDOWN_S
            if len(valid_ids(full)) >= hits:
                out = full
        return out, expected

    def valid_ids(rows):
        return tuple(hid for t, sc, *_ in rows if sc >= 0.6 and (hid := name_to_hero(t)))

    cards = []
    found, expected = memo("card_band", _crop(img, CARD_NAME_BAND), lines,
                           lambda result: len(valid_ids(result[0])) >= max(
                               result[1], opening.seen_slots if opening is not None else 2))
    for text, score, x0, x1 in found:
        hid = name_to_hero(text) if score >= 0.6 else None
        if not hid:
            continue
        cx = bx0 + (x0 + x1) / 2
        half = CARD_HALF_W * w
        cards.append({"slot": len(cards), "hero_id": hid, "score": round(float(score), 3),
                      "box": (int(cx - half) + origin[0], int(CARD_TOP * h) + origin[1],
                              int(cx + half) + origin[0], int(CARD_BOTTOM * h) + origin[1])})
    cards.sort(key=lambda c: c["box"][0])
    for i, c in enumerate(cards):
        c["slot"] = i
    if opening is not None:
        opening.observe(cards, expected)
    got = tuple(c["hero_id"] for c in cards)
    if seen.get("cards") != got:
        seen["cards"] = got
        if len(cards) >= 2:
            log.info("选英雄：认出 %d 张英雄卡 %s", len(cards), list(got))
        else:
            log.info("选英雄：标题在，英雄卡没认全，文字识别读到 %s", [(t, round(float(sc), 2)) for t, sc, *_ in found])
    return cards


def has_name(crop) -> bool:
    """我方这一行名字的位置有没有字：没人的空行（自定义只有自己时）是暗的背景，
    头像比对可能把空位的背景认成某个英雄（实测弗拉基米尔 0.76），所以没字就不比头像。"""
    return crop.size > 0 and float((crop.max(axis=2) >= 160).mean()) >= 0.02


def my_slot(img) -> int | None:
    """我方哪一行是自己：英雄名是金色字（其他人是白字）。"""
    h, w = img.shape[:2]
    best, idx = 0.0, None
    for i, (x0, y0, x1, y1) in enumerate(boxes("team_name", w, h)):
        c = img[y0:y1, x0:x1].astype(int)
        if c.size == 0:
            continue
        b, g, r = c[..., 0], c[..., 1], c[..., 2]
        gold = ((r > 190) & (g > 140) & (b < 120) & (r - b > 110)).mean()
        if gold > best:
            best, idx = float(gold), i
    return idx if best >= 0.02 else None


def read(img, bank: IconBank, origin: tuple[int, int] = (0, 0), name_reader=None, memo=None) -> dict:
    """截图（客户端客户区）→ {"bench": [...], "team": [...], "mine": 自己的英雄 id 或 None}。
    每项 {"slot", "hero_id", "score", "box"}，box 为屏幕物理像素坐标；认不出的位置不返回。
    name_reader(小块图) → 英雄 id 或 None：用来读我方每行的英雄称号。
    memo：Memo 实例时，画面没变的位置直接用上次的结果。"""
    memo = memo or _plain
    h, w = img.shape[:2]
    ox, oy = origin
    out: dict = {}
    names = boxes("team_name", w, h)
    for kind in LAYOUT:
        found = []
        for i, (x0, y0, x1, y1) in enumerate(boxes(kind, w, h)):
            hid, score = None, 0.0
            if kind == "team":
                nx0, ny0, nx1, ny1 = names[i]
                if not has_name(img[ny0:ny1, nx0:nx1]):
                    continue  # 空行
                if name_reader is not None:
                    hid = memo(("name", i), img[ny0:ny1, nx0:nx1], name_reader, bool)
                    score = 1.0 if hid else 0.0
            if hid is None:
                m = memo((kind, i), img[y0:y1, x0:x1], bank.classify)
                hid, score = m.hero_id, m.score
            if hid:
                found.append({"slot": i, "hero_id": hid, "score": round(score, 3),
                              "box": (x0 + ox, y0 + oy, x1 + ox, y1 + oy)})
        out[kind] = found
    me = my_slot(img)
    out["mine"] = next((s["hero_id"] for s in out["team"] if s["slot"] == me), None)
    return out
