#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Render a reusable Bilibili cover image for the PRL condensed-matter daily series.

Usage:
  /usr/bin/python3 render_prl_bilibili_cover.py --date 2026-04-28 --out cover.png
  /usr/bin/python3 render_prl_bilibili_cover.py --date 2026-04-28 --input-json input.json --out cover.png
"""

import argparse
import json
import random
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1920, 1080
BG = (246, 248, 255)
FG = (24, 32, 46)
MUTED = (99, 111, 129)
ACCENT = (93, 91, 255)
ACCENT_2 = (0, 184, 163)
ACCENT_3 = (122, 91, 255)
CARD = (255, 255, 255)
OUTLINE = (220, 227, 241)
FONT_REG_PATH = "/home/cndaqiang/.local/share/fonts/source-han-sans/SourceHanSansSC-Regular.otf"
FONT_BOLD_PATH = "/home/cndaqiang/.local/share/fonts/source-han-sans/SourceHanSansSC-Bold.otf"

KEYWORD_RULES = [
    (r"non[- ]?hermitian|exceptional point", "异常点"),
    (r"topolog", "拓扑"),
    (r"moir[eé]", "莫尔"),
    (r"exciton", "激子"),
    (r"ferroelectric", "铁电"),
    (r"anomalous hall", "反常霍尔"),
    (r"quantum hall", "量子霍尔"),
    (r"superconductor", "超导"),
    (r"chirality|chiral|enantiomer", "手性"),
    (r"floquet", "Floquet"),
    (r"landau", "兰道"),
    (r"orbital magnetization", "轨道磁化"),
    (r"twisted bilayer|heterobilayer", "扭转双层"),
    (r"antiferromagnet", "反铁磁"),
]
DEFAULT_KEYWORDS = ["拓扑", "莫尔", "激子", "量子霍尔", "超导", "手性"]


def seed_from_text(text: str) -> int:
    x = 0
    for ch in text:
        x = (x * 131 + ord(ch)) & 0xFFFFFFFF
    return x


def draw_round_rect(draw: ImageDraw.ImageDraw, box, *, fill, outline=None, radius=32, width=2):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def draw_chip(draw: ImageDraw.ImageDraw, text: str, x: int, y: int, font, *, fg=FG, bg=(241, 245, 255), outline=(212, 221, 242)):
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    pad_x = 22
    pad_y = 12
    h = th + pad_y * 2
    w = tw + pad_x * 2
    draw.rounded_rectangle((x, y, x + w, y + h), radius=h // 2, fill=bg, outline=outline, width=2)
    draw.text((x + pad_x - bbox[0], y + (h - th) / 2 - bbox[1]), text, fill=fg, font=font)
    return x + w


def wrap_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int):
    text = (text or "").strip()
    if not text:
        return []
    tokens = text.split()
    if len(tokens) <= 1:
        out, cur = [], ""
        for ch in text:
            trial = cur + ch
            bbox = draw.textbbox((0, 0), trial, font=font)
            if cur and bbox[2] - bbox[0] > max_width:
                out.append(cur)
                cur = ch
            else:
                cur = trial
        if cur:
            out.append(cur)
        return out

    out, cur = [], ""
    for token in tokens:
        trial = token if not cur else cur + " " + token
        bbox = draw.textbbox((0, 0), trial, font=font)
        if cur and bbox[2] - bbox[0] > max_width:
            out.append(cur)
            cur = token
        else:
            cur = trial
    if cur:
        out.append(cur)
    return out


def draw_gradient_bg(img: Image.Image, seed: int):
    rnd = random.Random(seed)
    base = Image.new("RGBA", (W, H), BG + (255,))
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)

    colors = [
        (219, 228, 255, 180),
        (232, 238, 255, 170),
        (227, 247, 244, 150),
        (238, 231, 255, 145),
    ]
    circles = [
        (-180, -80, 640, 760),
        (W - 720, -120, W + 160, 760),
        (W - 560, H - 480, W + 120, H + 120),
        (-220, H - 420, 520, H + 180),
    ]
    for box, color in zip(circles, colors):
        d.ellipse(box, fill=color)

    for _ in range(18):
        r = rnd.randint(18, 72)
        x = rnd.randint(0, W)
        y = rnd.randint(0, H)
        color = rnd.choice(colors[:-1])[:-1] + (rnd.randint(12, 32),)
        d.ellipse((x - r, y - r, x + r, y + r), fill=color)

    img.alpha_composite(base)
    img.alpha_composite(layer.filter(ImageFilter.GaussianBlur(6)))


def resolve_cover_title(input_json: str, cli_title: str = "") -> str:
    cli_title = (cli_title or "").strip()
    if cli_title:
        return cli_title
    if input_json:
        try:
            data = json.loads(Path(input_json).read_text(encoding="utf-8"))
            title = (data.get("cover_title") or data.get("video_title") or "").strip()
            if title:
                return title
        except Exception:
            pass
    return "PRL今日热点"


def extract_keywords(input_json: str, *, tags_file: str = "", limit: int = 7):
    if tags_file:
        try:
            raw = Path(tags_file).read_text(encoding="utf-8")
            items = [x.strip().lstrip("#") for x in raw.replace("\n", ",").split(",") if x.strip()]
            keywords = []
            for item in items:
                if item and item not in keywords:
                    keywords.append(item)
            if keywords:
                return keywords[:limit]
        except Exception:
            pass
    if not input_json:
        return DEFAULT_KEYWORDS[:limit]
    try:
        data = json.loads(Path(input_json).read_text(encoding="utf-8"))
    except Exception:
        return DEFAULT_KEYWORDS[:limit]

    texts = []
    for paper in (data.get("papers") or [])[:8]:
        texts.append((paper.get("title_zh") or "").strip())
        texts.append((paper.get("title_en") or "").strip())
    blob = "\n".join([t for t in texts if t])
    low = blob.lower()

    keywords = []
    for pattern, label in KEYWORD_RULES:
        if re.search(pattern, low, flags=re.I) and label not in keywords:
            keywords.append(label)
    if not keywords:
        keywords = DEFAULT_KEYWORDS[:]
    return keywords[:limit]


def draw_keyword_cloud(draw: ImageDraw.ImageDraw, keywords, seed: int):
    rnd = random.Random(seed)
    fonts = [
        ImageFont.truetype(FONT_REG_PATH, 28),
        ImageFont.truetype(FONT_REG_PATH, 32),
        ImageFont.truetype(FONT_BOLD_PATH, 30),
    ]
    anchors = [
        (170, 700), (360, 760), (560, 700), (250, 840),
        (470, 880), (700, 780), (820, 860), (930, 720),
    ]
    colors = [
        (93, 91, 255, 150),
        (0, 184, 163, 138),
        (122, 91, 255, 120),
        (99, 111, 129, 110),
    ]
    for i, kw in enumerate(keywords):
        x, y = anchors[i % len(anchors)]
        x += rnd.randint(-24, 24)
        y += rnd.randint(-18, 18)
        font = fonts[i % len(fonts)]
        color = colors[i % len(colors)]
        draw.text((x, y), kw, fill=color, font=font)


def draw_orbit_motif(draw: ImageDraw.ImageDraw):
    center_x, center_y = 1490, 548
    draw.ellipse((1260, 240, 1740, 720), fill=(93, 91, 255, 54), outline=(255, 255, 255, 120), width=4)
    draw.ellipse((1498, 552, 1778, 832), fill=(0, 184, 163, 70), outline=(255, 255, 255, 100), width=4)
    draw.arc((1190, 170, 1810, 790), start=220, end=18, fill=(93, 91, 255, 120), width=6)
    draw.arc((1290, 330, 1830, 870), start=194, end=340, fill=(0, 184, 163, 108), width=5)
    draw.ellipse((1458, 516, 1522, 580), fill=(255, 255, 255, 245), outline=(221, 227, 241), width=2)
    draw.ellipse((1610, 372, 1644, 406), fill=(255, 255, 255, 190), outline=None)
    draw.ellipse((1368, 678, 1398, 708), fill=(255, 255, 255, 180), outline=None)
    ring_font = ImageFont.truetype(FONT_BOLD_PATH, 34)
    bbox = draw.textbbox((0, 0), "CM", font=ring_font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    draw.text((center_x - tw / 2 - bbox[0], center_y - th / 2 - bbox[1]), "CM", fill=(255, 255, 255, 230), font=ring_font)


def render_cover(date: str, out_path: Path, title: str = "", input_json: str = "", tags_file: str = ""):
    img = Image.new("RGBA", (W, H), BG + (255,))
    seed = seed_from_text(date + "|bili-cover-v3")
    draw_gradient_bg(img, seed)
    d = ImageDraw.Draw(img)

    main_card = (84, 92, 1836, 988)
    draw_round_rect(d, main_card, fill=CARD, outline=OUTLINE, radius=58, width=3)

    chip_font = ImageFont.truetype(FONT_BOLD_PATH, 28)
    title_font = ImageFont.truetype(FONT_BOLD_PATH, 128)
    date_font = ImageFont.truetype(FONT_BOLD_PATH, 58)
    x1, y1, x2, y2 = main_card
    left_x = x1 + 58
    left_w = 980

    draw_chip(d, "PHYSICAL REVIEW LETTERS", left_x, y1 + 52, chip_font)

    title = resolve_cover_title(input_json, title)
    title_y = y1 + 190
    for line in wrap_text(d, title, title_font, left_w)[:2]:
        d.text((left_x, title_y), line, fill=FG, font=title_font)
        title_y += 140

    d.text((left_x, title_y + 6), date.replace('-', '.'), fill=ACCENT_3, font=date_font)
    draw_keyword_cloud(d, extract_keywords(input_json, tags_file=tags_file), seed=seed + 17)
    draw_orbit_motif(d)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(out_path, format="PNG", quality=95)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--title", default="")
    ap.add_argument("--input-json", default="", help="Optional input.json used to extract a few cover keywords")
    ap.add_argument("--tags-file", default="", help="Optional publish_tags.txt with comma-separated keywords")
    args = ap.parse_args()
    render_cover(args.date, Path(args.out), title=args.title, input_json=args.input_json, tags_file=args.tags_file)
    print(args.out)


if __name__ == "__main__":
    main()
