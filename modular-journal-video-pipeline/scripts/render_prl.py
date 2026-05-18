#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Render PRL daily short video from a filled template JSON.

Why you heard "乱码/奇怪内容" at the beginning:
- We previously fed SSML tags (<speak>, <voice>, <break>) directly into TTS.
- Some TTS endpoints may read the tags aloud instead of parsing them.

Fix:
- Do NOT feed SSML.
- Synthesize each bullet as its own audio segment and concatenate with real silent gaps.

Video rules (per D):
- Vertical 720x1280
- Cover slide: PRL今日热点解读 / 日期 / 论文列表
- Paper slide: title + blank line + 简述 + 方法与结果(bullets) + 总结(bullets)
- Footer: DOI only (no labels)
- Voice template:
  - Slide 1: PRL今日热点解读，{date}。
  - Paper slide: 标题/简述/要点/总结都直接口播，不额外加“论文”“要点如下”“意义如下”等提示语

Usage:
  python3 render_prl.py --input input.json [--outdir outdir]

Input schema:
{
  "date": "YYYY-MM-DD",
  "papers": [
    {
      "title_en": "...",
      "title_zh": "...",  // for TTS; may be empty
      "doi": "10.1103/xxxx",
      "brief": "...",
      "key_points": ["..."]
    }
  ]
}

Env knobs:
- PRL_VOICE (default zh-CN-YunxiNeural)
- PRL_TTS_RATE (default +0%)
- PRL_PAUSE_SECONDS (pause between slides, default 0.55)
- PRL_BULLET_PAUSE_SECONDS (pause between bullets, default 0.2)
"""

import argparse
import asyncio
import contextlib
import json
import math
import os
import random
import re
import subprocess
import wave
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont
import edge_tts
import gi
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Pango, PangoCairo
import cairo

try:
    import imageio_ffmpeg
except Exception:
    imageio_ffmpeg = None

# -------------------- Visual style --------------------
W, H = 720, 1280
MARGIN_X = 44
TOP_Y = 72
FOOTER_H = 92
BG = (250, 251, 255)
BG_2 = (238, 244, 255)
FG = (27, 34, 48)
MUTED = (102, 116, 138)
BLUE = (56, 118, 255)
ACCENT = (112, 86, 255)
ACCENT_2 = (0, 174, 163)
CARD = (255, 255, 255)
CARD_2 = (248, 250, 255)
CARD_3 = (255, 255, 255)
OUTLINE = (214, 223, 240)

FONT_REG_PATH = "/home/cndaqiang/.local/share/fonts/source-han-sans/SourceHanSansSC-Regular.otf"
FONT_BOLD_PATH = "/home/cndaqiang/.local/share/fonts/source-han-sans/SourceHanSansSC-Bold.otf"

TITLE_SIZE_COVER = 54
TITLE_SIZE_PAPER = 42
SUB_SIZE = 26
BODY_SIZE = 21
LABEL_SIZE = 26
FOOT_SIZE = 20

# -------------------- Audio settings --------------------
VOICE = os.environ.get("PRL_VOICE", "zh-CN-XiaoxiaoNeural")
_default_alt_voice = "zh-CN-YunjianNeural" if VOICE != "zh-CN-YunjianNeural" else "zh-CN-XiaoxiaoNeural"
VOICE_ALT = os.environ.get("PRL_VOICE_ALT", _default_alt_voice)
RATE = os.environ.get("PRL_TTS_RATE", "+0%")
VOICE_CHAR_BUDGET = int(os.environ.get("PRL_VOICE_CHAR_BUDGET", "40"))
PRE_SPEECH_PAUSE = float(os.environ.get("PRL_PRE_SPEECH_PAUSE_SECONDS", "1.0"))
BULLET_PAUSE_S = float(os.environ.get("PRL_BULLET_PAUSE_SECONDS", "0.2"))
OTHER_HOTSPOTS_SECONDS = float(os.environ.get("PRL_OTHER_HOTSPOTS_SECONDS", "4.0"))
MAX_OTHER_HOTSPOTS = int(os.environ.get("PRL_MAX_OTHER_HOTSPOTS", "10"))
TARGET_TOTAL_SECONDS = float(os.environ.get("PRL_TARGET_TOTAL_SECONDS", "60.0"))
MIN_OTHER_HOTSPOTS_SECONDS = float(os.environ.get("PRL_MIN_OTHER_HOTSPOTS_SECONDS", "2.0"))
AUDIO_SR = 24000


def ffmpeg_exe() -> str:
    if imageio_ffmpeg is None:
        raise RuntimeError("imageio-ffmpeg not installed")
    return imageio_ffmpeg.get_ffmpeg_exe()


def _font_weight(font: ImageFont.FreeTypeFont) -> int:
    return Pango.Weight.BOLD if getattr(font, "path", "") == FONT_BOLD_PATH else Pango.Weight.NORMAL


def _pango_layout(text: str, font: ImageFont.FreeTypeFont, max_width: int):
    surface = cairo.ImageSurface(cairo.Format.ARGB32, max(1, int(max_width)), 8)
    ctx = cairo.Context(surface)
    layout = PangoCairo.create_layout(ctx)
    desc = Pango.FontDescription()
    desc.set_family("Source Han Sans SC")
    desc.set_absolute_size(int(font.size * Pango.SCALE))
    desc.set_weight(_font_weight(font))
    layout.set_font_description(desc)
    layout.set_width(max(1, int(max_width)) * Pango.SCALE)
    layout.set_wrap(Pango.WrapMode.WORD)
    layout.set_text((text or "").replace("\r\n", "\n"), -1)
    return layout


def draw_text_pango(img: Image.Image, text: str, x: int, y: int, *, font: ImageFont.FreeTypeFont, color: Tuple[int, int, int], max_width: int | None = None) -> Tuple[int, int]:
    text = (text or "").replace("\r\n", "\n")
    if not text:
        return 0, 0
    width = max(1, int(max_width or (W - x)))
    layout = _pango_layout(text, font, width)
    ink_rect, logical_rect = layout.get_pixel_extents()
    render_w = max(1, logical_rect.width + max(0, -ink_rect.x) + 6)
    render_h = max(1, logical_rect.height + max(0, -ink_rect.y) + 6)

    surface = cairo.ImageSurface(cairo.Format.ARGB32, render_w, render_h)
    ctx = cairo.Context(surface)
    ctx.set_source_rgba(0, 0, 0, 0)
    ctx.paint()
    ctx.translate(max(0, -ink_rect.x) + 3, max(0, -ink_rect.y) + 3)
    layout = PangoCairo.create_layout(ctx)
    desc = Pango.FontDescription()
    desc.set_family("Source Han Sans SC")
    desc.set_absolute_size(int(font.size * Pango.SCALE))
    desc.set_weight(_font_weight(font))
    layout.set_font_description(desc)
    layout.set_width(width * Pango.SCALE)
    layout.set_wrap(Pango.WrapMode.WORD)
    layout.set_text(text, -1)
    ctx.set_source_rgba(color[0] / 255.0, color[1] / 255.0, color[2] / 255.0, 1.0)
    PangoCairo.show_layout(ctx, layout)

    stride = surface.get_stride()
    buf = surface.get_data()
    overlay = Image.frombuffer("RGBA", (render_w, render_h), bytes(buf), "raw", "BGRA", stride, 1)
    img.alpha_composite(overlay, (int(x), int(y)))
    return logical_rect.width, logical_rect.height


def wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> List[str]:
    layout = _pango_layout(text, font, max_width)
    raw = (text or "").replace("\r\n", "\n")
    raw_bytes = raw.encode("utf-8")
    lines: List[str] = []
    for idx in range(layout.get_line_count()):
        line = layout.get_line_readonly(idx)
        if line is None:
            continue
        piece = raw_bytes[line.start_index: line.start_index + line.length].decode("utf-8", errors="ignore").strip()
        if piece:
            lines.append(piece)
    return lines


def measure_text_box(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> Tuple[int, int]:
    layout = _pango_layout(text, font, max_width)
    return layout.get_pixel_size()


def clamp(v: float, lo: int = 0, hi: int = 255) -> int:
    return max(lo, min(hi, int(v)))


def mix(c1: Tuple[int, int, int], c2: Tuple[int, int, int], t: float) -> Tuple[int, int, int]:
    return tuple(clamp(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))


def seed_from_text(text: str) -> int:
    return sum((idx + 1) * ord(ch) for idx, ch in enumerate(text or "")) % (2 ** 32)


MATHSCR_MAP = {
    "A": "𝒜", "B": "ℬ", "C": "𝒞", "D": "𝒟", "E": "ℰ", "F": "𝓕", "G": "𝒢", "H": "ℋ", "I": "ℐ", "J": "𝒥",
    "K": "𝒦", "L": "ℒ", "M": "ℳ", "N": "𝒩", "O": "𝒪", "P": "𝒫", "Q": "𝒬", "R": "ℛ", "S": "𝒮", "T": "𝒯",
    "U": "𝒰", "V": "𝒱", "W": "𝒲", "X": "𝒳", "Y": "𝒴", "Z": "𝒵",
    "a": "𝒶", "b": "𝒷", "c": "𝒸", "d": "𝒹", "e": "ℯ", "f": "𝒻", "g": "ℊ", "h": "𝒽", "i": "𝒾", "j": "𝒿",
    "k": "𝓀", "l": "𝓁", "m": "𝓂", "n": "𝓃", "o": "ℴ", "p": "𝓅", "q": "𝓆", "r": "𝓇", "s": "𝓈", "t": "𝓉",
    "u": "𝓊", "v": "𝓋", "w": "𝓌", "x": "𝓍", "y": "𝓎", "z": "𝓏",
}


def _latex_fragment_to_display(text: str) -> str:
    s = text or ""
    s = re.sub(r"\\(?:text|mathrm)\{([^{}]*)\}", lambda m: m.group(1), s)
    s = re.sub(r"\\mathscr\{([A-Za-z])\}", lambda m: MATHSCR_MAP.get(m.group(1), m.group(1)), s)
    s = re.sub(r"\\mathcal\{([A-Za-z])\}", lambda m: MATHSCR_MAP.get(m.group(1), m.group(1)), s)
    s = re.sub(r"_\{([^}]*)\}", lambda m: "_" + (m.group(1) or ""), s)
    s = re.sub(r"\^\{([^}]*)\}", lambda m: "^" + (m.group(1) or ""), s)
    s = s.replace("\\", "")
    s = s.replace("{", "").replace("}", "")
    return s


def normalize_formula_text(text: str) -> str:
    s = (text or "").strip()
    s = re.sub(r"\$([^$]+)\$", lambda m: _latex_fragment_to_display(m.group(1)), s)
    s = _latex_fragment_to_display(s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def squeeze_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def normalize_mixed_spacing(text: str) -> str:
    s = squeeze_spaces(normalize_formula_text(text))
    s = re.sub(r"([\u4e00-\u9fff])\s+([A-Za-z0-9])", r"\1\2", s)
    s = re.sub(r"([A-Za-z0-9])\s+([\u4e00-\u9fff])", r"\1\2", s)
    s = re.sub(r"([A-Za-z0-9])([α-ωΑ-Ω])", r"\1 \2", s)
    s = re.sub(r"([α-ωΑ-Ω])([A-Za-z0-9])", r"\1 \2", s)
    s = re.sub(r"\s+([,，。；：、！？.!?:;])", r"\1", s)
    s = re.sub(r"([(（【《“])\s+", r"\1", s)
    s = re.sub(r"\s+([)）】》”])", r"\1", s)
    return squeeze_spaces(s)


def looks_like_placeholder(text: str) -> bool:
    s = squeeze_spaces(text)
    if not s:
        return True
    bad_patterns = [
        r"PRL论文解读[0-9a-f]{6}$",
        r"自动摘要",
        r"自动总结",
        r"自动化测试文案",
        r"用于联调",
        r"占位",
        r"稳定跑通",
        r"相关问题展开",
        r"基于摘要片段整理",
    ]
    return any(re.search(p, s, flags=re.I) for p in bad_patterns)


def clean_point(text: str) -> str:
    s = normalize_mixed_spacing(text)
    s = re.sub(r"^[-•]\s*", "", s)
    s = re.sub(r"^摘要片段提示[:：]\s*", "", s)
    s = re.sub(r"^自动摘要要点[0-9a-f-]+[:：]\s*", "", s)
    s = re.sub(r"^自动总结[0-9a-f-]+[:：]\s*", "", s)
    s = re.sub(r"^我们(?=(首次|发现|提出|展示|证明|观察到|实现|揭示|说明|得到|构建|设计|验证))", "", s)
    s = re.sub(r"^它也为", "也为", s)
    s = re.sub(r"^它为", "为", s)
    s = re.sub(r"^(论文|文章)", "", s)
    s = re.sub(r"^(这项工作|本研究|该研究)(?=[把利用讨论研究关注聚焦提出揭示展示分析比较验证说明发现围绕重新])", "", s)
    s = re.sub(r"^(直接结论是|重点如下|意义如下|这篇工作已经|这一页重点整理|它的价值在于|它的重要性在于|研究对象是|重点在于|核心目标是|研究场景聚焦在|如果方案可行[,，]?|如果结论成立[,，]?|它为|它也为)[:：，, ]*", "", s)
    s = re.sub(r"^(这是一篇|一篇)(前瞻性)?(述评|评论)[，,：: ]*", "", s)
    s = re.sub(r"^目标是[，,:： ]*", "", s)
    return s.strip()


def normalize_paper_payload(p: dict) -> dict:
    title_en = normalize_formula_text(p.get("title_en") or "")
    title_zh_raw = squeeze_spaces(normalize_formula_text(p.get("title_zh") or ""))
    title_zh = "" if looks_like_placeholder(title_zh_raw) else title_zh_raw

    brief_raw = clean_point(p.get("brief") or "")
    brief = "" if looks_like_placeholder(brief_raw) else brief_raw

    key_points = [clean_point(x) for x in (p.get("key_points") or [])]
    key_points = [x for x in key_points if x and not looks_like_placeholder(x)]

    doi = squeeze_spaces(p.get("doi") or "")
    if doi and not re.match(r"^10\.1103/\S+$", doi):
        doi = ""

    author_text = squeeze_spaces(p.get("author_text") or "")

    voice_intro_raw = clean_point(p.get("voice_intro") or "")
    voice_intro = "" if looks_like_placeholder(voice_intro_raw) else voice_intro_raw
    if not brief:
        brief = voice_intro
    elif not voice_intro:
        voice_intro = brief
    voice_points = [clean_point(x) for x in (p.get("voice_points") or [])]
    voice_points = [x for x in voice_points if x and not looks_like_placeholder(x) and x != voice_intro]

    return {
        "title_en": title_en,
        "title_zh": title_zh,
        "brief": brief,
        "key_points": key_points,
        "doi": doi,
        "author_text": author_text,
        "voice_intro": voice_intro,
        "voice_points": voice_points,
    }


def draw_gradient_bg(img: Image.Image, *, seed: int):
    rnd = random.Random(seed)
    draw = ImageDraw.Draw(img)
    top = mix(BG, ACCENT, 0.08)
    bottom = mix(BG_2, ACCENT_2, 0.04)
    for y in range(H):
        t = y / max(1, H - 1)
        color = mix(top, bottom, t)
        draw.line((0, y, W, y), fill=color)

    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    for _ in range(9):
        cx = rnd.randint(-120, W + 120)
        cy = rnd.randint(-120, H + 120)
        r = rnd.randint(90, 220)
        color = tuple(list(mix(ACCENT, ACCENT_2, rnd.random())) + [26])
        od.ellipse((cx - r, cy - r, cx + r, cy + r), fill=color)
    img.alpha_composite(overlay)


def draw_round_rect(draw: ImageDraw.ImageDraw, box, *, fill, outline=None, radius=28, width=2):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def draw_chip(draw: ImageDraw.ImageDraw, text: str, x: int, y: int, *, fg=FG, bg=(241, 245, 255, 235), outline=(212, 221, 242, 255)) -> int:
    font = ImageFont.truetype(FONT_BOLD_PATH, 22)
    pad_x, pad_y = 18, 10
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    box_h = th + pad_y * 2
    box = (x, y, x + int(tw) + pad_x * 2, y + box_h)
    draw_round_rect(draw, box, fill=bg, outline=outline, radius=20, width=1)
    text_x = x + pad_x - bbox[0]
    text_y = y + int((box_h - th) / 2) - bbox[1]
    draw.text((text_x, text_y), text, fill=fg, font=font)
    return box[2]


def draw_abstract_figure(img: Image.Image, box, *, seed: int, label: str = ""):
    x1, y1, x2, y2 = box
    w = x2 - x1
    h = y2 - y1
    rnd = random.Random(seed)
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)

    draw_round_rect(d, box, fill=(245, 248, 255, 235), outline=(214, 223, 240, 255), radius=34, width=2)

    for _ in range(22):
        px = x1 + rnd.randint(40, max(41, w - 40))
        py = y1 + rnd.randint(40, max(41, h - 40))
        r = rnd.randint(4, 12)
        color = tuple(list(mix(ACCENT, ACCENT_2, rnd.random())) + [190])
        d.ellipse((px - r, py - r, px + r, py + r), fill=color)

    for idx in range(4):
        line_color = tuple(list(mix(ACCENT, ACCENT_2, idx / 4)) + [155])
        points = []
        amp = rnd.randint(26, 56)
        base_y = y1 + int(h * (0.22 + idx * 0.17))
        phase = rnd.random() * math.pi * 2
        step = max(10, w // 30)
        for dx in range(24, w - 24, step):
            px = x1 + dx
            py = base_y + int(math.sin((dx / max(1, w)) * math.pi * 2 + phase) * amp)
            points.append((px, py))
        if len(points) >= 2:
            d.line(points, fill=line_color, width=3)

    ring_r = min(w, h) // 4
    cx = x1 + int(w * 0.72)
    cy = y1 + int(h * 0.34)
    for idx, alpha in enumerate([120, 80, 50]):
        rr = ring_r + idx * 26
        d.ellipse((cx - rr, cy - rr, cx + rr, cy + rr), outline=(186, 197, 224, alpha), width=2)

    if label:
        font = ImageFont.truetype(FONT_BOLD_PATH, 20)
        d.text((x1 + 22, y2 - 42), label, fill=(82, 97, 126, 220), font=font)

    img.alpha_composite(overlay)


def draw_footer(img: Image.Image, doi_line: str):
    doi_line = (doi_line or "").strip()
    if not doi_line:
        return
    d = ImageDraw.Draw(img)
    y0 = H - FOOTER_H
    font = ImageFont.truetype(FONT_REG_PATH, FOOT_SIZE)

    tw = d.textlength(doi_line, font=font)
    pad_x = 18
    pill_w = int(tw) + pad_x * 2
    x = max(MARGIN_X, (W - pill_w) / 2)
    draw_round_rect(d, (x, y0 + 18, x + pill_w, y0 + 18 + 44), fill=(245, 248, 255), outline=(220, 227, 242), radius=22, width=1)
    d.text((x + pad_x, y0 + 28), doi_line, fill=MUTED, font=font)


def draw_title(draw: ImageDraw.ImageDraw, title: str, y: int, *, size: int, color: Tuple[int, int, int]) -> int:
    max_w = W - 2 * MARGIN_X
    font = ImageFont.truetype(FONT_BOLD_PATH, size)
    lines = wrap_text(draw, title, font, max_w)
    if len(lines) > 3:
        lines = lines[:3]
        lines[-1] = lines[-1].rstrip() + "…"

    for ln in lines:
        draw.text((MARGIN_X, y), ln, fill=color, font=font)
        y += 58
    return y


def draw_sub_lines(draw: ImageDraw.ImageDraw, lines: List[str], y: int) -> int:
    max_w = W - 2 * MARGIN_X
    font = ImageFont.truetype(FONT_REG_PATH, SUB_SIZE)
    for line in lines:
        for ln in wrap_text(draw, line, font, max_w):
            draw.text((MARGIN_X, y), ln, fill=BLUE, font=font)
            y += 40
    return y


def draw_label(draw: ImageDraw.ImageDraw, label: str, y: int) -> int:
    font = ImageFont.truetype(FONT_BOLD_PATH, LABEL_SIZE)
    draw.text((MARGIN_X, y), label, fill=FG, font=font)
    return y + 44


def draw_bullets(draw: ImageDraw.ImageDraw, bullets: List[str], y: int) -> int:
    max_w = W - 2 * MARGIN_X
    font = ImageFont.truetype(FONT_REG_PATH, BODY_SIZE)
    bullet_indent = 26
    gap = 12

    for b in bullets:
        b = (b or "").strip()
        if not b:
            continue
        wrapped = wrap_text(draw, b, font, max_w - bullet_indent)
        if not wrapped:
            continue
        draw.text((MARGIN_X, y), "•", fill=FG, font=font)
        draw.text((MARGIN_X + bullet_indent, y), wrapped[0], fill=FG, font=font)
        y += 40
        for cont in wrapped[1:]:
            draw.text((MARGIN_X + bullet_indent, y), cont, fill=FG, font=font)
            y += 40
        y += gap
        if y > H - FOOTER_H - 50:
            break
    return y


def measure_section_height(draw: ImageDraw.ImageDraw, bullets: List[str], *, font_size: int = BODY_SIZE, line_step: int = 36) -> int:
    max_w = W - 2 * MARGIN_X - 32
    font = ImageFont.truetype(FONT_REG_PATH, font_size)
    bullet_x = MARGIN_X + 28
    text_x = bullet_x + 20
    wrap_w = max_w - (text_x - (MARGIN_X + 10))
    total = 60
    has_content = False
    for b in bullets:
        b = (b or "").strip()
        if not b:
            continue
        wrapped = wrap_text(draw, b, font, wrap_w)
        if not wrapped:
            continue
        has_content = True
        total += line_step * len(wrapped) + 8
    if not has_content:
        total += line_step
    return total + 24


def draw_section_card(img: Image.Image, draw: ImageDraw.ImageDraw, title: str, bullets: List[str], y: int, *, height: int, accent: Tuple[int, int, int], body_font_size: int = BODY_SIZE, line_step: int = 36) -> int:
    box = (MARGIN_X, y, W - MARGIN_X, y + height)
    draw_round_rect(draw, box, fill=CARD_2, outline=OUTLINE, radius=30, width=2)
    title_font = ImageFont.truetype(FONT_BOLD_PATH, LABEL_SIZE)
    draw.text((MARGIN_X + 24, y + 20), title, fill=FG, font=title_font)
    draw.rounded_rectangle((W - MARGIN_X - 92, y + 24, W - MARGIN_X - 28, y + 32), radius=4, fill=accent)

    inner_y = y + 62
    max_w = W - 2 * MARGIN_X - 32
    font = ImageFont.truetype(FONT_REG_PATH, body_font_size)
    bullet_x = MARGIN_X + 28
    bullet_w = 10
    text_x = bullet_x + 20
    max_inner_y = y + height - 24
    for b in bullets:
        b = (b or "").strip()
        if not b:
            continue
        wrapped = wrap_text(draw, b, font, max_w - (text_x - (MARGIN_X + 6)))
        if not wrapped:
            continue
        needed = line_step * len(wrapped) + 8
        if inner_y + needed > max_inner_y:
            break
        draw.rounded_rectangle((bullet_x, inner_y + max(9, int(line_step * 0.30)), bullet_x + bullet_w, inner_y + max(16, int(line_step * 0.50))), radius=3, fill=accent)
        draw_text_pango(img, wrapped[0], text_x, inner_y, font=font, color=FG, max_width=max_w - (text_x - (MARGIN_X + 6)))
        inner_y += line_step
        for cont in wrapped[1:]:
            draw_text_pango(img, cont, text_x, inner_y, font=font, color=FG, max_width=max_w - (text_x - (MARGIN_X + 6)))
            inner_y += line_step
        inner_y += 4
    return y + height


def render_cover(date: str, titles: List[str], dois: List[str], out_path: Path, *, cover_title: str = "PRL今日热点", cover_subtitle: str = "", section_label: str = "今日精讲"):
    img = Image.new("RGBA", (W, H), BG + (255,))
    draw_gradient_bg(img, seed=seed_from_text(date + "|cover"))
    d = ImageDraw.Draw(img)

    hero = (MARGIN_X, 72, W - MARGIN_X, 356)
    draw_round_rect(d, hero, fill=CARD, outline=(214, 223, 240), radius=36, width=2)

    hero_x1, hero_y1, hero_x2, hero_y2 = hero
    pad_x = 20
    inner_x = hero_x1 + pad_x
    inner_w = hero_x2 - hero_x1 - pad_x * 2

    draw_chip(d, "PHYSICAL REVIEW LETTERS", inner_x, hero_y1 + 26)

    title_font = ImageFont.truetype(FONT_BOLD_PATH, 42)
    title_y = hero_y1 + 94
    title_lines = wrap_text(d, cover_title, title_font, inner_w)
    for ln in title_lines[:2]:
        d.text((inner_x, title_y), ln, fill=FG, font=title_font)
        title_y += 52

    sub_font = ImageFont.truetype(FONT_REG_PATH, 24)
    subtitle = cover_subtitle or date
    d.text((inner_x, title_y + 2), subtitle, fill=MUTED, font=sub_font)

    list_box = (MARGIN_X, 388, W - MARGIN_X, H - 42)
    draw_round_rect(d, list_box, fill=CARD_3, outline=OUTLINE, radius=34, width=2)
    box_x1, box_y1, box_x2, box_y2 = list_box
    section_font = ImageFont.truetype(FONT_BOLD_PATH, 28)
    d.text((box_x1 + 26, box_y1 + 24), section_label, fill=FG, font=section_font)

    cards = titles
    row_gap = 10
    card_top = box_y1 + 76
    card_w = box_x2 - box_x1 - 36
    avail_h = box_y2 - card_top - 18
    num_font = ImageFont.truetype(FONT_BOLD_PATH, 17)

    x1 = box_x1 + 18
    x2 = x1 + card_w
    text_x = x1 + 46
    max_w = x2 - text_x - 16

    card_layouts = []
    for body_size, line_step in ((18, 24), (17, 23), (16, 22), (15, 21), (14, 20)):
        body_font = ImageFont.truetype(FONT_REG_PATH, body_size)
        trial_layouts = []
        total_h = 0
        for idx, title in enumerate(cards):
            wrapped = wrap_text(d, title.replace("-", "‑"), body_font, max_w)
            if not wrapped:
                wrapped = [""]
            text_h = line_step * len(wrapped)
            card_h = max(68, 28 + text_h + 18)
            trial_layouts.append({
                "idx": idx,
                "wrapped": wrapped,
                "card_h": card_h,
                "body_font": body_font,
                "line_step": line_step,
            })
            total_h += card_h
        total_h += row_gap * max(0, len(trial_layouts) - 1)
        if total_h <= avail_h or body_size == 14:
            card_layouts = trial_layouts
            break

    y = card_top
    for item in card_layouts:
        card_h = item["card_h"]
        y1 = y
        y2 = y1 + card_h
        if y2 > box_y2 - 18:
            break
        draw_round_rect(d, (x1, y1, x2, y2), fill=(249, 251, 255), outline=(226, 232, 244), radius=24, width=1)

        num_x = x1 + 18
        num_y = y1 + 20
        d.text((num_x, num_y), f"{item['idx'] + 1}", fill=ACCENT_2, font=num_font)

        text_y = y1 + 14
        for ln in item["wrapped"]:
            d.text((text_x, text_y), ln, fill=FG, font=item["body_font"])
            text_y += item["line_step"]
        y = y2 + row_gap

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(out_path, format="PNG")


def render_paper(p: dict, out_path: Path):
    paper = normalize_paper_payload(p)
    img = Image.new("RGBA", (W, H), BG + (255,))
    seed_text = paper.get("doi") or paper.get("title_en") or paper.get("title_zh") or "paper"
    draw_gradient_bg(img, seed=seed_from_text(seed_text))
    d = ImageDraw.Draw(img)

    def bad_title_layout(lines: List[str], font: ImageFont.FreeTypeFont) -> bool:
        if not lines:
            return True
        if len(lines) >= 3:
            last = lines[-1].strip()
            if re.fullmatch(r"[A-Za-z0-9À-ÿα-ωΑ-Ω_./+\-–—]+", last):
                return True
            if re.match(r"^(of|in|on|at|to|for|with|by|from|and|or)\b", last, re.I):
                return True
        for idx, line in enumerate(lines[:-1]):
            if re.search(r"\b(of|in|on|at|to|for|with|by|from|and|or)$", line.strip(), re.I):
                return True
            width = d.textlength(line, font=font)
            if idx > 0 and width < inner_w * 0.42:
                return True
        return False

    pad_x = 20
    hero_x1, hero_y1, hero_x2 = MARGIN_X, 72, W - MARGIN_X
    inner_x = hero_x1 + pad_x
    inner_w = hero_x2 - hero_x1 - pad_x * 2

    title_lines: List[str] = []
    title_font = None
    for size in (36, 34, 32, 30, 28):
        candidate_font = ImageFont.truetype(FONT_BOLD_PATH, size)
        candidate_lines = wrap_text(d, paper["title_en"].replace("-", "‑"), candidate_font, inner_w)
        if len(candidate_lines) <= 3:
            title_font = candidate_font
            title_lines = candidate_lines
            break
    if not title_lines:
        title_font = ImageFont.truetype(FONT_BOLD_PATH, 26)
        title_lines = wrap_text(d, paper["title_en"].replace("-", "‑"), title_font, inner_w)

    author_lines: List[str] = []
    author_font = None
    for size in (18, 17, 16, 15):
        candidate_font = ImageFont.truetype(FONT_REG_PATH, size)
        candidate_lines = wrap_text(d, paper["author_text"], candidate_font, inner_w)
        if not candidate_lines:
            candidate_lines = [""]
        if len(candidate_lines) <= 4:
            author_font = candidate_font
            author_lines = candidate_lines
            break
    if not author_lines:
        author_font = ImageFont.truetype(FONT_REG_PATH, 15)
        author_lines = wrap_text(d, paper["author_text"], author_font, inner_w)
        if not author_lines:
            author_lines = [""]

    brief_lines: List[str] = []
    brief_font = None
    for size in (20, 19, 18, 17):
        candidate_font = ImageFont.truetype(FONT_REG_PATH, size)
        candidate_lines = wrap_text(d, paper["brief"], candidate_font, inner_w)
        if len(candidate_lines) <= 3:
            brief_font = candidate_font
            brief_lines = candidate_lines
            break
    if not brief_lines:
        brief_font = ImageFont.truetype(FONT_REG_PATH, 17)
        brief_lines = wrap_text(d, paper["brief"], brief_font, inner_w)[:3]

    title_step = max(38, int(title_font.size * 1.18))
    author_step = max(26, int(author_font.size * 1.45))
    brief_step = max(34, int(brief_font.size * 1.55))
    hero_h = 34 + title_step * len(title_lines) + 16 + author_step * max(len(author_lines), 1) + 12 + brief_step * max(len(brief_lines), 1) + 30
    hero_h = max(268, min(hero_h, 420))
    hero = (hero_x1, hero_y1, hero_x2, hero_y1 + hero_h)
    draw_round_rect(d, hero, fill=CARD, outline=(214, 223, 240), radius=34, width=2)
    hero_y2 = hero[3]

    y = hero_y1 + 34
    for ln in title_lines:
        draw_text_pango(img, ln, inner_x, y, font=title_font, color=FG, max_width=inner_w)
        y += title_step

    y += 16
    for ln in author_lines:
        draw_text_pango(img, ln, inner_x, y, font=author_font, color=MUTED, max_width=inner_w)
        y += author_step

    y += 12
    for ln in brief_lines:
        draw_text_pango(img, ln, inner_x, y, font=brief_font, color=FG, max_width=inner_w)
        y += brief_step

    section_top = hero_y2 + 22
    bottom_pad = FOOTER_H
    usable_height = H - bottom_pad - section_top

    section_style = None
    for body_font_size, section_line_step in ((BODY_SIZE, 36), (20, 34), (19, 32), (18, 30), (17, 28)):
        key_needed = measure_section_height(d, paper["key_points"], font_size=body_font_size, line_step=section_line_step)
        if key_needed <= usable_height:
            section_style = (body_font_size, section_line_step, key_needed)
            break
    if section_style is None:
        body_font_size, section_line_step = 17, 28
        key_needed = measure_section_height(d, paper["key_points"], font_size=body_font_size, line_step=section_line_step)
    else:
        body_font_size, section_line_step, key_needed = section_style

    key_h = min(max(key_needed, 132), usable_height)

    y = section_top
    y = draw_section_card(img, d, "关键要点", paper["key_points"], y, height=key_h, accent=ACCENT_2, body_font_size=body_font_size, line_step=section_line_step)

    draw_footer(img, f"DOI {paper['doi']}" if paper["doi"] else "")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(out_path, format="PNG")


def normalize_other_paper_payload(p: dict) -> dict:
    title_en = normalize_formula_text(p.get("title_en") or "")
    doi = squeeze_spaces(p.get("doi") or "")
    if doi and not re.match(r"^10\.1103/\S+$", doi):
        doi = ""
    return {
        "title_en": title_en,
        "title_zh": "",
        "doi": doi,
    }


def render_other_hotspots(date: str, other_papers: List[dict], out_path: Path):
    items = [normalize_other_paper_payload(p) for p in other_papers if (p.get("title_en") or p.get("title_zh"))]
    items = items[: max(1, MAX_OTHER_HOTSPOTS)]
    img = Image.new("RGBA", (W, H), BG + (255,))
    draw_gradient_bg(img, seed=seed_from_text(date + "|other-hotspots"))
    d = ImageDraw.Draw(img)

    hero = (MARGIN_X, 72, W - MARGIN_X, 166)
    draw_round_rect(d, hero, fill=CARD, outline=(214, 223, 240), radius=34, width=2)
    hero_x1, hero_y1, hero_x2, hero_y2 = hero
    title_font = ImageFont.truetype(FONT_BOLD_PATH, 40)
    title = "其他热点"
    title_bbox = d.textbbox((0, 0), title, font=title_font)
    title_w = title_bbox[2] - title_bbox[0]
    title_h = title_bbox[3] - title_bbox[1]
    title_x = int((hero_x1 + hero_x2 - title_w) / 2)
    title_y = int((hero_y1 + hero_y2 - title_h) / 2 - title_bbox[1])
    d.text((title_x, title_y), title, fill=FG, font=title_font)

    title_font = ImageFont.truetype(FONT_BOLD_PATH, 18)
    num_font = ImageFont.truetype(FONT_BOLD_PATH, 30)
    divider = (231, 236, 246)
    line_step = 21
    inner_pad_x = 14
    inner_pad_y = 8
    row_gap = 0
    row_h = 87
    list_top = 202
    list_bottom = min(H - 18, list_top + inner_pad_y * 2 + row_h * len(items) + 8)
    list_box = (MARGIN_X, list_top, W - MARGIN_X, list_bottom)
    draw_round_rect(d, list_box, fill=CARD_3, outline=OUTLINE, radius=34, width=2)
    box_x1, box_y1, box_x2, box_y2 = list_box

    def fit_lines(text: str, font, max_width: int, max_lines: int | None = None) -> List[str]:
        lines = wrap_text(d, text, font, max_width)
        if max_lines is not None:
            lines = lines[:max_lines]
        return lines

    full_x1 = box_x1 + inner_pad_x
    full_x2 = box_x2 - inner_pad_x
    text_x = full_x1 + 42
    max_text_w = full_x2 - text_x - 8

    for idx, item in enumerate(items):
        y1 = box_y1 + inner_pad_y + idx * (row_h + row_gap)
        y2 = y1 + row_h
        if idx > 0:
            d.line((full_x1, y1, full_x2, y1), fill=divider, width=1)

        num_text = f"{idx+1:02d}"
        num_bbox = d.textbbox((0, 0), num_text, font=num_font)
        num_h = num_bbox[3] - num_bbox[1]
        num_y = int(y1 + (row_h - num_h) / 2 - num_bbox[1])
        d.text((full_x1, num_y), num_text, fill=ACCENT_2, font=num_font)

        max_lines = 2
        en_lines = fit_lines(item["title_en"].replace("-", "‑"), title_font, max_text_w, max_lines)
        text_block_h = len(en_lines) * line_step if en_lines else 0
        y = int(y1 + (row_h - text_block_h) / 2)
        if en_lines:
            for line in en_lines:
                d.text((text_x, y), line, fill=FG, font=title_font)
                y += line_step

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(out_path, format="PNG")


def wav_duration(path: Path) -> float:
    with contextlib.closing(wave.open(str(path), "rb")) as w:
        return w.getnframes() / float(w.getframerate())


async def _tts_save_mp3(text: str, mp3_path: Path, rate: str, voice: str):
    communicate = edge_tts.Communicate(text=text, voice=voice, rate=rate)
    await communicate.save(str(mp3_path))


def tts_to_mp3(text: str, mp3_path: Path, voice: Optional[str] = None):
    mp3_path.parent.mkdir(parents=True, exist_ok=True)
    voice_name = voice or VOICE

    async def _run():
        last = None

        # Keep voice speed at the requested/default normal rate; only dedupe candidates.
        rate_candidates = []
        for r in [RATE, "+0%"]:
            if r not in rate_candidates:
                rate_candidates.append(r)

        for r in rate_candidates:
            for attempt in range(4):
                try:
                    await _tts_save_mp3(text, mp3_path, r, voice_name)
                    return
                except Exception as e:
                    last = e
                    await asyncio.sleep(0.9 + 0.4 * attempt)

        raise RuntimeError(f"TTS failed: {last}")

    asyncio.run(_run())


def mp3_to_wav(mp3_path: Path, wav_path: Path, sample_rate: int = AUDIO_SR):
    cmd = [ffmpeg_exe(), "-y", "-i", str(mp3_path), "-ar", str(sample_rate), "-ac", "1", str(wav_path)]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg mp3->wav failed: {p.stderr[-800:]}")


def silence_wav(duration_s: float, wav_path: Path, sample_rate: int = AUDIO_SR):
    cmd = [ffmpeg_exe(), "-y", "-f", "lavfi", "-t", f"{duration_s}", "-i", f"anullsrc=r={sample_rate}:cl=mono", str(wav_path)]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg silence wav failed: {p.stderr[-800:]}")


def concat_wavs(wavs: List[Path], out_wav: Path):
    if not wavs:
        raise ValueError("concat_wavs: empty")
    inputs: List[str] = []
    for w in wavs:
        inputs += ["-i", str(w)]
    parts = "".join([f"[{i}:a]" for i in range(len(wavs))])
    filt = parts + f"concat=n={len(wavs)}:v=0:a=1[outa]"
    cmd = [ffmpeg_exe(), "-y", *inputs, "-filter_complex", filt, "-map", "[outa]", str(out_wav)]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg concat failed: {p.stderr[-1200:]}")


def concat_wavs_with_fixed_pause(wavs: List[Path], pause_s: float, out_wav: Path):
    if not wavs:
        raise ValueError("concat_wavs_with_fixed_pause: empty")

    # Insert pauses as additional inputs
    inputs: List[str] = []
    for w in wavs:
        inputs += ["-i", str(w)]

    pause_count = max(0, len(wavs) - 1)
    for _ in range(pause_count):
        inputs += ["-f", "lavfi", "-t", f"{pause_s}", "-i", f"anullsrc=r={AUDIO_SR}:cl=mono"]

    # [a0][p0][a1][p1]...
    parts: List[str] = []
    idx = 0
    for i in range(len(wavs)):
        parts.append(f"[{idx}:a]")
        idx += 1
        if i < len(wavs) - 1:
            parts.append(f"[{idx}:a]")
            idx += 1

    filt = "".join(parts) + f"concat=n={len(parts)}:v=0:a=1[outa]"
    cmd = [ffmpeg_exe(), "-y", *inputs, "-filter_complex", filt, "-map", "[outa]", str(out_wav)]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg concat+pause failed: {p.stderr[-1200:]}")


def run_ffmpeg_slides(slides: List[Path], durations: List[float], audio_wav: Path, out_mp4: Path):
    concat_txt = out_mp4.parent / "concat.txt"
    with open(concat_txt, "w", encoding="utf-8") as f:
        for p, d in zip(slides, durations):
            f.write(f"file '{p.resolve().as_posix()}'\n")
            f.write(f"duration {d:.3f}\n")
        f.write(f"file '{slides[-1].resolve().as_posix()}'\n")

    cmd = [
        ffmpeg_exe(),
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_txt),
        "-i",
        str(audio_wav),
        "-vf",
        "fps=30,format=yuv420p",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-shortest",
        str(out_mp4),
    ]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg video failed: {p.stderr[-1200:]}")


def paper_voice_parts(p: dict) -> Tuple[str, List[str], List[str]]:
    paper = normalize_paper_payload(p)

    def clean(s: str) -> str:
        s = (s or "").strip()
        s = s.replace("《", "").replace("》", "")
        s = s.replace("（", "(").replace("）", ")")
        s = s.replace("。。", "。").replace("..", ".")
        return s.strip()

    def ensure_period(s: str) -> str:
        s = s.strip()
        if not s:
            return s
        if s.endswith(("。", "！", "？", ".", "!", "?")):
            return s
        return s + "。"

    def voice_clean(s: str) -> str:
        return ensure_period(clean_point(clean(s)))

    def text_len(s: str) -> int:
        return len(re.sub(r"[\s，。；：、】【（）()、,.!?？！:;\-]", "", s))

    shared_intro = voice_clean(paper.get("brief") or p.get("voice_intro") or "")
    voice_intro = shared_intro
    voice_points = [voice_clean(x) for x in (p.get("voice_points") or []) if clean(x)]
    voice_points = [x for x in voice_points if x and x != voice_intro]
    if voice_intro or voice_points:
        intro = voice_intro or voice_points[0]
        budget = VOICE_CHAR_BUDGET
        followups: List[str] = []
        current = text_len(intro)
        for candidate in voice_points:
            if candidate == intro:
                continue
            cand_len = text_len(candidate)
            if current + cand_len <= budget:
                followups.append(candidate)
                current += cand_len
            if len(followups) >= 2:
                break
        return intro, followups, []

    return shared_intro, [], []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--outdir", default=None)
    args = ap.parse_args()

    data = json.loads(Path(args.input).read_text(encoding="utf-8"))
    date = data["date"]
    papers: List[dict] = data["papers"]
    other_papers: List[dict] = data.get("other_papers") or []

    out_dir = Path(args.outdir) if args.outdir else (Path("/home/cndaqiang/work/hermes/workspace/bilibili/tmp/prl_daily/out") / date / "render")
    slides_dir = out_dir / "slides"
    audio_dir = out_dir / "audio"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Render slides
    slide_paths: List[Path] = []
    cover = slides_dir / "slide_1.png"
    render_cover(
        date,
        [p["title_en"] for p in papers],
        [p["doi"] for p in papers],
        cover,
        cover_title=data.get("cover_title") or data.get("video_title") or "PRL今日热点",
        cover_subtitle=data.get("cover_subtitle") or date,
        section_label=data.get("section_label") or "今日精讲",
    )
    slide_paths.append(cover)

    for i, p in enumerate(papers, 1):
        out_path = slides_dir / f"slide_{i+1}.png"
        render_paper(p, out_path)
        slide_paths.append(out_path)

    if other_papers:
        tail = slides_dir / f"slide_{len(slide_paths)+1}.png"
        render_other_hotspots(date, other_papers, tail)
        slide_paths.append(tail)

    # Build and synthesize per-slide audio
    if audio_dir.exists():
        for f in audio_dir.glob("*"):
            f.unlink(missing_ok=True)
    audio_dir.mkdir(parents=True, exist_ok=True)

    slide_wavs: List[Path] = []
    seg_durs: List[float] = []

    # Slide 1 audio
    issue_name = (data.get("video_title") or "PRL今日热点").strip()
    s1_text = f"{date.replace('-', '年', 1).replace('-', '月', 1)}日{issue_name}。"
    s1_mp3 = audio_dir / "seg_1.mp3"
    s1_wav = audio_dir / "seg_1.wav"
    tts_to_mp3(s1_text, s1_mp3, VOICE)
    mp3_to_wav(s1_mp3, s1_wav)
    slide_wavs.append(s1_wav)
    seg_durs.append(wav_duration(s1_wav))

    script_lines: List[str] = [s1_text]

    paper_voices = [VOICE]
    if VOICE_ALT and VOICE_ALT != VOICE:
        paper_voices.append(VOICE_ALT)

    # Paper slide audios
    for idx, p in enumerate(papers, 1):
        paper_voice = paper_voices[(idx - 1) % len(paper_voices)]
        intro, mr, sm = paper_voice_parts(p)
        script_lines.append(intro)
        script_lines.extend([f"- {x}" for x in mr])
        script_lines.extend([f"- {x}" for x in sm])

        parts_wavs: List[Path] = []

        def synth(text: str, name: str) -> Path:
            mp3 = audio_dir / f"paper{idx}_{name}.mp3"
            wav = audio_dir / f"paper{idx}_{name}.wav"
            tts_to_mp3(text, mp3, paper_voice)
            mp3_to_wav(mp3, wav)
            return wav

        # pre-speech pause so the new page is visible before narration starts
        if PRE_SPEECH_PAUSE > 0:
            pre_pause_wav = audio_dir / f"paper{idx}_pre_pause.wav"
            silence_wav(PRE_SPEECH_PAUSE, pre_pause_wav)
            parts_wavs.append(pre_pause_wav)

        # intro + direct bullets
        parts_wavs.append(synth(intro, "intro"))

        # bullets with pauses
        mr_wavs = [synth(x, f"mr{i}") for i, x in enumerate(mr, 1)]
        if mr_wavs:
            mr_concat = audio_dir / f"paper{idx}_mr_concat.wav"
            concat_wavs_with_fixed_pause(mr_wavs, BULLET_PAUSE_S, mr_concat)
            parts_wavs.append(mr_concat)

        sm_wavs = [synth(x, f"sm{i}") for i, x in enumerate(sm, 1)]
        if sm_wavs:
            sm_concat = audio_dir / f"paper{idx}_sm_concat.wav"
            concat_wavs_with_fixed_pause(sm_wavs, BULLET_PAUSE_S, sm_concat)
            parts_wavs.append(sm_concat)

        slide_wav = audio_dir / f"seg_{idx+1}.wav"
        concat_wavs(parts_wavs, slide_wav)
        slide_wavs.append(slide_wav)
        seg_durs.append(wav_duration(slide_wav))

    # Tail slide audio (silent dwell for list reading)
    tail_seconds = 0.0
    if other_papers:
        elapsed_before_tail = sum(seg_durs)
        if elapsed_before_tail < TARGET_TOTAL_SECONDS:
            tail_seconds = max(TARGET_TOTAL_SECONDS - elapsed_before_tail, MIN_OTHER_HOTSPOTS_SECONDS)
        else:
            tail_seconds = MIN_OTHER_HOTSPOTS_SECONDS
        tail_wav = audio_dir / f"seg_{len(slide_wavs)+1}.wav"
        silence_wav(tail_seconds, tail_wav)
        slide_wavs.append(tail_wav)
        seg_durs.append(wav_duration(tail_wav))

    # Concatenate slides with page pause
    voice_wav = out_dir / "voice.wav"
    concat_wavs(slide_wavs, voice_wav)

    slide_seconds = list(seg_durs)

    out_mp4 = out_dir / "out.mp4"
    run_ffmpeg_slides(slide_paths, slide_seconds, voice_wav, out_mp4)

    (out_dir / "script.txt").write_text("\n".join(script_lines) + "\n", encoding="utf-8")

    meta = {
        "date": date,
        "papers": papers,
        "other_papers": other_papers,
        "voice": VOICE,
        "voice_alt": VOICE_ALT,
        "segment_seconds": seg_durs,
        "pause_seconds": 0.0,
        "slide_seconds": slide_seconds,
        "audio_seconds": sum(seg_durs),
        "target_total_seconds": TARGET_TOTAL_SECONDS,
        "other_hotspots_seconds": tail_seconds if other_papers else 0.0,
        "out": str(out_mp4),
    }
    (out_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    print(str(out_mp4))


if __name__ == "__main__":
    main()
