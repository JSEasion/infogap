"""WLB 信号抽取。"""
from market_lens.wlb import analyze_wlb


def test_positive_only():
    r = analyze_wlb("弹性工作，双休，三餐免费")
    assert r.level in (0, 1)
    assert r.signals and all(s.kind == "pos" for s in r.signals)


def test_explicit_996():
    r = analyze_wlb("我们实行996工作制")
    assert r.level == 4


def test_explicit_daxiaozhou():
    r = analyze_wlb("创业氛围浓厚，大小周")
    assert r.level == 3


def test_explicit_965():
    r = analyze_wlb("965 工作制，双休")
    assert r.level == 0


def test_negation():
    """「不强制996」不应判为 L4。"""
    r = analyze_wlb("我们不强制996，弹性办公")
    assert r.level != 4


def test_negative_rhetoric():
    r = analyze_wlb("抗压能力强，快节奏，狼性文化")
    assert r.level == 2
    assert len([s for s in r.signals if s.kind == "neg"]) >= 3


def test_no_signals():
    r = analyze_wlb("负责后端接口开发，熟悉 MySQL Redis")
    assert r.level is None


def test_quotes_present():
    r = analyze_wlb("高强度交付")
    assert r.signals[0].quote  # 证据引用非空
