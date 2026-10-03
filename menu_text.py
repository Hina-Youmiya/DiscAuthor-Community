"""Shared local-font label layout for the menu video and button colour masks."""
from functools import lru_cache
from pathlib import Path
import re
from PIL import Image, ImageDraw, ImageFont
from fonts import resolve_font

FONT = Path('/System/Library/Fonts/Hiragino Sans GB.ttc')
FALLBACKS = (FONT, Path('/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc'),
             Path('/System/Library/Fonts/Supplemental/Arial Unicode.ttf'))
DEFAULT_TEXT_STYLE = {'color': '#ffffff', 'outline_enabled': False,
                      'outline_color': '#ffffff', 'outline_width': 4}


def text_style_config(config=None):
    raw = (config or {}).get('text_style', {})
    if not isinstance(raw, dict):
        raise ValueError('text_style must be an object')
    style = {**DEFAULT_TEXT_STYLE, **{k:v for k,v in raw.items() if k in DEFAULT_TEXT_STYLE}}
    for key in ('color','outline_color'):
        if not isinstance(style[key], str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', style[key]):
            raise ValueError(f'text_style.{key} must be a hex RGB colour')
        style[key] = style[key].lower()
    if type(style['outline_enabled']) is not bool:
        raise ValueError('text_style.outline_enabled must be a boolean')
    if type(style['outline_width']) is not int or not 1 <= style['outline_width'] <= 12:
        raise ValueError('Text outline width must be between 1 and 12')
    return style


@lru_cache(maxsize=128)
def local_font(path, size, index=0):
    return ImageFont.truetype(str(resolve_font(path)), size, index=index)


def fit_text(draw, text, width, size, font_spec=None):
    spec = font_spec or {'path': str(FONT), 'index': 0}
    font = local_font(spec['path'], size, int(spec.get('index', 0)))
    while draw.textlength(text, font=font) > width and len(text) > 1:
        text = text[:-2] + '…'
    return text, font


@lru_cache(maxsize=16384)
def supports_character(path, index, character):
    if character.isspace():
        return True
    font = local_font(path, 32, index)
    glyph, missing = font.getmask(character), font.getmask(chr(0x10ffff))
    return glyph.size != missing.size or bytes(glyph) != bytes(missing)


def label_runs(text, spec, size):
    """Use the chosen face, falling back only for glyphs absent from that face."""
    primary = (spec['path'], int(spec.get('index', 0)))
    fallbacks = [(str(path), 0) for path in FALLBACKS if path.is_file()]
    runs = []
    for character in text:
        face = primary
        if not supports_character(*primary, character):
            face = next((candidate for candidate in fallbacks
                         if supports_character(*candidate, character)), primary)
        if runs and runs[-1][0] == face:
            runs[-1] = (face, runs[-1][1] + character)
        else:
            runs.append((face, character))
    return [(part, local_font(path, size, index)) for (path, index), part in runs]


def button_text_mask(button, config=None, stroke_width=0):
    """Antialiased grayscale text, centred in the exact authored button rectangle."""
    config = config or {}
    _, _, width, height = button['rect']
    mask = Image.new('L', (width, height), 0)
    draw = ImageDraw.Draw(mask)
    spec = config.get('font') or {'path': str(FONT), 'index': 0}
    size = int(config.get('font_size', 42))
    if config.get('menu_template') is not None:
        style = text_style_config(config)
        outline = style['outline_width'] if style['outline_enabled'] else 0
        # Reserve both stroke edges. Use the project style (not stroke_width)
        # so normal glyphs and selected/activated masks keep identical geometry.
        padding = max(8, outline+2)
        size = min(size, max(16,height-2*padding))
    text = str(button['label'])
    # Preserve the user's string in the project; truncate only its rendered label.
    while True:
        runs = label_runs(text, spec, size)
        if sum(draw.textlength(part, font=font) for part, font in runs) <= width - 40 or len(text) <= 1:
            break
        text = text[:-2] + '…'
    if not runs:
        return mask
    pen = 0
    boxes = []
    for part, font in runs:
        left, top, right, bottom = draw.textbbox((pen, 0), part, font=font, anchor='ls')
        boxes.append((left, top, right, bottom))
        pen += draw.textlength(part, font=font)
    left = min(b[0] for b in boxes); top = min(b[1] for b in boxes)
    right = max(b[2] for b in boxes); bottom = max(b[3] for b in boxes)
    x = (width - (right-left))/2 - left
    baseline = (height - (bottom-top))/2 - top
    for part, font in runs:
        draw.text((x, baseline), part, font=font, fill=255, anchor='ls',
                  stroke_width=stroke_width, stroke_fill=255)
        x += draw.textlength(part, font=font)
    return mask


def button_outline_mask(button, config=None):
    """Full text silhouette including the outline; glyph fill is painted last."""
    style = text_style_config(config)
    if not style['outline_enabled']:
        return Image.new('L', tuple(button['rect'][2:]), 0)
    return button_text_mask(button, config, stroke_width=style['outline_width'])
