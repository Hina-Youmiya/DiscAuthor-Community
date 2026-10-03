"""One DVD-compatible button mask shared by previews, DVD SPU and Blu-ray IGS.

Normal leaves the authored menu visible; selected and activated recolour the
same region. Opacity uses DVD's 4-bit alpha steps so previews and both formats
use identical effective opacity.
"""
import re
from PIL import Image, ImageDraw
from menu_text import button_text_mask, button_outline_mask, text_style_config

DEFAULT_FEEDBACK = {
    'style': 'fill',
    'selected_color': '#ffff00',
    'activated_color': '#ff0000',
    'opacity': 0.5,
    'region_enabled': True,
    'text_enabled': False,
    'selected_text_color': '#ffff00',
    'activated_text_color': '#ff0000',
}
STATES = ('normal', 'selected', 'activated')


def feedback_config(config=None):
    raw = (config or {}).get('button_feedback', {})
    if not isinstance(raw, dict):
        raise ValueError('button_feedback must be an object')
    result = {**DEFAULT_FEEDBACK, **{k: v for k, v in raw.items() if k in DEFAULT_FEEDBACK}}
    if result['style'] not in ('fill', 'frame'):
        raise ValueError('button_feedback.style must be fill or frame')
    for key in ('region_enabled', 'text_enabled'):
        if type(result[key]) is not bool:
            raise ValueError(f'button_feedback.{key} must be a boolean')
    for key in ('selected_color', 'activated_color', 'selected_text_color', 'activated_text_color'):
        if not isinstance(result[key], str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', result[key]):
            raise ValueError(f'button_feedback.{key} must be a hex RGB colour')
        result[key] = result[key].lower()
    value = result['opacity']
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0.1 <= value <= 1:
        raise ValueError('button_feedback.opacity must be between 0.1 and 1')
    return result


def palette_rgba(config=None):
    style = feedback_config(config)
    alpha = round(style['opacity'] * 15) * 17
    def colour(key, opacity=alpha):
        value = style[key]
        return tuple(int(value[i:i+2], 16) for i in (1, 3, 5)) + (opacity,)
    outline = text_style_config(config)['outline_color']
    outline_rgba = tuple(int(outline[i:i+2],16) for i in (1,3,5)) + (255,)
    return [(0, 0, 0, 0), colour('selected_color'), colour('activated_color'),
            colour('selected_text_color', 255), colour('activated_text_color', 255), outline_rgba]


def button_indexed(button, state, config=None):
    """Return a palette-index bitmap in button-local HD coordinates."""
    if state not in STATES:
        raise ValueError(f'Unknown feedback state: {state}')
    _, _, width, height = button['rect']
    image = Image.new('P', (width, height), 0)
    if state == 'normal':
        return image
    style = feedback_config(config)
    index = STATES.index(state)
    # The same geometry is used in both non-normal states. DVD changes palette
    # on activation rather than replacing its subpicture bitmap.
    draw = ImageDraw.Draw(image)
    bounds = (0, 0, width - 1, height - 1)
    if style['region_enabled']:
        if style['style'] == 'fill':
            draw.rectangle(bounds, fill=index)
        else:
            draw.rounded_rectangle(bounds, radius=min(18, height // 4), outline=index, width=6)
    if style['text_enabled']:
        # DVD permits four simultaneous subpicture colours: transparent,
        # region, glyph and outline. Geometry stays the same across states.
        outline = button_outline_mask(button, config).point(lambda value: 255 if value else 0)
        image.paste(5, (0, 0), outline)
        mask = button_text_mask(button, config).point(lambda value: 255 if value else 0)
        image.paste(index + 2, (0, 0), mask)
    return image


def button_overlay(button, state, config=None, size=None):
    """RGBA texture for a preview hotspot, generated from the disc mask."""
    indexed = button_indexed(button, state, config)
    palette = palette_rgba(config)
    image = Image.new('RGBA', indexed.size)
    # Indexed lookup preserves the tiny DVD subpicture palette (no antialiasing).
    image.putdata([palette[p] for p in indexed.getdata()])
    if size is not None and tuple(size) != image.size:
        image = image.resize(tuple(size), Image.Resampling.NEAREST)
    return image


def dvd_button_rect(button, height):
    x, y, w, h = button['rect']
    return tuple(round(v) // 2 * 2 for v in (x * 720 / 1920, y * height / 1080,
                                            (x+w) * 720 / 1920, (y+h) * height / 1080))


def page_overlay(buttons, state, config=None, size=(1920, 1080), active_index=None):
    """Transparent full-page layer; active_index=None includes all button masks."""
    image = Image.new('RGBA', tuple(size), (0, 0, 0, 0))
    for i, button in enumerate(buttons):
        if active_index is not None and i != active_index:
            continue
        x, y, w, h = button['rect']
        if size[0] == 720 and size[1] in (480, 576):
            x, y, right, bottom = dvd_button_rect(button, size[1])
            # spumux button x1/y1 are exclusive. A +1 edge escapes its colour
            # remapping and can become a visible text-coloured line on DVD.
            w, h = right - x, bottom - y
        elif tuple(size) != (1920, 1080):
            x, y, w, h = (round(x*size[0]/1920), round(y*size[1]/1080),
                           round(w*size[0]/1920), round(h*size[1]/1080))
        # Paste without a mask: the source alpha must not be multiplied twice.
        image.paste(button_overlay(button, state, config, (w, h)), (x, y))
    return image


def bd_palette(config=None):
    """Blu-ray palette entries in HD BT.709 limited-range Y/Cr/Cb/alpha."""
    result = bytearray(b'\0\0')  # palette id and version
    for index, (r, g, b, alpha) in enumerate(palette_rgba(config)):
        y = round(16 + (0.2126*r + 0.7152*g + 0.0722*b) * 219 / 255)
        cb = round(128 + (-0.114572*r - 0.385428*g + 0.5*b) * 224 / 255)
        cr = round(128 + (0.5*r - 0.454153*g - 0.045847*b) * 224 / 255)
        result.extend((index, y, cr, cb, alpha))
    return bytes(result)
