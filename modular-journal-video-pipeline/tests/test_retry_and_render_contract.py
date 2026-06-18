import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

pil = types.ModuleType("PIL")
pil.Image = types.SimpleNamespace(Image=object)
pil.ImageDraw = types.SimpleNamespace(Draw=object, ImageDraw=object)
pil.ImageFont = types.SimpleNamespace(FreeTypeFont=object)
pil.ImageFilter = types.SimpleNamespace(GaussianBlur=object)
sys.modules.setdefault("PIL", pil)
sys.modules.setdefault("edge_tts", types.ModuleType("edge_tts"))

gi = types.ModuleType("gi")
gi.require_version = lambda *args, **kwargs: None
repository = types.ModuleType("repository")
repository.Pango = types.SimpleNamespace()
repository.PangoCairo = types.SimpleNamespace()
gi.repository = repository
sys.modules.setdefault("gi", gi)
sys.modules.setdefault("gi.repository", repository)
sys.modules.setdefault("cairo", types.ModuleType("cairo"))

import prl_llm_core  # noqa: E402
import prl_rss_extract  # noqa: E402
import render_prl  # noqa: E402
import render_prl_bilibili_cover  # noqa: E402


def test_build_item_stub_extracts_rss_authors_from_dc_creator():
    item_xml = """
    <item>
      <title>Test Paper</title>
      <link>http://example.com/paper</link>
      <prism:doi>10.1103/test-doi</prism:doi>
      <dc:creator>I. V. Tokatly, Yao Lu, and F. Sebastian Bergeret</dc:creator>
      <dc:date>2026-05-14T10:00:00+00:00</dc:date>
      <content:encoded><![CDATA[<p>Author(s): I. V. Tokatly, Yao Lu, and F. Sebastian Bergeret</p><p>Abstract text.</p>]]></content:encoded>
    </item>
    """

    stub = prl_rss_extract.build_item_stub(item_xml)

    assert stub["author_text"] == "I. V. Tokatly, Yao Lu, and F. Sebastian Bergeret"
    assert stub["authors"] == ["I. V. Tokatly", "Yao Lu", "F. Sebastian Bergeret"]
    assert stub["first_author"] == "I. V. Tokatly"



def test_build_item_stub_extracts_rss_cover_image():
    item_xml = """
    <item>
      <title>Image Paper</title>
      <link>http://example.com/paper</link>
      <prism:doi>10.1103/test-image-doi</prism:doi>
      <dc:creator>A. Author</dc:creator>
      <dc:date>2026-05-14T10:00:00+00:00</dc:date>
      <description>&lt;p&gt;Abstract.&lt;/p&gt;&lt;img src=\"//cdn.journals.aps.org/journals/PRL/key_images/test.png\" /&gt;</description>
      <content:encoded><![CDATA[<p>Abstract text.</p>]]></content:encoded>
    </item>
    """

    stub = prl_rss_extract.build_item_stub(item_xml)

    assert stub["rss_cover_image"] == "https://cdn.journals.aps.org/journals/PRL/key_images/test.png"



def test_build_item_stub_keeps_only_text_inside_escaped_xml_author_tags():
    item_xml = """
    <item>
      <title>Tagged Author Paper</title>
      <link>http://example.com/paper</link>
      <prism:doi>10.1103/test-tagged-doi</prism:doi>
      <dc:creator>&lt;string&gt;A. Author&lt;/string&gt;, &lt;string&gt;B. Author&lt;/string&gt; and &lt;string&gt;C. Author&lt;/string&gt;</dc:creator>
      <dc:date>2026-05-14T10:00:00+00:00</dc:date>
      <content:encoded><![CDATA[<p>Abstract text.</p>]]></content:encoded>
    </item>
    """

    stub = prl_rss_extract.build_item_stub(item_xml)

    assert stub["author_text"] == "A. Author, B. Author and C. Author"
    assert stub["authors"] == ["A. Author", "B. Author", "C. Author"]
    assert stub["first_author"] == "A. Author"



def test_fake_fill_from_raw_preserves_author_fields():
    raw = {
        "date": "2026-04-30",
        "items": [
            {
                "title_en": "Test Title",
                "doi": "10.1103/test-doi",
                "abstract_en": "A valid abstract.",
                "authors": ["A Author", "B Author"],
                "first_author": "A Author",
                "author_text": "A Author and B Author",
            }
        ],
    }

    result = prl_llm_core.fake_fill_from_raw(raw, selected_n=1, other_n=0)

    assert result["papers"][0]["authors"] == ["A Author", "B Author"]
    assert result["papers"][0]["first_author"] == "A Author"
    assert result["papers"][0]["author_text"] == "A Author and B Author"
    assert result["papers"][0]["paper_url"] == ""
    assert result["papers"][0]["rss_cover_image"] == ""



def test_api_fill_from_raw_preserves_author_fields(monkeypatch):
    raw = {
        "date": "2026-04-30",
        "items": [
            {
                "title_en": "Test Title",
                "doi": "10.1103/test-doi",
                "abstract_en": "A valid abstract.",
                "authors": ["A Author", "B Author"],
                "first_author": "A Author",
                "author_text": "A Author and B Author",
            }
        ],
    }

    def fake_request(prompt, validator, *, label, paper_title_en, doi):
        if label.startswith("page:"):
            return {"key_points": ["甲。", "乙。", "丙。"]}
        if label.startswith("voice:"):
            return {"title_zh": "", "voice_intro": "一句简介。", "voice_points": ["补充句。"]}
        if label.startswith("title:"):
            return {"title_zh": "测试标题"}
        raise AssertionError(label)

    monkeypatch.setattr(prl_llm_core, "request_json_with_retry", fake_request)

    result = prl_llm_core.api_fill_from_raw(raw, selected_n=1, other_n=0)

    assert result["papers"][0]["authors"] == ["A Author", "B Author"]
    assert result["papers"][0]["first_author"] == "A Author"
    assert result["papers"][0]["author_text"] == "A Author and B Author"
    assert result["papers"][0]["paper_url"] == ""
    assert result["papers"][0]["rss_cover_image"] == ""



def test_fake_fill_from_raw_preserves_paper_url_from_link():
    raw = {
        "date": "2026-04-30",
        "items": [
            {
                "title_en": "Test Title",
                "doi": "10.1103/test-doi",
                "abstract_en": "A valid abstract.",
                "link": "http://example.com/paper-1",
                "rss_cover_image": "https://example.com/cover-1.png",
            }
        ],
    }

    result = prl_llm_core.fake_fill_from_raw(raw, selected_n=1, other_n=0)

    assert result["papers"][0]["paper_url"] == "http://example.com/paper-1"
    assert result["papers"][0]["rss_cover_image"] == "https://example.com/cover-1.png"



def test_issue_composition_summary_splits_condensed_and_recent_counts():
    raw = {
        "date": "2026-06-02",
        "feed_date_condensed": "2026-05-28",
        "feed_date_recent": "2026-05-27",
        "item_count_today_condensed": 6,
        "item_count_today_recent": 4,
    }

    meta = prl_llm_core.issue_meta_from_raw(raw)
    desc = prl_llm_core.build_publish_desc({}, raw)

    assert meta["issue_composition_summary"] == "本次凝聚态 6 篇（2026-05-28），其他 PRL 方向补充 4 篇（2026-05-27）。"
    assert meta["cover_subtitle"] == "本次凝聚态 6 篇（2026-05-28），其他 PRL 方向补充 4 篇（2026-05-27）。"
    assert "本次凝聚态 6 篇（2026-05-28），其他 PRL 方向补充 4 篇（2026-05-27）。" in desc



def test_choose_cover_image_url_falls_back_to_second_paper(tmp_path):
    input_path = tmp_path / "input.json"
    input_path.write_text(
        '{"papers": ['
        '{"title_en": "A", "rss_cover_image": ""}, '
        '{"title_en": "B", "rss_cover_image": "https://example.com/cover-2.png"}, '
        '{"title_en": "C", "rss_cover_image": "https://example.com/cover-3.png"}'
        ']}'
    )

    assert render_prl_bilibili_cover.choose_cover_image_url(str(input_path)) == "https://example.com/cover-2.png"



def test_choose_cover_image_url_randomizes_within_selected_papers_when_all_rss_dates_stale(tmp_path):
    input_path = tmp_path / "input.json"
    input_path.write_text(
        json.dumps(
            {
                "date": "2026-06-02",
                "feed_date_condensed": "2026-05-28",
                "feed_date_recent": "2026-05-27",
                "papers": [
                    {"title_en": "A", "rss_cover_image": "https://example.com/cover-1.png", "rss_date": "2026-05-28T10:00:00+00:00"},
                    {"title_en": "B", "rss_cover_image": "https://example.com/cover-2.png", "rss_date": "2026-05-27T10:00:00+00:00"},
                    {"title_en": "C", "rss_cover_image": "https://example.com/cover-3.png", "rss_date": "2026-05-27T10:00:00+00:00"},
                ],
            }
        )
    )

    chosen = render_prl_bilibili_cover.choose_cover_image_url(str(input_path), local_date="2026-06-02")

    assert chosen in {
        "https://example.com/cover-1.png",
        "https://example.com/cover-2.png",
        "https://example.com/cover-3.png",
    }



def test_api_fill_from_raw_does_not_fallback_title_translation(monkeypatch):
    raw = {
        "date": "2026-04-30",
        "items": [
            {
                "title_en": "Test Title",
                "doi": "10.1103/test-doi",
                "abstract_en": "A valid abstract.",
            }
        ],
    }

    def fake_request(prompt, validator, *, label, paper_title_en, doi):
        if label.startswith("page:"):
            return {"key_points": ["甲。", "乙。", "丙。"]}
        if label.startswith("voice:"):
            return {"title_zh": "", "voice_intro": "一句简介。", "voice_points": []}
        if label.startswith("title:"):
            return None
        raise AssertionError(label)

    monkeypatch.setattr(prl_llm_core, "request_json_with_retry", fake_request)

    with pytest.raises(RuntimeError, match="0 valid papers"):
        prl_llm_core.api_fill_from_raw(raw, selected_n=1, other_n=0)


def test_normalize_paper_payload_does_not_invent_content_from_placeholders():
    normalized = render_prl.normalize_paper_payload(
        {
            "title_en": "Placeholder Paper",
            "title_zh": "",
            "brief": "",
            "voice_intro": "",
            "key_points": [],
            "method_results": ["旧字段方法结论"],
            "summary": ["旧字段摘要总结"],
            "doi": "10.1103/test-doi",
        }
    )

    assert normalized["title_zh"] == ""
    assert normalized["brief"] == ""
    assert normalized["voice_intro"] == ""
    assert normalized["key_points"] == []
    assert normalized["author_text"] == ""



def test_normalize_paper_payload_preserves_rss_author_text_except_space_squeeze():
    raw_author_text = "  A. Author,   B. Author and   C. Author  "
    normalized = render_prl.normalize_paper_payload(
        {
            "title_en": "Author Paper",
            "brief": "一句简介。",
            "key_points": ["甲。", "乙。", "丙。"],
            "author_text": raw_author_text,
            "doi": "10.1103/test-doi",
        }
    )

    assert normalized["author_text"] == "A. Author, B. Author and C. Author"



def test_paper_voice_parts_does_not_fallback_to_legacy_fields():
    intro, followups, extra = render_prl.paper_voice_parts(
        {
            "title_en": "Voice Placeholder",
            "brief": "",
            "voice_intro": "",
            "voice_points": [],
            "method_results": ["旧字段方法结论"],
            "summary": ["旧字段摘要总结"],
        }
    )

    assert intro == ""
    assert followups == []
    assert extra == []


def test_voice_payload_does_not_reject_copy_like_intro():
    result = prl_llm_core.validate_voice_payload(
        {"text": "这篇工作利用高红移莱曼α森林数据，给出了原初黑洞暗物质丰度的最新约束。"}
    )

    assert result == {
        "title_zh": "",
        "voice_intro": "这篇工作利用高红移莱曼α森林数据，给出了原初黑洞暗物质丰度的最新约束。",
        "voice_points": [],
    }



def test_voice_payload_preserves_api_prefix_without_trimming_to_fragment():
    result = prl_llm_core.validate_voice_payload(
        {"voice_intro": "作者建立了声学散射中因果律约束的普适求和规则。"}
    )

    assert result == {
        "title_zh": "",
        "voice_intro": "作者建立了声学散射中因果律约束的普适求和规则。",
        "voice_points": [],
    }



def test_page_payload_does_not_reject_copy_like_keypoints():
    result = prl_llm_core.validate_page_payload(
        {
            "key_points": [
                "这篇工作研究了一个量子多体体系。",
                "第二条给出方法设定。",
                "第三条给出主要结果。",
            ]
        }
    )

    assert result == {
        "key_points": [
            "这篇工作研究了一个量子多体体系。",
            "第二条给出方法设定。",
            "第三条给出主要结果。",
        ]
    }



def test_page_payload_accepts_json_string_wrapped_dict():
    result = prl_llm_core.validate_page_payload('{"key_points": ["甲。", "乙。", "丙。"]}')

    assert result == {"key_points": ["甲。", "乙。", "丙。"]}



def test_page_payload_accepts_json_array_string():
    result = prl_llm_core.validate_page_payload('["甲。", "乙。", "丙。"]')

    assert result == {"key_points": ["甲。", "乙。", "丙。"]}



def test_page_payload_accepts_points_dict():
    result = prl_llm_core.validate_page_payload({"points": ["甲。", "乙。", "丙。"]})

    assert result == {"key_points": ["甲。", "乙。", "丙。"]}



def test_page_payload_salvages_sentences_from_plain_text():
    result = prl_llm_core.validate_page_payload("甲。乙。丙。丁。")

    assert result == {"key_points": ["甲。", "乙。", "丙。", "丁。"]}



def test_page_payload_salvages_bullets_from_plain_text():
    result = prl_llm_core.validate_page_payload("- 甲。\n- 乙。\n- 丙。")

    assert result == {"key_points": ["甲。", "乙。", "丙。"]}



def test_title_payload_does_not_reject_copy_like_title():
    result = prl_llm_core.validate_title_payload({"title_zh": "这篇工作关于拓扑超导的研究"})

    assert result == {"title_zh": "这篇工作关于拓扑超导的研究"}



def test_title_payload_accepts_title_cn():
    result = prl_llm_core.validate_title_payload({"title_cn": "对称性强制费米面"})

    assert result == {"title_zh": "对称性强制费米面"}



def test_build_page_prompt_prefers_plain_text_lines_not_structured_output():
    prompt = prl_llm_core.build_page_copy_prompt(
        {
            "title_en": "Symmetry-Enforced Fermi Surfaces",
            "abstract_en": "Discusses F, f(k), and L_F U(1) style structures.",
        }
    )

    assert "总共返回 4~6 行，每行正好 1 句中文" in prompt
    assert "返回结构：" not in prompt
    assert "只返回 JSON" not in prompt
    assert "不要返回 JSON" in prompt



def test_build_title_prompt_prefers_plain_text_not_structured_output():
    prompt = prl_llm_core.build_title_translation_prompt(
        {
            "title_en": "Symmetry-Enforced Fermi Surfaces",
            "abstract_en": "Discusses F, f(k), and L_F U(1) style structures.",
        }
    )

    assert "只返回中文标题这一行" in prompt
    assert "返回结构：" not in prompt
    assert "只返回 JSON" not in prompt
    assert "json" not in prompt.lower()



def test_render_normalize_formula_text_supports_inline_latex():
    result = render_prl.normalize_formula_text("序参量 $\\mathscr{F}$ 与 $L_{\\mathscr{F}}U(1)$，以及 $f(k)$ 的关系")

    assert result == "序参量 𝓕 与 L_𝓕U(1)，以及 f(k) 的关系"



def test_render_normalize_formula_text_keeps_unicode_math_letters():
    result = render_prl.normalize_formula_text("序参量 𝓕 与 L_𝓕U(1) 的关系")

    assert result == "序参量 𝓕 与 L_𝓕U(1) 的关系"



def test_build_page_prompt_requires_inline_formula_examples():
    prompt = prl_llm_core.build_page_copy_prompt(
        {
            "title_en": "Symmetry-Enforced Fermi Surfaces",
            "abstract_en": "Discusses F, f(k), and L_F U(1) style structures.",
        }
    )

    assert "统一用行内 LaTeX 形式写成 $...$" in prompt
    assert "$\\mathscr{F}$" in prompt
    assert "$L_{\\mathscr{F}}U(1)$" in prompt
    assert "$f(k)$" in prompt



def test_build_publish_tags_prefers_api_summary_from_all_briefs(monkeypatch):
    seen = {}

    def fake_call(prompt):
        seen["prompt"] = prompt
        return '黑洞,暗物质,量子多体,无序相变,费米面'

    monkeypatch.setattr(prl_llm_core, "call_openai_compatible", fake_call)

    result = prl_llm_core.build_publish_tags(
        {
            "papers": [
                {"brief": "利用莱曼α森林约束原初黑洞暗物质丰度。", "title_en": "A", "title_zh": "甲"},
                {"brief": "用猫态增强极弱暗物质信号的探测灵敏度。", "title_en": "B", "title_zh": "乙"},
                {"brief": "研究量子门中的混沌涨落。", "title_en": "C", "title_zh": "丙"},
            ]
        }
    )

    assert "利用莱曼α森林约束原初黑洞暗物质丰度。" in seen["prompt"]
    assert "用猫态增强极弱暗物质信号的探测灵敏度。" in seen["prompt"]
    assert seen["prompt"].startswith("输出内容只能是一行英文逗号分隔的标签。")
    assert "不要输出 JSON、Markdown、编号、解释、前缀或后缀。" in seen["prompt"]
    assert "每个标签必须是 2 到 4 个汉字或字符。" in seen["prompt"]
    assert "不要输出泛词或普通机制词" in seen["prompt"]
    assert result == "黑洞,暗物质,量子多体,无序相变,费米面\n"



def test_build_publish_tags_normalizes_internal_spaces(monkeypatch):
    monkeypatch.setattr(prl_llm_core, "call_openai_compatible", lambda _prompt: 'FP 方程,暗物质,费米面,任意子')

    result = prl_llm_core.build_publish_tags({"papers": [{"brief": "测试 brief。"}]})

    assert result == "FP方程,暗物质,费米面,任意子\n"



def test_build_publish_tags_accepts_keywords_field(monkeypatch):
    monkeypatch.setattr(prl_llm_core, "call_openai_compatible", lambda _prompt: '{"keywords":"对称性,马约拉纳,费米面,暗物质"}')

    result = prl_llm_core.build_publish_tags({"papers": [{"brief": "测试 brief。"}]})

    assert result == "对称性,马约拉纳,费米面,暗物质\n"



def test_build_publish_tags_retries_same_prompt_when_api_tags_invalid(monkeypatch):
    calls = []

    def fake_call(prompt):
        calls.append(prompt)
        if len(calls) == 1:
            return "声学求和规则多体非局域性超冷中子光镊阵列费米子量子模拟XY模型BKT相变代数衰减相互作用,拓扑,莫尔,激子"
        return "声学规则,光镊阵列,量子模拟,BKT相变"

    monkeypatch.setattr(prl_llm_core, "call_openai_compatible", fake_call)

    result = prl_llm_core.build_publish_tags({"papers": [{"brief": "测试 brief。"}]})

    assert len(calls) == 2
    assert calls[0] == calls[1]
    assert result == "声学规则,光镊阵列,量子模拟,BKT相变\n"



def test_build_publish_tags_cleans_invalid_second_api_result(monkeypatch):
    calls = []

    def fake_call(prompt):
        calls.append(prompt)
        return "声学求和规则多体非局域性超冷中子光镊阵列费米子量子模拟XY模型BKT相变代数衰减相互作用,拓扑,莫尔,拓扑,,量子霍尔"

    monkeypatch.setattr(prl_llm_core, "call_openai_compatible", fake_call)

    result = prl_llm_core.build_publish_tags({"papers": [{"brief": "测试 brief。"}]})

    assert len(calls) == 2
    assert result == "拓扑,莫尔,量子霍尔\n"



def test_build_publish_tags_marks_failure_and_uses_safe_defaults_when_all_api_tags_invalid(monkeypatch):
    monkeypatch.setattr(
        prl_llm_core,
        "call_openai_compatible",
        lambda _prompt: "声学求和规则多体非局域性超冷中子光镊阵列,另一个非常非常长的标签",
    )

    result = prl_llm_core.build_publish_tags({"papers": [{"brief": "没有可匹配规则的测试 brief。"}]})

    assert result == "物理,科研,PRL\n# tag生成失败\n"



def test_cover_extract_keywords_ignores_tag_failure_comment(tmp_path):
    tags_path = tmp_path / "publish_tags.txt"
    tags_path.write_text("物理,科研,PRL\n# tag生成失败\n", encoding="utf-8")

    result = render_prl_bilibili_cover.extract_keywords("", tags_file=str(tags_path), limit=7)

    assert result == ["物理", "科研", "PRL"]



def test_cover_extract_keywords_prefers_comma_separated_tags_file(tmp_path):
    tags_path = tmp_path / "publish_tags.txt"
    tags_path.write_text("无序超导,局域化,马约拉纳,费米面\n", encoding="utf-8")

    result = render_prl_bilibili_cover.extract_keywords("", tags_file=str(tags_path), limit=7)

    assert result == ["无序超导", "局域化", "马约拉纳", "费米面"]



def test_request_json_with_retry_retries_only_failed_call(monkeypatch):
    calls = {"count": 0}

    def fake_call(_prompt, *, system_prompt=""):
        assert system_prompt == "只输出 JSON，不要输出 markdown 或解释。"
        calls["count"] += 1
        if calls["count"] == 1:
            return '{"key_points": ["坏"]}'
        return '{"key_points": ["甲。", "乙。", "丙。"]}'

    monkeypatch.setattr(prl_llm_core, "call_openai_compatible", fake_call)

    result = prl_llm_core.request_json_with_retry(
        "dummy prompt",
        prl_llm_core.validate_page_payload,
        label="page:test",
        paper_title_en="Test Title",
        doi="10.1103/test-doi",
    )

    assert result == {"key_points": ["甲。", "乙。", "丙。"]}
    assert calls["count"] == 2



def test_request_json_with_retry_salvages_plain_text_page_without_retry(monkeypatch):
    calls = {"count": 0}

    def fake_call(_prompt, *, system_prompt=""):
        assert system_prompt == "只输出 JSON，不要输出 markdown 或解释。"
        calls["count"] += 1
        return "甲。乙。丙。丁。"

    monkeypatch.setattr(prl_llm_core, "call_openai_compatible", fake_call)

    result = prl_llm_core.request_json_with_retry(
        "dummy prompt",
        prl_llm_core.validate_page_payload,
        label="page:test",
        paper_title_en="Test Title",
        doi="10.1103/test-doi",
    )

    assert result == {"key_points": ["甲。", "乙。", "丙。", "丁。"]}
    assert calls["count"] == 1



def test_request_json_with_retry_accepts_json_array_page_without_retry(monkeypatch):
    calls = {"count": 0}

    def fake_call(_prompt, *, system_prompt=""):
        assert system_prompt == "只输出 JSON，不要输出 markdown 或解释。"
        calls["count"] += 1
        return '["甲。", "乙。", "丙。"]'

    monkeypatch.setattr(prl_llm_core, "call_openai_compatible", fake_call)

    result = prl_llm_core.request_json_with_retry(
        "dummy prompt",
        prl_llm_core.validate_page_payload,
        label="page:test",
        paper_title_en="Test Title",
        doi="10.1103/test-doi",
    )

    assert result == {"key_points": ["甲。", "乙。", "丙。"]}
    assert calls["count"] == 1


def test_request_text_with_retry_retries_until_validator_accepts(monkeypatch):
    calls = {"count": 0}

    def fake_call(_prompt, *, system_prompt=""):
        assert system_prompt == ""
        calls["count"] += 1
        return "score=9.5" if calls["count"] == 1 else "9.5"

    monkeypatch.setattr(prl_llm_core, "call_openai_compatible", fake_call)

    result = prl_llm_core.request_text_with_retry(
        "dummy prompt",
        lambda text: float(text) if text.strip().replace('.', '', 1).isdigit() else None,
        label="score:test",
        paper_title_en="Test Title",
        doi="10.1103/test-doi",
    )

    assert result == 9.5
    assert calls["count"] == 2


def test_request_text_with_retry_accepts_pure_number_without_retry(monkeypatch):
    calls = {"count": 0}

    def fake_call(_prompt, *, system_prompt=""):
        assert system_prompt == ""
        calls["count"] += 1
        return "8.7"

    monkeypatch.setattr(prl_llm_core, "call_openai_compatible", fake_call)

    result = prl_llm_core.request_text_with_retry(
        "dummy prompt",
        lambda text: float(text) if text.strip().replace('.', '', 1).isdigit() else None,
        label="score:test",
        paper_title_en="Test Title",
        doi="10.1103/test-doi",
    )

    assert result == 8.7
    assert calls["count"] == 1


# ---- OpenAlex search sanitizer + fallback ----

def test_sanitize_openalex_query_replaces_lucene_specials_with_spaces():
    title = "Ds0*(2317)+→Ds*+γ"
    cleaned = prl_rss_extract.sanitize_openalex_query(title)
    assert "*" not in cleaned
    assert "+" not in cleaned
    assert "(" not in cleaned and ")" not in cleaned
    assert "→" in cleaned and "γ" in cleaned
    assert "Ds0" in cleaned and "2317" in cleaned
    assert "  " not in cleaned


def test_aggressive_sanitize_openalex_query_keeps_only_ascii_alnum_and_truncates():
    title = "Ds0*(2317)+→Ds*+γ Decay"
    cleaned = prl_rss_extract.aggressive_sanitize_openalex_query(title)
    assert cleaned == "Ds0 2317 Ds Decay"

    long_title = "abcdef " * 60
    capped = prl_rss_extract.aggressive_sanitize_openalex_query(long_title, max_chars=50)
    assert len(capped) <= 50


def test_openalex_search_uses_sanitized_query_on_happy_path(monkeypatch):
    captured = {}

    def fake_safe_get_json(url, timeout=30):
        captured["url"] = url
        return {"results": [{"id": "W1"}]}

    monkeypatch.setattr(prl_rss_extract, "safe_get_json", fake_safe_get_json)

    results = prl_rss_extract.openalex_search_by_title("Ds0*(2317)+→Ds*+γ", per_page=12)

    assert results == [{"id": "W1"}]
    assert "%2A" not in captured["url"]
    assert "%2B" not in captured["url"]
    assert "per-page=12" in captured["url"]


def test_openalex_search_skips_retry_on_4xx_and_falls_back_to_aggressive(monkeypatch):
    import requests as _requests

    calls = []

    def fake_safe_get_json(url, timeout=30):
        calls.append(url)
        if len(calls) == 1:
            resp = _requests.Response()
            resp.status_code = 400
            raise _requests.HTTPError("400 Client Error", response=resp)
        return {"results": [{"id": "W_AGGR"}]}

    monkeypatch.setattr(prl_rss_extract, "safe_get_json", fake_safe_get_json)

    results = prl_rss_extract.openalex_search_by_title("Ds0*(2317)+→Ds*+γ", per_page=10)

    assert results == [{"id": "W_AGGR"}]
    assert len(calls) == 2  # primary 400, then aggressive — no retry of primary


def test_openalex_search_retries_primary_on_timeout_then_succeeds(monkeypatch):
    import requests as _requests

    calls = []

    def fake_safe_get_json(url, timeout=30):
        calls.append(url)
        if len(calls) == 1:
            raise _requests.ReadTimeout("timed out")
        return {"results": [{"id": "W_RETRY"}]}

    monkeypatch.setattr(prl_rss_extract, "safe_get_json", fake_safe_get_json)
    monkeypatch.setattr(prl_rss_extract.time, "sleep", lambda *_a, **_k: None)

    results = prl_rss_extract.openalex_search_by_title("A Normal Title", per_page=10)

    assert results == [{"id": "W_RETRY"}]
    assert len(calls) == 2
    assert calls[0] == calls[1]  # same query retried


def test_openalex_search_returns_empty_when_all_attempts_fail(monkeypatch):
    import requests as _requests

    def always_fail(url, timeout=30):
        raise _requests.ConnectionError("dns down")

    monkeypatch.setattr(prl_rss_extract, "safe_get_json", always_fail)
    monkeypatch.setattr(prl_rss_extract.time, "sleep", lambda *_a, **_k: None)

    results = prl_rss_extract.openalex_search_by_title("Ds0*(2317)+→Ds*+γ", per_page=10)

    assert results == []


def test_enrich_item_payload_falls_back_to_rss_snippet_when_openalex_fails(monkeypatch):
    def boom(_title, _doi):
        raise RuntimeError("simulated openalex blow-up")

    monkeypatch.setattr(prl_rss_extract, "choose_best_abstract_from_openalex", boom)

    enriched = prl_rss_extract.enrich_item_payload({
        "title_en": "Some PRL Paper",
        "doi": "10.1103/test",
        "rss_snippet": "RSS abstract sentence.",
    })

    assert enriched["abstract_en"] == "RSS abstract sentence."
    assert enriched["abstract_source"] == "rss_snippet"
    assert enriched["openalex_results_checked"] == 0
    assert enriched["matched_prl_record"] is False
    assert enriched["openalex_top_matches"] == []
    assert enriched["missing_any_abstract"] is False
