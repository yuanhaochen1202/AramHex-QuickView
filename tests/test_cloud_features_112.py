"""定制版保留本机的标题去重，网络配置不能关闭它。"""

import pytest

from lolhex import cloud
from lolhex.vision.detector import AugmentDetector
from lolhex.vision.ocr import TextLine


def hits():
    # 同一行：左卡名字在 0.30 和 0.38 两处都匹配到（1.1.1 坏校准的样子），右卡在 0.70
    H = 1080
    return [(TextLine("甲", .99, (556, 400, 596, 430)), "A", 99.0),
            (TextLine("甲", .99, (710, 400, 750, 430)), "A", 98.0),
            (TextLine("丙", .99, (1324, 400, 1364, 430)), "C", 99.0)], H


def test_defaults_on_when_offline_or_unset(monkeypatch):
    monkeypatch.setattr(cloud, "get", lambda: cloud._merge({}))
    for name in ("title_priority", "same_name_once", "overlay_fit", "cards_drop_early"):
        assert cloud.feature(name) is True
    h, H = hits()
    assert [aid for _, aid, _ in sorted(AugmentDetector._pick_row(h, H), key=lambda t: t[0].cx)] == ["A", "C"]


@pytest.mark.parametrize("value", [False, "false"])
def test_remote_feature_cannot_disable_title_deduplication(monkeypatch, value):
    cfg = cloud._merge({"features": {"same_name_once": value}})
    monkeypatch.setattr(cloud, "get", lambda: cfg)
    assert "same_name_once" not in cfg["features"]
    h, H = hits()
    assert [aid for _, aid, _ in sorted(AugmentDetector._pick_row(h, H), key=lambda t: t[0].cx)] == ["A", "C"]
