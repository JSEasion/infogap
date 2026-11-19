"""匹配引擎：硬门槛 + 加权打分。"""
import json

from market_lens.match import match_job, score_badge
from market_lens.models import DEFAULT_PROFILE


def _job(**kw):
    base = {"title": "Agent 工程师", "company": "X", "city": "北京", "salary_raw": "30-50K",
            "salary_min_k": 30.0, "salary_max_k": 50.0, "wlb_level": 1, "work_mode": "hybrid",
            "company_type": "unicorn", "skills": json.dumps(["langgraph", "mcp", "rag"]),
            "description": ""}
    base.update(kw)
    return base


def test_pass_with_score():
    m = match_job(_job(), DEFAULT_PROFILE)
    assert m.verdict == "pass"
    assert 0 <= m.score <= 100
    assert len(m.dims) == 5


def test_veto_salary_floor():
    p = DEFAULT_PROFILE.model_copy(deep=True)
    p.hard.salary_floor_k = 40
    m = match_job(_job(salary_min_k=30.0), p)
    assert m.verdict == "veto"
    assert any("底线" in r for r in m.reasons)


def test_veto_city():
    p = DEFAULT_PROFILE.model_copy(deep=True)
    p.hard.cities = ["上海"]
    m = match_job(_job(city="北京"), p)
    assert m.verdict == "veto"
    assert any("城市" in r for r in m.reasons)


def test_city_subregion_matches():
    """『北京·海淀区』应匹配目标『北京』。"""
    p = DEFAULT_PROFILE.model_copy(deep=True)
    p.hard.cities = ["北京"]
    m = match_job(_job(city="北京·海淀区"), p)
    assert m.verdict == "pass"


def test_remote_city_rule():
    p = DEFAULT_PROFILE.model_copy(deep=True)
    p.hard.cities = ["远程"]
    m = match_job(_job(city="不限", work_mode="remote"), p)
    assert m.verdict == "pass"
    m2 = match_job(_job(city="北京", work_mode="onsite"), p)
    assert m2.verdict == "veto"


def test_veto_overtime():
    p = DEFAULT_PROFILE.model_copy(deep=True)
    p.hard.overtime_max = 1
    m = match_job(_job(wlb_level=3), p)
    assert m.verdict == "veto"
    assert any("加班" in r for r in m.reasons)


def test_unknown_fields_warn_not_veto():
    """薪资/WLB 未知 → 提醒不否决。"""
    m = match_job(_job(salary_min_k=None, salary_max_k=None, wlb_level=None), DEFAULT_PROFILE)
    assert m.verdict == "pass"
    assert m.reasons  # 有数据缺失提醒


def test_weights_affect_score():
    j = _job(wlb_level=4)  # 加班重的岗位
    p_low = DEFAULT_PROFILE.model_copy(deep=True)
    p_low.prefs.weights = {"wlb": 1, "work_mode": 3, "company_type": 2, "tech_fit": 4, "salary_margin": 3}
    p_high = DEFAULT_PROFILE.model_copy(deep=True)
    p_high.prefs.weights = {"wlb": 5, "work_mode": 3, "company_type": 2, "tech_fit": 4, "salary_margin": 3}
    p_high.hard.overtime_max = 4
    p_low.hard.overtime_max = 4
    s_low = match_job(j, p_low).score
    s_high = match_job(j, p_high).score
    assert s_high < s_low   # WLB 权重越高，加班重岗位扣分越多


def test_tech_avoid_penalty():
    p = DEFAULT_PROFILE.model_copy(deep=True)
    p.prefs.tech_avoid = ["coze"]
    good = match_job(_job(), p).score
    bad = match_job(_job(skills=json.dumps(["langgraph", "mcp", "coze"])), p).score
    assert bad < good


def test_badge():
    assert score_badge(None, "veto") == ("否决", "badge-veto")
    label, cls = score_badge(85, "pass")
    assert label == "85" and cls == "badge-good"
