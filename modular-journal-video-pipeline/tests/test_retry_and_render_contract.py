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
import render_prl  # noqa: E402
import render_prl_bilibili_cover  # noqa: E402


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

    assert "直接返回 4~6 行正文" in prompt
    assert "返回结构：" not in prompt
    assert "只返回 JSON" not in prompt
    assert "json" not in prompt.lower()



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
    assert "用英文逗号分隔" in seen["prompt"]
    assert "返回 4~12 个中文关键词" in seen["prompt"]
    assert "json" not in seen["prompt"].lower()
    assert result == "黑洞,暗物质,量子多体,无序相变,费米面\n"



def test_build_publish_tags_normalizes_internal_spaces(monkeypatch):
    monkeypatch.setattr(prl_llm_core, "call_openai_compatible", lambda _prompt: 'FP 方程,暗物质,费米面,任意子')

    result = prl_llm_core.build_publish_tags({"papers": [{"brief": "测试 brief。"}]})

    assert result == "FP方程,暗物质,费米面,任意子\n"



def test_build_publish_tags_accepts_keywords_field(monkeypatch):
    monkeypatch.setattr(prl_llm_core, "call_openai_compatible", lambda _prompt: '{"keywords":"对称性,马约拉纳,费米面,暗物质"}')

    result = prl_llm_core.build_publish_tags({"papers": [{"brief": "测试 brief。"}]})

    assert result == "对称性,马约拉纳,费米面,暗物质\n"



def test_cover_extract_keywords_prefers_comma_separated_tags_file(tmp_path):
    tags_path = tmp_path / "publish_tags.txt"
    tags_path.write_text("无序超导,局域化,马约拉纳,费米面\n", encoding="utf-8")

    result = render_prl_bilibili_cover.extract_keywords("", tags_file=str(tags_path), limit=7)

    assert result == ["无序超导", "局域化", "马约拉纳", "费米面"]



def test_request_json_with_retry_retries_only_failed_call(monkeypatch):
    calls = {"count": 0}

    def fake_call(_prompt):
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

    def fake_call(_prompt):
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

    def fake_call(_prompt):
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
