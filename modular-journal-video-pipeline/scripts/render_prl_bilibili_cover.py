#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Render a reusable Bilibili cover image for the PRL condensed-matter daily series.

Usage:
  /usr/bin/python3 render_prl_bilibili_cover.py --date 2026-04-28 --out cover.png
  /usr/bin/python3 render_prl_bilibili_cover.py --date 2026-04-28 --input-json input.json --out cover.png
"""

import argparse
import datetime as dt
import io
import json
import random
import re
import urllib.request
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


def boxes_overlap(a, b, *, pad: int = 10) -> bool:
    return not (
        a[2] + pad <= b[0]
        or b[2] + pad <= a[0]
        or a[3] + pad <= b[1]
        or b[3] + pad <= a[1]
    )


def draw_keyword_cloud(draw: ImageDraw.ImageDraw, keywords, seed: int):
    rnd = random.Random(seed)
    font = ImageFont.truetype(FONT_REG_PATH, 54)
    colors = [
        (93, 91, 255, 138),
        (0, 184, 163, 130),
        (122, 91, 255, 128),
        (74, 88, 112, 124),
    ]

    # Stable rows, but each row gets different loose x slots. This avoids the
    # "all rows line up vertically" look while keeping row spacing controlled.
    row_ys = [500, 590, 680, 770]
    row_slot_patterns = [
        [(230, 430), (650, 1085)],
        [(380, 610), (760, 1085)],
        [(250, 520), (570, 875)],
        [(455, 720), (820, 1095)],
    ]
    min_gap = 38
    placed = []

    for i, kw in enumerate(keywords):
        bbox = draw.textbbox((0, 0), kw, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        if text_w <= 0 or text_h <= 0:
            continue
        row_idx = (i // 2) % len(row_ys)
        slot_idx = i % 2
        slot_left, slot_right = row_slot_patterns[row_idx][slot_idx]
        max_x = max(slot_left, slot_right - text_w)
        preferred_y = row_ys[row_idx]

        candidates = []
        for _ in range(90):
            x = rnd.randint(slot_left, max_x)
            y = preferred_y + rnd.randint(-6, 6)
            box = (x, y, x + text_w, y + text_h)
            conflicts = sum(1 for p in placed if boxes_overlap(box, p["box"], pad=min_gap))
            crowd_penalty = 0
            vertical_alignment_penalty = 0
            for p in placed:
                px1, py1, px2, py2 = p["box"]
                pc = (px1 + px2) / 2
                c = (box[0] + box[2]) / 2
                if not (box[3] + min_gap <= py1 or py2 + min_gap <= box[1]):
                    crowd_penalty += max(0, min(box[2], px2) - max(box[0], px1) + min_gap)
                # Penalize repeated x-centers across rows; this is what made
                # the previous two-lane layout look insufficiently random.
                if abs(c - pc) < 120:
                    vertical_alignment_penalty += 120 - abs(c - pc)
            candidates.append((conflicts, crowd_penalty + vertical_alignment_penalty * 0.8 + abs(y - preferred_y), x, y, box))

        candidates.sort(key=lambda c: (c[0], c[1]))
        _, _, x, y, box = candidates[0]

        for _ in range(18):
            conflicts = [p for p in placed if boxes_overlap(box, p["box"], pad=min_gap)]
            if not conflicts:
                break
            nearest = min(conflicts, key=lambda p: abs((box[0] + box[2]) / 2 - (p["box"][0] + p["box"][2]) / 2))
            shift = 26 if (box[0] + box[2]) >= (nearest["box"][0] + nearest["box"][2]) else -26
            x = max(slot_left, min(max_x, x + shift))
            box = (x, y, x + text_w, y + text_h)
            if x in {slot_left, max_x}:
                break

        draw.text((x - bbox[0], y - bbox[1]), kw, fill=colors[i % len(colors)], font=font)
        placed.append({"box": box, "text": kw})


def parse_date_key(value: str) -> dt.date | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return dt.datetime.strptime(value[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def rss_dates_are_stale(data: dict, local_date: str, *, stale_days: int = 3) -> bool:
    local = parse_date_key(local_date or data.get("date") or "")
    if local is None:
        return False
    dates = [
        parse_date_key(data.get("feed_date_condensed") or data.get("target_date_condensed") or ""),
        parse_date_key(data.get("feed_date_recent") or data.get("target_date_recent") or ""),
    ]
    dates = [d for d in dates if d is not None]
    if not dates:
        paper_dates = [parse_date_key(p.get("rss_date") or "") for p in (data.get("papers") or [])]
        dates = [d for d in paper_dates if d is not None]
    return bool(dates) and all((local - d).days > stale_days for d in dates)


def choose_cover_image_url(input_json: str, *, paper_limit: int = 2, local_date: str = "") -> str:
    if not input_json:
        return ""
    try:
        data = json.loads(Path(input_json).read_text(encoding="utf-8"))
    except Exception:
        return ""
    papers = list(data.get("papers") or [])
    candidates = [(paper.get("rss_cover_image") or "").strip() for paper in papers if (paper.get("rss_cover_image") or "").strip()]
    if not candidates:
        return ""
    if rss_dates_are_stale(data, local_date or data.get("date") or ""):
        seed_text = f"{local_date or data.get('date') or ''}|stale-rss-cover|" + "|".join(candidates)
        return random.Random(seed_from_text(seed_text)).choice(candidates)
    for url in candidates[: max(0, paper_limit)]:
        if url:
            return url
    return candidates[0]


def load_cover_image(input_json: str, *, timeout: int = 12, local_date: str = "") -> Image.Image | None:
    image_url = choose_cover_image_url(input_json, local_date=local_date)
    if not image_url:
        return None
    opener = urllib.request.build_opener()
    opener.addheaders = [("User-Agent", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")]
    try:
        with opener.open(image_url, timeout=timeout) as resp:
            raw = resp.read()
        return Image.open(io.BytesIO(raw)).convert("RGBA")
    except Exception:
        return None


def paste_cover_image(img: Image.Image, art: Image.Image, box) -> None:
    x1, y1, x2, y2 = box
    box_w = max(1, x2 - x1)
    box_h = max(1, y2 - y1)
    src_w, src_h = art.size
    if src_w <= 0 or src_h <= 0:
        return
    resampling = getattr(Image, "Resampling", Image)
    lanczos = getattr(resampling, "LANCZOS", getattr(Image, "LANCZOS", 1))
    scale = max(box_w / src_w, box_h / src_h) * 1.18
    resized = art.resize((max(1, int(src_w * scale)), max(1, int(src_h * scale))), lanczos)
    left = max(0, (resized.width - box_w) // 2)
    top = max(0, (resized.height - box_h) // 2)
    cropped = resized.crop((left, top, left + box_w, top + box_h))
    mask = Image.new("L", (box_w, box_h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, box_w, box_h), radius=40, fill=255)
    panel = Image.new("RGBA", (box_w, box_h), (255, 255, 255, 0))
    panel.alpha_composite(cropped, (0, 0))
    img.paste(panel, (x1, y1), mask)


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
    left_x = x1 + 182
    left_w = 760

    draw_chip(d, "PHYSICAL REVIEW LETTERS", left_x, y1 + 52, chip_font)

    title = resolve_cover_title(input_json, title)
    title_y = y1 + 145
    for line in wrap_text(d, title, title_font, left_w)[:2]:
        d.text((left_x, title_y), line, fill=FG, font=title_font)
        title_y += 140

    d.text((left_x, title_y + 6), date.replace('-', '.'), fill=ACCENT_3, font=date_font)
    draw_keyword_cloud(d, extract_keywords(input_json, tags_file=tags_file), seed=seed + 17)

    image_box = (1200, 175, 1780, 885)
    d.rounded_rectangle(image_box, radius=46, fill=(244, 247, 255), outline=(220, 227, 241), width=3)
    cover_image = load_cover_image(input_json, local_date=date)
    if cover_image is not None:
        paste_cover_image(img, cover_image, image_box)
    else:
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
