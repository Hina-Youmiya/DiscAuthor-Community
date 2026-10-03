"""Translate application messages, preserving interpolated filenames and titles."""
import json,re
from paths import ROOT
CATALOG=json.loads((ROOT/'web/translations.json').read_text())
def translate(message,language='ja',**values):
    language=language if language in ('ja','zh','en') else 'ja'
    if message in CATALOG:
        result=CATALOG[message][language]
        return re.sub(r'\{(\w+)\}',lambda m:str(values.get(m[1],m[0])),result)
    for key,translations in CATALOG.items():
        names=re.findall(r'\{(\w+)\}',key)
        if not names:continue
        pattern=re.escape(key)
        for name in names:pattern=pattern.replace(re.escape('{'+name+'}'),'(.*?)',1)
        match=re.fullmatch(pattern,message,re.S)
        if match:
            params=dict(zip(names,match.groups()))
            return re.sub(r'\{(\w+)\}',lambda m:params.get(m[1],m[0]),translations[language])
    return message
