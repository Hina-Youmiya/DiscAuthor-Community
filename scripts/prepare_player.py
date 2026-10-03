#!/usr/bin/env python3
"""Fetch a pinned official VLC arm64 runtime and the identified source materials.

No company-local dependency paths are needed. Does not install or run VLC.
The existing package_vlc helper selects modules and applies local ad-hoc signatures;
this is not Developer ID signing or notarization. Use --sources-only to collect
source/licenses without mounting a DMG or preparing binaries.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request


VERSION = '3.0.23'
DMG_NAME = f'vlc-{VERSION}-arm64.dmg'
DMG_URL = f'https://download.videolan.org/pub/videolan/vlc/{VERSION}/macosx/{DMG_NAME}'
DMG_SHA256 = 'fc6fac08d87f538517d44aca0c5e7a244b67c8c4cb589bf478363a7315fd5e0d'
SOURCE_NAME = f'vlc-{VERSION}.tar.xz'
SOURCE_URL = f'https://download.videolan.org/pub/videolan/vlc/{VERSION}/{SOURCE_NAME}'
SOURCE_SHA256 = 'e891cae6aa3ccda69bf94173d5105cbc55c7a7d9b1d21b9b21666e69eff3e7e0'
# Identified selected-plugin/static dependencies. This is a reviewed collection
# scope, not an automatic proof of complete corresponding source for every build.
RECIPE_PACKAGES = tuple('ffmpeg freetype2 harfbuzz fribidi bluray dvdnav dvdread dvdcss '
                       'png jpeg samplerate flac speexdsp mad mpg123 zlib libxml2 dvbpsi '
                       'gsm openjpeg lame ogg aribb24 faad2'.split())


def sha(path, algorithm='sha256'):
    h = hashlib.new(algorithm)
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def fetch(urls, target, expected, algorithm='sha256'):
    if target.exists():
        if sha(target, algorithm) != expected:
            raise RuntimeError(f'Cached file hash mismatch: {target}; remove only that bad cache file and retry.')
        return urls[0]
    target.parent.mkdir(parents=True, exist_ok=True)
    errors = []
    for url in urls:
        print(f'Downloading {url}', flush=True)
        with tempfile.NamedTemporaryFile(prefix=target.name + '.', suffix='.partial',
                                         dir=target.parent, delete=False) as stream:
            temporary = Path(stream.name)
            try:
                with urllib.request.urlopen(url, timeout=60) as response:
                    shutil.copyfileobj(response, stream)
                stream.flush()
                if sha(temporary, algorithm) != expected:
                    raise RuntimeError('downloaded hash does not match the pinned source/recipe')
                temporary.replace(target)
                return url
            except Exception as exc:
                errors.append(f'{url}: {exc}')
            finally:
                temporary.unlink(missing_ok=True)
    raise RuntimeError('\n'.join(errors))


def license_texts(archive, package):
    texts, entries = [], []
    with tarfile.open(archive) as source:
        for member in source:
            name = Path(member.name).name.lower()
            if not member.isfile() or member.size > 1024 * 1024:
                continue
            match = re.match(r'^(copying|license|licence|notice|copyright)([._-].*)?$', name)
            if not match and name not in ('ftl.txt', 'gplv2.txt', 'gplv3.txt') and not (package in ('jpeg', 'gsm') and name in ('readme', 'readme.txt')):
                continue
            data = source.extractfile(member).read()
            texts.append(f'\n{"=" * 78}\n{package} / {member.name}\n{"=" * 78}\n' + data.decode('utf-8', 'replace') + '\n')
            entries.append({'package': package, 'member': member.name,
                            'sha256': hashlib.sha256(data).hexdigest()})
    return texts, entries


def collect_sources(destination):
    destination.mkdir(parents=True, exist_ok=True)
    core = destination/SOURCE_NAME
    fetch([SOURCE_URL], core, SOURCE_SHA256)
    plan = []
    with tarfile.open(core) as source:
        for package in RECIPE_PACKAGES:
            prefix = f'vlc-{VERSION}/contrib/src/{package}/'
            sums = source.extractfile(prefix + 'SHA512SUMS').read().decode()
            for line in sums.splitlines():
                if not line.strip():
                    continue
                expected, name = line.split(None, 1)
                name = name.strip()
                if Path(name).name != name:
                    raise RuntimeError(f'Unexpected archive name in verified recipe: {name}')
                plan.append((package, name, expected))
        # Preserve build recipes, patches and build entry points without executing them.
        prefixes = tuple(f'vlc-{VERSION}/contrib/src/{p}/' for p in RECIPE_PACKAGES)
        extra = {'COPYING', 'COPYING.LIB', 'AUTHORS', 'THANKS', 'contrib/src/main.mak',
                 'contrib/bootstrap', 'extras/package/macosx/build.sh', 'extras/package/macosx/configure.sh'}
        for member in source:
            relative = Path(member.name).relative_to(f'vlc-{VERSION}')
            if not member.isfile() or (not member.name.startswith(prefixes) and str(relative) not in extra):
                continue
            if '..' in relative.parts:
                raise RuntimeError('Unsafe path in source archive.')
            target = destination/'recipes'/relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.extractfile(member).read())
    records = []
    texts, notices = license_texts(core, 'vlc')
    for package, name, expected in plan:
        urls = [f'https://download.videolan.org/pub/contrib/{package}/{name}']
        if package == 'bluray':
            urls.append(f'https://download.videolan.org/pub/videolan/libbluray/1.4.0/{name}')
        archive = destination/'archives'/name
        url = fetch(urls, archive, expected, 'sha512')
        records.append({'package': package, 'filename': name, 'download_url': url, 'status': 'verified',
                        'expected_sha512': expected, 'sha512': sha(archive, 'sha512'),
                        'sha256': sha(archive), 'bytes': archive.stat().st_size})
        found, entries = license_texts(archive, package)
        texts.extend(found)
        notices.extend(entries)
    manifest = {'vlc_version': VERSION, 'vlc_source_url': SOURCE_URL, 'vlc_source_sha256': SOURCE_SHA256,
                'completeness': 'Identified source collection; exact static linkage/build configuration still requires review. See docs/PLAYER_DEPENDENCIES.md.',
                'archives': records}
    (destination/'source-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    (destination/'license-manifest.json').write_text(json.dumps(notices, indent=2) + '\n')
    (destination/'THIRD_PARTY_LICENSES.txt').write_text(
        'VLC selected runtime: source-archive license texts. See source-manifest.json for scope/hashes.\n' + ''.join(texts))
    return manifest


def prepare_runtime(dmg, destination, source_materials):
    if destination.exists():
        raise RuntimeError(f'Refusing to replace an existing runtime: {destination}')
    from package_vlc import package_runtime
    if sys.platform != 'darwin':
        raise RuntimeError('Preparing a macOS runtime requires macOS. --sources-only works separately.')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='discauthor-vlc-mount-') as temporary:
        mountpoint = Path(temporary).resolve()/'volume'
        mountpoint.mkdir()
        result = subprocess.run(['/usr/bin/hdiutil', 'attach', '-readonly', '-nobrowse', '-noautoopen',
                                 '-mountpoint', str(mountpoint), '-plist', str(dmg)],
                                check=True, capture_output=True)
        entities = plistlib.loads(result.stdout).get('system-entities', [])
        mounted = any(item.get('mount-point') == str(mountpoint) for item in entities)
        try:
            if not mounted:
                raise RuntimeError('The DMG did not mount at the requested directory.')
            app = mountpoint/'VLC.app'
            if not app.is_dir():
                raise RuntimeError('Verified image does not contain VLC.app.')
            package_runtime(app, destination)
            licenses = destination/'licenses'
            shutil.copyfile(source_materials/'THIRD_PARTY_LICENSES.txt', licenses/'PLAYER-THIRD-PARTY-LICENSES.txt')
            shutil.copyfile(source_materials/'source-manifest.json', licenses/'PLAYER-SOURCE-MANIFEST.json')
            # This data's own LGPL notice is embedded in its SOFA License attribute.
            hrtf = destination/'share/hrtfs/dodeca_and_7channel_3DSL_HRTF.sofa'
            if hrtf.is_file():
                lines = subprocess.check_output(['/usr/bin/strings', str(hrtf)], text=True).splitlines()
                start = lines.index('License') + 1
                end = lines.index('Organization', start)
                (licenses/'HRTF-LICENSE.txt').write_text('\n'.join(lines[start:end]) + '\n')
            receipt = {'version': VERSION, 'official_dmg_url': DMG_URL, 'official_dmg_sha256': sha(dmg),
                       'files': [{'path': str(p.relative_to(destination)), 'bytes': p.stat().st_size, 'sha256': sha(p)}
                                 for p in sorted(destination.rglob('*')) if p.is_file() and not p.name.startswith('._')]}
            (destination/'preparation-receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
        finally:
            # Detach only the volume mounted by this operation, never another disk.
            if mounted:
                subprocess.run(['/usr/bin/hdiutil', 'detach', str(mountpoint)], check=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination', type=Path, default=Path('toolchain/deps/player-runtime'))
    parser.add_argument('--cache-dir', type=Path, default=Path('.cache/player-downloads'))
    parser.add_argument('--source-materials-dir', type=Path, default=Path('source-materials/player'))
    parser.add_argument('--sources-only', action='store_true', help='fetch source/licenses only; no DMG mount or binary preparation')
    args = parser.parse_args(argv)
    if not args.sources_only and args.destination.exists():
        parser.error('--destination already exists; select a new runtime directory.')
    manifest = collect_sources(args.source_materials_dir.expanduser().resolve())
    print(f'Collected {len(manifest["archives"])} source archives; completeness remains subject to the documented review.')
    if not args.sources_only:
        dmg = args.cache_dir.expanduser().resolve()/DMG_NAME
        fetch([DMG_URL], dmg, DMG_SHA256)
        prepare_runtime(dmg, args.destination.expanduser().resolve(), args.source_materials_dir.expanduser().resolve())
        print(f'Prepared {args.destination}. No VLC installation or Developer ID notarization was performed.')


if __name__ == '__main__':
    try:
        main()
    except (OSError, RuntimeError, ValueError, tarfile.TarError, subprocess.CalledProcessError) as exc:
        print(f'PLAYER PREPARATION STOPPED: {exc}', file=sys.stderr)
        sys.exit(2)
