"""规则抽取 / 粘贴切分 / 结构化入库。"""
import json

from market_lens.extract import _find_salary_raw, extract_rules, split_paste, structure
from market_lens.sample_data import SAMPLE_JOBS


def test_split_paste():
    text = "JD1 内容\n---\nJD2 内容\n---\nJD3"
    assert len(split_paste(text)) == 3
    assert split_paste("") == []
    assert split_paste("单条JD") == ["单条JD"]


def test_extract_rules_typical():
    j = extract_rules(SAMPLE_JOBS[0])  # 字节跳动 35-60K·16薪
    assert j["title"]
    assert j["salary_min_k"] == 35 and j["salary_max_k"] == 60 and j["salary_months"] == 16
    assert "langgraph" in json.loads(j["skills"])
    assert j["company_type"] == "bigtech"
    assert j["wlb_level"] is not None


def test_extract_rules_city():
    j = extract_rules("AI工程师\n城市：成都\n职责略")
    assert j["city"] == "成都"


def test_structure_dedup(tmp_db):
    s1 = structure(tmp_db, SAMPLE_JOBS[:5], "sample", use_llm=False)
    assert s1.added == 5 and s1.duplicates == 0
    s2 = structure(tmp_db, SAMPLE_JOBS[:5], "sample", use_llm=False)
    assert s2.added == 0 and s2.duplicates == 5


def test_structure_fts_tokens(tmp_db):
    structure(tmp_db, [SAMPLE_JOBS[0]], "sample", use_llm=False)
    row = tmp_db.execute("SELECT skills_text, desc_tokens FROM jobs LIMIT 1").fetchone()
    assert row["skills_text"] and row["desc_tokens"]   # FTS 冗余列已填充


def test_all_samples_structurable(tmp_db):
    """32 条示例 JD 全部能规则入库（标题+薪资识别率抽检）。"""
    s = structure(tmp_db, SAMPLE_JOBS, "sample", use_llm=False)
    assert s.added == len(SAMPLE_JOBS)
    rows = tmp_db.execute(
        "SELECT COUNT(*) c FROM jobs WHERE salary_min_k IS NOT NULL").fetchone()
    assert rows["c"] >= len(SAMPLE_JOBS) * 0.9   # 薪资识别率 ≥90%
    titled = tmp_db.execute(
        "SELECT COUNT(*) c FROM jobs WHERE title != ''").fetchone()
    assert titled["c"] == len(SAMPLE_JOBS)


def test_find_salary_raw_multipage_guard():
    """多岗位列表页（≥3 个不同薪资区间）无法断定归属 → 放弃抓取，宁缺毋假。"""
    multi = """高校人才网岗位列表
电机智能控制算法工程师
18-30万/年
机器人运控算法工程师
面议
AI产品经理
20-40万/年
控制算法与软件工程师
15-25万/年"""
    assert _find_salary_raw(multi) == ""
    # 单条 JD 正常识别
    single = "AI工程师\n公司:某公司\n薪资：15-25K·13薪\n职责略"
    assert _find_salary_raw(single) == "15-25K·13薪"


def test_enrich_one_salary_consistency(tmp_db, monkeypatch):
    """LLM 增强回写时 salary_raw 与数值字段必须同进退，不留下「有数无据」的行。"""
    from market_lens import db
    from market_lens.sources import realjobs
    structure(tmp_db, ["AI工程师\n公司:某公司\n城市:北京\n薪资：15-25K\n职责略"],
              "test", use_llm=False)
    jid = tmp_db.execute("SELECT id FROM jobs LIMIT 1").fetchone()["id"]
    db_path = str(db.DB_PATH)

    def llm_no_salary(gateway, texts):
        return [{"title": "AI工程师", "company": "某公司", "city": "北京",
                 "salary_raw": "", "experience": "3-5年", "education": "本科",
                 "work_mode": "onsite", "company_type": "startup",
                 "skills": ["python"], "desc_zh": ""}]

    def llm_new_salary(gateway, texts):
        return [dict(llm_no_salary(gateway, texts)[0], salary_raw="20-40万/年")]

    # LLM 没识别出薪资 → 规则层 raw 与数值一起保留（而不是 raw 清空数值残留）
    monkeypatch.setattr("market_lens.extract.extract_llm", llm_no_salary)
    assert realjobs._enrich_one(db_path, None, jid, "任意文本") is True
    row = tmp_db.execute(
        "SELECT salary_raw, salary_min_k, salary_max_k FROM jobs WHERE id=?", (jid,)).fetchone()
    assert (row["salary_raw"], row["salary_min_k"], row["salary_max_k"]) == ("15-25K", 15.0, 25.0)

    # LLM 给出可解析薪资 → raw 与数值一起更新
    monkeypatch.setattr("market_lens.extract.extract_llm", llm_new_salary)
    assert realjobs._enrich_one(db_path, None, jid, "任意文本") is True
    row = tmp_db.execute(
        "SELECT salary_raw, salary_min_k, salary_max_k FROM jobs WHERE id=?", (jid,)).fetchone()
    assert (row["salary_raw"], row["salary_min_k"], row["salary_max_k"]) == ("20-40万/年", 16.7, 33.3)
