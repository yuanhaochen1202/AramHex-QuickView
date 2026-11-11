"""Synthetic title-crop evidence; no client access or saved game screenshots."""
import copy

import numpy as np
import pytest

from lolhex.vision.loadingcards import LoadingCardLocator, badge_box, match_name, name_boxes


NAMES = ("盖伦", "赵信", "卡特琳娜", "提莫", "艾希",
         "瑞兹", "薇恩", "李青", "杰斯", "奎桑提")


def roster():
    return {"game_key": "current-game", "complete": True,
            "teams": [{"side": side, "members": [
                {"hero_id": str(team * 5 + col), "slot": col, "name": NAMES[team * 5 + col],
                 "nick": "不祥之刃" if team * 5 + col == 2 else "", "is_local": False}
                for col in range(5)]} for team, side in enumerate((100, 200))]}


def image(width=1920, height=1080):
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    for index, (x0, y0, x1, y1) in enumerate(name_boxes(width, height)):
        frame[y0:y1, x0:x1] = index + 1
    return frame


class Recognize:
    def __init__(self, names=None, score=.99):
        self.names = list(NAMES if names is None else names)
        self.calls = []
        self.score = score

    def __call__(self, crop):
        index = int(crop[0, 0, 0]) - 1
        self.calls.append(index)
        return self.names[index], self.score


def by_box(cards):
    return {tuple(c["box"]): c["hero_id"] for c in cards}


@pytest.mark.parametrize("width,height", [(1280, 720), (1920, 1080), (2560, 1440),
                                         (3840, 2160), (3440, 1440), (1920, 1200)])
def test_boxes_fit_contained_viewport_and_badge_is_three_quarters(width, height):
    scale = min(width / 1920, height / 1080)
    boxes = name_boxes(width, height)
    assert len(boxes) == 10
    for index, name in enumerate(boxes):
        badge = badge_box(width, height, *divmod(index, 5))
        assert 0 <= name[0] < name[2] <= width and 0 <= name[1] < name[3] <= height
        assert 0 <= badge[0] < badge[2] <= width and 0 <= badge[1] < badge[3] <= height
        assert badge[3] <= name[1]
        assert (badge[2] - badge[0]) / (255 * scale) == pytest.approx(.75, abs=.006)
        assert abs((badge[0] + badge[2]) / 2 - (name[0] + name[2]) / 2) <= 1


@pytest.mark.parametrize("flipped", [False, True])
def test_screen_names_resolve_either_team_order_without_local_side(flipped):
    state, frame = roster(), image()
    reader = Recognize(NAMES[5:] + NAMES[:5] if flipped else NAMES)
    locator = LoadingCardLocator()
    for count in range(10):
        cards = locator.step(frame, state, reader)
        assert len(reader.calls) == count + 1
    assert len(cards) == 10
    found = by_box(cards)
    for row in range(2):
        for col in range(5):
            expected = (1 - row if flipped else row) * 5 + col
            assert found[badge_box(1920, 1080, row, col)] == str(expected)


def test_one_direct_card_is_visible_before_row_can_be_inferred():
    locator = LoadingCardLocator()
    cards = locator.step(image(), roster(), Recognize())
    assert cards == [{"hero_id": "0", "box": list(badge_box(1920, 1080, 0, 0))}]


def test_two_unique_anchors_can_fill_special_skin_name_missing_hero_text():
    locator, frame, state = LoadingCardLocator(), image(), roster()
    reader = Recognize(["神王 盖伦", "赵信", "闭月之颜 貂蝉", *NAMES[3:]])
    locator.step(frame, state, reader)
    cards = locator.step(frame, state, reader)
    assert len(cards) == 2  # Other three titles must be visited before filling a row.
    cards = locator.step(frame, state, reader)
    assert len(cards) == 2
    for _ in range(2):
        cards = locator.step(frame, state, reader)
    assert by_box(cards)[badge_box(1920, 1080, 0, 2)] == "2"
    assert len(cards) == 5  # Unknown skin is not a contradictory hero match.


def test_contradictory_column_blocks_whole_row_inference():
    locator, frame, state = LoadingCardLocator(), image(), roster()
    reader = Recognize([NAMES[0], NAMES[1], NAMES[7], "", "", *NAMES[5:]])
    for _ in range(5):
        cards = locator.step(frame, state, reader)
    assert len(cards) == 3
    assert badge_box(1920, 1080, 0, 4) not in by_box(cards)


def test_opposite_row_conflict_prevents_reusing_same_team_twice():
    locator, frame, state = LoadingCardLocator(), image(), roster()
    reader = Recognize([NAMES[0], NAMES[1], "", "", "", NAMES[0], "", "", "", ""])
    for _ in range(6):
        cards = locator.step(frame, state, reader)
    # Two direct top cards remain; a third bottom card cannot justify filling either row.
    assert len(cards) == 3
    assert badge_box(1920, 1080, 0, 2) not in by_box(cards)
    assert badge_box(1920, 1080, 1, 1) not in by_box(cards)


def test_partial_roster_never_fills_absent_heroes_or_compresses_columns():
    state = roster()
    state["complete"] = False
    state["teams"][0]["members"] = state["teams"][0]["members"][3:4]
    state["teams"][1]["members"] = []
    locator, frame, reader = LoadingCardLocator(), image(), Recognize()
    for _ in range(4):
        cards = locator.step(frame, state, reader)
    assert cards == [{"hero_id": "3", "box": list(badge_box(1920, 1080, 0, 3))}]


def test_duplicate_hero_does_not_provide_two_distinct_anchors():
    state = roster()
    duplicate = copy.deepcopy(state["teams"][0]["members"][0])
    duplicate["slot"] = 1
    state["teams"][0]["members"][1] = duplicate
    locator, frame, reader = LoadingCardLocator(), image(), Recognize([NAMES[0], NAMES[0], *([""] * 8)])
    for _ in range(5):
        cards = locator.step(frame, state, reader)
    assert len(cards) == 2


def test_identical_anchors_on_both_teams_do_not_choose_a_team():
    state = roster()
    for col in (0, 1):
        state["teams"][1]["members"][col] = copy.deepcopy(state["teams"][0]["members"][col])
    locator, frame, reader = LoadingCardLocator(), image(), Recognize([NAMES[0], NAMES[1], *([""] * 8)])
    for _ in range(5):
        cards = locator.step(frame, state, reader)
    assert len(cards) == 2


@pytest.mark.parametrize("score", [.84, 0, float("nan"), float("inf"), 1.5, True, "0.99"])
def test_low_or_invalid_confidence_is_rejected(score):
    assert match_name("盖伦", score, roster()) is None


def test_match_is_exact_or_unambiguous_containment_not_fuzzy():
    state = roster()
    assert match_name("神王 盖伦", .99, state) == "0"
    assert match_name("不祥之刃", .99, state) == "2"
    assert match_name("盖论", .99, state) is None
    assert match_name("盖伦 赵信", .99, state) is None
    state["teams"][0]["members"][0]["name"] = "蔚"
    assert match_name("蔚", .99, state) == "0"
    assert match_name("蔚蓝天空", .99, state) is None


def test_failed_card_does_not_block_others_and_successful_titles_are_cached():
    reader = Recognize(["", *NAMES[1:]])
    locator, frame, state = LoadingCardLocator(), image(), roster()
    for _ in range(10):
        locator.step(frame, state, reader)
    assert reader.calls == list(range(10))
    locator.step(frame, state, reader)
    assert reader.calls[-1] == 0
    assert len(reader.calls) == 11


def test_changed_title_clears_direct_and_row_inference_until_reconfirmed():
    frame, state, locator = image(), roster(), LoadingCardLocator()
    reader = Recognize([NAMES[0], NAMES[1], "", "", "", "", "", "", "", ""])
    for _ in range(5):
        cards = locator.step(frame, state, reader)
    assert len(cards) == 5
    x0, y0, x1, y1 = name_boxes(1920, 1080)[1]
    frame[y0:y1, x0:x1, 1] = 42
    cards = locator.step(frame, state, reader)
    assert len(cards) == 1 and cards[0]["hero_id"] == "0"
    for _ in range(5):
        cards = locator.step(frame, state, reader)
    assert len(cards) == 5


def test_animating_special_skin_pauses_inference_then_recovers_when_stable():
    frame, state, locator = image(), roster(), LoadingCardLocator()
    reader = Recognize([NAMES[0], NAMES[1], "闭月之颜 貂蝉", "", "", "", "", "", "", ""])
    for _ in range(5):
        cards = locator.step(frame, state, reader)
    assert len(cards) == 5
    x0, y0, x1, y1 = name_boxes(1920, 1080)[2]
    # Background/fade changes strongly, but the text still cannot identify the hero.
    for value in (40, 80, 120):
        frame[y0:y1, x0:x1, 1] = value
        cards = locator.step(frame, state, reader)
        assert len(cards) == 2
    # Stable special-skin text must not remain blocked forever by a previous change.
    for _ in range(10):
        cards = locator.step(frame, state, reader)
    assert len(cards) == 5
    assert by_box(cards)[badge_box(1920, 1080, 0, 2)] == "2"


def test_minor_screenshot_noise_does_not_clear_name_confirmation():
    frame, state, locator = image(), roster(), LoadingCardLocator()
    reader = Recognize([NAMES[0], "", "", "", "", "", "", "", "", ""])
    first = locator.step(frame, state, reader)
    x0, y0, x1, y1 = name_boxes(1920, 1080)[0]
    frame[y0:y1, x0:x1, 1] += 2
    assert locator.step(frame, state, reader) == first
    assert reader.calls == [0, 1]


def test_significant_single_name_stroke_change_clears_confirmation():
    frame, state, locator = image(), roster(), LoadingCardLocator()
    reader = Recognize([NAMES[0], "", "", "", "", "", "", "", "", ""])
    assert len(locator.step(frame, state, reader)) == 1
    x0, y0, x1, y1 = name_boxes(1920, 1080)[0]
    # A changed character occupies a small fraction of the full title crop.
    frame[y0 + 5:y1 - 5, x0 + 30:x0 + 45] = 240
    assert locator.step(frame, state, reader) == []


@pytest.mark.parametrize("change", ["game", "size", "roster"])
def test_new_game_resize_or_roster_change_never_reuses_confirmed_positions(change):
    locator, frame, state, reader = LoadingCardLocator(), image(), roster(), Recognize()
    for _ in range(5):
        cards = locator.step(frame, state, reader)
    assert len(cards) == 5
    if change == "game":
        state["game_key"] = "next-game"
    elif change == "size":
        frame = image(1280, 720)
    else:
        state["teams"][0]["members"].pop()
    assert locator.step(frame, state, Recognize([""] * 10)) == []


def test_statistics_change_does_not_invalidate_position_and_missing_context_does():
    locator, frame, state, reader = LoadingCardLocator(), image(), roster(), Recognize()
    locator.step(frame, state, reader)
    state["teams"][0]["members"][0]["win"] = .73
    locator.step(frame, state, reader)
    assert reader.calls == [0, 1]
    assert locator.step(frame, None, reader) == []
    assert locator.step(frame, state, Recognize([""] * 10)) == []
