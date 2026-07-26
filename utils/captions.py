import os
from PIL import Image, ImageDraw, ImageFont

# Common font locations across Linux (Render/servers), Windows, and macOS.
FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "C:\\Windows\\Fonts\\arialbd.ttf",
    "C:\\Windows\\Fonts\\arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
]

HIGHLIGHT_COLOR = (255, 75, 62, 235)   # kept for backward compatibility, unused by the new box style
TEXT_COLOR = (255, 255, 255, 255)

# Reference-style palette (cream background, near-black text, muted terracotta accent)
CARD_BG = (245, 241, 236, 255)
CARD_TEXT = (30, 26, 20, 255)
CARD_SUBTEXT = (140, 130, 118, 255)
CARD_ACCENT = (214, 129, 84, 255)
CARD_BORDER = (30, 26, 20, 255)

# Color emoji fonts to try, in order — availability varies by OS.
EMOJI_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",   # Linux (Render, after Aptfile install)
    "C:\\Windows\\Fonts\\seguiemj.ttf",                     # Windows (Segoe UI Emoji)
    "/System/Library/Fonts/Apple Color Emoji.ttc",          # macOS
]


def render_emoji_png(emoji_char, size, out_path):
    """
    Render a single emoji character to a transparent square PNG using a
    color emoji font. Returns True on success, False if no usable color
    emoji font/glyph was found on this machine — callers should just skip
    decoration in that case rather than fail the whole video.
    """
    for path in EMOJI_FONT_CANDIDATES:
        if not os.path.isfile(path):
            continue
        try:
            font_size = int(size * 0.9)
            font = ImageFont.truetype(path, font_size)
            img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            draw.text((size * 0.05, size * 0.05), emoji_char, font=font, embedded_color=True)
            if img.getbbox() is None:
                continue  # font loaded but had no glyph for this character
            img.save(out_path)
            return True
        except Exception:
            continue
    return False


def _load_font(size: int):
    for path in FONT_CANDIDATES:
        if os.path.isfile(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _wrap_text(draw, text, font, max_width):
    words = text.split()
    lines, current = [], ""
    for w in words:
        trial = (current + " " + w).strip()
        bbox = draw.textbbox((0, 0), trial, font=font)
        if bbox[2] - bbox[0] <= max_width or not current:
            current = trial
        else:
            lines.append(current)
            current = w
    if current:
        lines.append(current)
    return lines


def make_caption_overlay(text, width, height, out_path, font_size=54, margin=70, position="bottom"):
    """
    Render word-wrapped caption text onto a transparent PNG the same size
    as the video, with a semi-transparent readability band behind it.
    Used as a static fallback (e.g. when no per-word timing is available).
    """
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    font = _load_font(font_size)

    max_text_width = width - 2 * margin
    lines = _wrap_text(draw, text.strip(), font, max_text_width) if text.strip() else []
    if not lines:
        img.save(out_path)
        return out_path

    line_heights = [draw.textbbox((0, 0), l, font=font)[3] - draw.textbbox((0, 0), l, font=font)[1] for l in lines]
    line_spacing = 14
    total_h = sum(line_heights) + line_spacing * (len(lines) - 1)

    if position == "top":
        y = margin + 20
    elif position == "center":
        y = (height - total_h) // 2
    else:
        y = height - margin - total_h - 90

    pad = 26
    band_top = max(int(y - pad), 0)
    band_bottom = min(int(y + total_h + pad), height)
    draw.rectangle([(0, band_top), (width, band_bottom)], fill=(0, 0, 0, 150))

    cy = y
    for i, line in enumerate(lines):
        bbox = draw.textbbox((0, 0), line, font=font)
        w = bbox[2] - bbox[0]
        x = (width - w) // 2
        draw.text((x, cy), line, font=font, fill=(255, 255, 255, 255))
        cy += line_heights[i] + line_spacing

    img.save(out_path)
    return out_path


def make_word_chunk_overlay(text, width, height, out_path, font_size=None, position="bottom"):
    """
    Render a short (1-2 word) caption chunk as a bold bordered box with an
    offset accent-colored shadow — matches the reference video style (a
    cream box, black border, terracotta drop-shadow, bold dark text).
    This is what gets timed to appear/disappear in sync with the speech.
    """
    font_size = font_size or int(height * 0.05)
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    font = _load_font(font_size)

    text = text.strip().upper()
    if not text:
        img.save(out_path)
        return out_path

    bbox = draw.textbbox((0, 0), text, font=font)
    text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]

    pad_x, pad_y = int(font_size * 0.6), int(font_size * 0.4)
    box_w, box_h = text_w + pad_x * 2, text_h + pad_y * 2
    cx = width // 2
    if position == "top":
        box_top = int(height * 0.10)
    elif position == "center":
        box_top = (height - box_h) // 2
    else:
        box_top = int(height * 0.80)

    box_left = cx - box_w // 2
    shadow_offset = max(int(box_h * 0.12), 4)

    # Offset accent-colored drop shadow, then the bordered cream box on top
    draw.rectangle(
        [box_left + shadow_offset, box_top + shadow_offset, box_left + box_w + shadow_offset, box_top + box_h + shadow_offset],
        fill=CARD_ACCENT,
    )
    draw.rectangle([box_left, box_top, box_left + box_w, box_top + box_h], fill=CARD_BG, outline=CARD_BORDER, width=3)

    text_x = box_left + pad_x - bbox[0]
    text_y = box_top + pad_y - bbox[1]
    draw.text((text_x, text_y), text, font=font, fill=CARD_TEXT)

    img.save(out_path)
    return out_path


def make_text_panel(text, width, height, out_path, bg_color=CARD_BG):
    """
    Render a solid-color panel (half the frame) with bold centered,
    word-wrapped text — used as the 'text side' when an image sits on the
    other side of a split-layout slide.
    """
    img = Image.new("RGB", (width, height), bg_color[:3])
    draw = ImageDraw.Draw(img)

    font_size = int(height * 0.075)
    font = _load_font(font_size)
    max_w = int(width * 0.82)

    lines = _wrap_text(draw, text.strip(), font, max_w) if text.strip() else []
    line_heights = [draw.textbbox((0, 0), l, font=font)[3] - draw.textbbox((0, 0), l, font=font)[1] for l in lines]
    line_spacing = int(font_size * 0.3)
    total_h = sum(line_heights) + line_spacing * max(len(lines) - 1, 0)

    cy = (height - total_h) // 2
    for i, line in enumerate(lines):
        bbox = draw.textbbox((0, 0), line, font=font)
        w = bbox[2] - bbox[0]
        x = (width - w) // 2
        draw.text((x, cy), line, font=font, fill=CARD_TEXT[:3])
        cy += line_heights[i] + line_spacing

    img.save(out_path)
    return out_path


def make_title_card(headline, width, height, out_path, subtitle=None, bg_color=CARD_BG):
    """
    Render a full-frame typographic "title card": bold centered headline
    (word-wrapped), optional muted subtitle beneath. Used as a generated
    background slide when the user wants a text-driven look instead of
    photos (reference style — no images at all, just big bold statements).
    """
    img = Image.new("RGB", (width, height), bg_color[:3])
    draw = ImageDraw.Draw(img)

    headline_size = int(height * 0.09)
    font = _load_font(headline_size)
    max_w = int(width * 0.78)

    lines = _wrap_text(draw, headline.strip(), font, max_w)
    line_heights = [draw.textbbox((0, 0), l, font=font)[3] - draw.textbbox((0, 0), l, font=font)[1] for l in lines]
    line_spacing = int(headline_size * 0.25)
    total_h = sum(line_heights) + line_spacing * max(len(lines) - 1, 0)

    sub_h = 0
    sub_font = None
    if subtitle:
        sub_size = int(height * 0.035)
        sub_font = _load_font(sub_size)
        sub_bbox = draw.textbbox((0, 0), subtitle, font=sub_font)
        sub_h = (sub_bbox[3] - sub_bbox[1]) + int(height * 0.03)

    start_y = (height - total_h - sub_h) // 2
    cy = start_y
    for i, line in enumerate(lines):
        bbox = draw.textbbox((0, 0), line, font=font)
        w = bbox[2] - bbox[0]
        x = (width - w) // 2
        draw.text((x, cy), line, font=font, fill=CARD_TEXT[:3])
        cy += line_heights[i] + line_spacing

    if subtitle and sub_font:
        cy += int(height * 0.02)
        bbox = draw.textbbox((0, 0), subtitle, font=sub_font)
        w = bbox[2] - bbox[0]
        x = (width - w) // 2
        draw.text((x, cy), subtitle, font=sub_font, fill=CARD_SUBTEXT[:3])

    img.save(out_path)
    return out_path


def make_speaker_badge(label, width, height, out_path, active=True):
    """
    Small corner badge (e.g. 'TEACHER' / 'STUDENT') used to indicate which
    speaker is currently talking in 2-voice dialogue mode. Active speaker
    gets the bold bordered/shadowed treatment; inactive gets a faint
    outline-only version so it visually recedes.
    """
    pad_x, pad_y = 18, 12
    font_size = int(height * 0.5)
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    font = _load_font(font_size)

    text = label.strip().upper()
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    box_w, box_h = text_w + pad_x * 2, text_h + pad_y * 2
    box_left, box_top = 4, 4

    if active:
        shadow_offset = 5
        draw.rectangle(
            [box_left + shadow_offset, box_top + shadow_offset, box_left + box_w + shadow_offset, box_top + box_h + shadow_offset],
            fill=CARD_ACCENT,
        )
        draw.rectangle([box_left, box_top, box_left + box_w, box_top + box_h], fill=CARD_BG, outline=CARD_BORDER, width=3)
        text_fill = CARD_TEXT
    else:
        draw.rectangle([box_left, box_top, box_left + box_w, box_top + box_h], fill=(0, 0, 0, 0), outline=(120, 112, 102, 160), width=2)
        text_fill = (120, 112, 102, 180)

    draw.text((box_left + pad_x - bbox[0], box_top + pad_y - bbox[1]), text, font=font, fill=text_fill)
    img.save(out_path)
    return out_path