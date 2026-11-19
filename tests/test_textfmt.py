"""textfmt：文本规范化 + 无效摘要检测（数据为王的地基）。"""
import market_lens.textfmt as tf


def test_normalize_cjk_space_artifact():
    """HTML→text 伪影：汉字间空格必须清除，英文词间空格保留。"""
    assert tf.normalize_text("负责 AI 平台 开发") == "负责 AI 平台开发"  # AI 两侧空格保留（英文词）
    assert tf.normalize_text("负责 平台 开发，要求如下") == "负责平台开发，要求如下"


def test_normalize_blank_lines_and_bullets():
    raw = "岗位描述\n\n\n\n要求：\n  • Python 熟练\n  ●熟悉 LLM\n  -加分：RAG"
    out = tf.normalize_text(raw)
    assert "\n\n\n" not in out            # 连续空行折叠
    assert "· Python 熟练" in out          # 列表符归一
    assert "· 熟悉 LLM" in out
    assert "· 加分：RAG" in out            # ASCII - 也归一


def test_normalize_trailing_spaces():
    assert tf.normalize_text("行尾空白   \n下一行") == "行尾空白\n下一行"


def test_fragment_merge():
    """碎行合并：无句末标点的短行并入下一段。"""
    out = tf.normalize_text("负责公司级 AI 平台的\n设计与落地，包含 Agent 编排")
    assert "设计与落地" in out


def test_to_paragraphs():
    ps = tf.to_paragraphs("第一段。\n\n第二段。")
    assert ps == ["第一段。", "第二段。"]


def test_bad_summary_detection():
    """无效摘要黑名单：点击查看原文/过短/与标题重复。"""
    assert tf.is_bad_summary("点击查看原文>>")[0]
    assert tf.is_bad_summary("")[0]
    assert tf.is_bad_summary("太短了", "标题")[0]
    ok, why = tf.is_bad_summary("OpenAI 发布 o5，推理成本下降 70%，上下文 512K", "OpenAI")
    assert not ok, why
    dup = "某公司发布重要公告宣布重大更新了一系列产品线"
    assert tf.is_bad_summary(dup, dup)[0]   # 与标题完全一致


def test_clean_inline():
    assert tf.clean_inline("多\n行\n文本", limit=6) == "多 行 文本"
