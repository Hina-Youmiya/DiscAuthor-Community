"""Enumerate local TrueType/OpenType fonts, including TTC collection faces."""
from pathlib import Path
from PIL import ImageFont
import json
from paths import ROOT
FOLDERS=[Path('/System/Library/Fonts'),Path('/Library/Fonts'),Path.home()/'Library/Fonts']

BUILTIN_FONT='builtin:mochiypop-p'
SYSTEM_SANS_FONT='builtin:system-sans'
BUNDLED_FONTS={
    BUILTIN_FONT:('MochiyPopP-Regular.otf','Mochiy Pop P · Regular'),
}

def resolve_font(path):
    value=str(path)
    if value==SYSTEM_SANS_FONT:
        # Resolve a system sans-serif without redistributing macOS font files.
        return next((p for p in (Path('/System/Library/Fonts/Supplemental/Arial.ttf'),
                                Path('/System/Library/Fonts/Helvetica.ttc')) if p.is_file()),
                    Path('/System/Library/Fonts/Helvetica.ttc'))
    if value in BUNDLED_FONTS:return ROOT/'assets/fonts'/BUNDLED_FONTS[value][0]
    if value.startswith('builtin:'):raise ValueError('Unknown bundled font')
    return Path(path)

def bundled():
    result=[{'label':label,'path':alias,'index':0} for alias,(_,label) in BUNDLED_FONTS.items() if resolve_font(alias).is_file()]
    if resolve_font(SYSTEM_SANS_FONT).is_file():
        result.append({'label':'Arial · Regular (macOS)','path':SYSTEM_SANS_FONT,'index':0})
    return result

def installed():
    fonts=bundled();seen=set()
    for directory in FOLDERS:
        if not directory.exists():continue
        for path in sorted(directory.rglob('*')):
            if path.name.startswith('.') or path.suffix.lower() not in {'.ttf','.otf','.ttc','.otc'}:continue
            for index in range(100 if path.suffix.lower() in {'.ttc','.otc'} else 1):
                try:
                    font=ImageFont.truetype(str(path),20,index=index);family,style=font.getname()
                except OSError:break
                label=f'{family} · {style}'
                key=(str(path),index)
                if key not in seen:fonts.append({'label':label,'path':str(path),'index':index});seen.add(key)
    return sorted(fonts,key=lambda f:f['label'].casefold())

if __name__=='__main__':
    result=installed();(ROOT/'assets/fonts.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(f'{len(result)} local font faces indexed')
