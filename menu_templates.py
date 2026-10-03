"""Public-edition menu compatibility boundary.

General menus and user-supplied backgrounds are rendered by author.py.
Private template projects are rejected explicitly so their design is never
silently replaced with a different layout.
"""

IDS = ()
UNAVAILABLE = "公开版不包含私有菜单模板。请新建通用菜单或使用自己的背景图片。"


def settings(config, strict=False):
    if config.get('menu_template') is not None:
        raise ValueError(UNAVAILABLE)
    return None


def asset_paths(config):
    settings(config)
    return []


def rects(config, count=None):
    raise ValueError(UNAVAILABLE)


def render_background(config):
    raise ValueError(UNAVAILABLE)


def draw_button(image, button, config=None):
    raise ValueError(UNAVAILABLE)
