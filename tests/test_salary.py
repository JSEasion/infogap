"""薪资解析全覆盖。"""
from market_lens.salary import parse_salary


def test_k_range_with_months():
    r = parse_salary("15-25K·14薪")
    assert (r.min_k, r.max_k, r.months) == (15, 25, 14)
    assert r.annual_min_wan == 21.0   # 15K*14/10
    assert r.annual_max_wan == 35.0


def test_k_range_plain():
    r = parse_salary("25-45K")
    assert (r.min_k, r.max_k, r.months) == (25, 45, 12)
    assert r.mid_k == 35.0


def test_wan_range():
    r = parse_salary("2-4万")
    assert (r.min_k, r.max_k) == (20, 40)


def test_wan_range_2():
    r = parse_salary("1.5万-3万")
    assert (r.min_k, r.max_k) == (15, 30)


def test_annual():
    r = parse_salary("20万-40万/年")
    assert r.min_k is not None and abs(r.min_k - 16.7) < 0.2
    assert abs(r.max_k - 33.3) < 0.2
    assert r.months == 12


def test_daily():
    r = parse_salary("2000-3500元/天")
    assert abs(r.min_k - 43.5) < 0.5
    assert abs(r.max_k - 76.1) < 0.5


def test_floor_only():
    r = parse_salary("25K以上")
    assert r.min_k == 25 and r.max_k is None
    assert r.mid_k == 25


def test_negotiable_and_junk():
    assert parse_salary("面议") is None
    assert parse_salary("") is None
    assert parse_salary(None) is None
    assert parse_salary("薪资面谈") is None


def test_full_width_and_spaces():
    r = parse_salary("１５－２５Ｋ")
    assert (r.min_k, r.max_k) == (15, 25)


def test_salary_line_in_context():
    """从一行混杂文本中截取。"""
    r = parse_salary("薪资：30-55K·15薪")
    assert (r.min_k, r.max_k, r.months) == (30, 55, 15)


def test_usd_annual_k():
    """$120k-$160k → 人民币月薪K。"""
    r = parse_salary("$120k - $160k")
    assert r.min_k == 72.0 and r.max_k == 96.0   # 120k*7.2/12
    assert "USD" in r.note


def test_usd_annual_comma():
    r = parse_salary("$120,000 - $160,000")
    assert r.min_k == 72.0 and r.max_k == 96.0


def test_usd_monthly_k():
    r = parse_salary("$6k-$9k")
    assert r.min_k == 43.2 and r.max_k == 64.8   # 月薪折算


def test_gbp():
    r = parse_salary("£60-80k")
    assert abs(r.min_k - 45.5) < 0.5             # 60k*9.1/12


def test_rmb_unaffected():
    assert parse_salary("15-25K·14薪").min_k == 15


def test_gbp_single_value_annual():
    """£90k+（单值+股权）按英镑年薪折算，而不是当人民币 90K/月。"""
    r = parse_salary("£90k + 1%股权")
    assert r.min_k is not None and abs(r.min_k - 68.2) < 0.2
    assert r.max_k is None


def test_eur_suffix_word():
    """EUR 后缀（去空格后单词边界丢失）也应识别为欧元年薪。"""
    r = parse_salary("70k-100k EUR")
    assert abs(r.min_k - 45.5) < 0.2 and abs(r.max_k - 65.0) < 0.2


def test_usd_chinese_word():
    r = parse_salary("70K-80K美元")
    assert (r.min_k, r.max_k) == (42.0, 48.0)


def test_unsupported_currency_rejected():
    """PHP 比索等不支持的币种返回 None，绝不冒充人民币/美元。"""
    assert parse_salary("Php80,000-100,000/月 + $1,000签约奖金") is None


def test_foreign_small_single_value_rejected():
    """小额外币单值（月薪/奖金歧义）放弃，宁缺毋假。"""
    assert parse_salary("$1,000签约奖金") is None


def test_usd_annual_marker():
    r = parse_salary("$35,000 - $70,000/年")
    assert (r.min_k, r.max_k) == (21.0, 42.0)
