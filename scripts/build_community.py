#!/usr/bin/env python3
"""Build a free, unnotarized Community app from the documented source inputs.

No Apple account is used. The application and all third-party sources must be
distributed with the applicable licenses; see THIRD_PARTY_NOTICES.md.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def write_provenance(work):
    tools = work/'toolchain'
    files = {}
    # Reuse may skip compilation, never evidence. An altered binary must not be
    # relabelled as a verified source build merely by refreshing this manifest.
    media = json.loads((work/'source-materials/ffmpeg/build-manifest.json').read_text())
    navigation = json.loads((work/'source-materials/navigation/manifest.json').read_text())
    authoring = json.loads((work/'source-materials/authoring/authoring-build-manifest.json').read_text())
    expected = {'bin/'+name: sha for name, sha in media['binary_sha256'].items()}
    expected.update({'bin/'+name: sha for name, sha in navigation['outputs'].items()})
    expected.update({item['path']: item['sha256'] for item in authoring['outputs']})
    sources = {
        'bin/ffmpeg': 'FFmpeg 9.0.2 + x264; source-materials/ffmpeg/build-manifest.json',
        'bin/ffprobe': 'FFmpeg 9.0.2 + x264; source-materials/ffmpeg/build-manifest.json',
        'bin/tsmuxer': 'tsMuxer pinned source + local patch; source-materials/navigation/manifest.json',
        'bin/reexport-m2ts': 'src/reexport_m2ts.c; GPL-3.0-only',
        'bin/mkisofs': 'cdrtools 3.02a09; scripts/build_authoring_tools.py',
        'deps/bin/dvdauthor': 'dvdauthor 0.7.2; scripts/build_authoring_tools.py',
        'deps/bin/spumux': 'dvdauthor 0.7.2; scripts/build_authoring_tools.py',
        'deps/lib/libbluray.2.dylib': 'libbluray 1.3.4; scripts/build_authoring_tools.py',
    }
    for name, source in sources.items():
        path = tools/name
        with path.open('rb') as stream:
            sha = hashlib.file_digest(stream, 'sha256').hexdigest()
        if expected.get(name) != sha:
            raise RuntimeError('Tool differs from its component build receipt: '+name)
        files[name] = {'source': source, 'sha256': sha}
    runtime = tools/'deps/player-runtime'
    if not (runtime/'lib/libvlc.dylib').is_file():
        raise RuntimeError('VLC runtime is missing')
    player_files = {}
    for path in sorted(runtime.rglob('*')):
        if path.is_file() and not path.name.startswith('._'):
            with path.open('rb') as stream:
                player_files[str(path.relative_to(runtime))] = hashlib.file_digest(stream, 'sha256').hexdigest()
    receipt = json.loads((runtime/'preparation-receipt.json').read_text())
    if receipt.get('official_dmg_sha256') != 'fc6fac08d87f538517d44aca0c5e7a244b67c8c4cb589bf478363a7315fd5e0d':
        raise RuntimeError('Playback runtime has no receipt for the pinned official image')
    expected_player = {item['path']: item['sha256'] for item in receipt['files']}
    recorded_player = {name: sha for name, sha in player_files.items() if name != 'preparation-receipt.json'}
    if recorded_player != expected_player:
        raise RuntimeError('Playback runtime differs from its preparation receipt')
    manifest = {'format_version': 1, 'files': files, 'player_files': player_files,
                'player_source': 'Official VLC 3.0.23 arm64; docs/PLAYER_DEPENDENCIES.md describes the source collection scope',
                'notarized': False}
    (tools/'provenance.json').write_text(json.dumps(manifest, indent=2)+'\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work-root', required=True, type=Path)
    parser.add_argument('--jobs', type=int, choices=(1, 2), default=2)
    parser.add_argument('--reuse-toolchain', action='store_true',
                        help='Package an already prepared toolchain; regenerate its local hashes')
    parser.add_argument('--manifest-only', action='store_true',
                        help='Record hashes for a prepared toolchain without compiling or packaging')
    args = parser.parse_args()
    work = args.work_root.expanduser().resolve()
    work.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, 'PATH': str(Path(sys.executable).parent)+os.pathsep+os.environ.get('PATH', '')}

    def run(script, *arguments):
        subprocess.run([sys.executable, '-B', str(ROOT/'scripts'/script), *map(str, arguments)], check=True, env=env)

    if not args.reuse_toolchain and not args.manifest_only:
        cmake = shutil.which('cmake', path=env['PATH'])
        if cmake is None:
            raise RuntimeError('Install requirements-build.txt in the active virtual environment first')
        run('build_ffmpeg.py', '--work-root', work, '--jobs', args.jobs)
        # The FFmpeg recipe creates this generic alias so no customer paths are
        # embedded in compiler/configuration strings.
        alias = Path('/tmp/discauthor-community-build')
        run('build_authoring_tools.py', '--sources', alias/'source-materials/authoring',
            '--work', alias, '--output', alias/'toolchain', '--cmake', cmake, '--jobs', args.jobs)
        run('build_navigation.py', '--work-root', alias, '--freetype-prefix', alias/'prefix',
            '--cmake', cmake, '--jobs', args.jobs)
        runtime = work/'toolchain/deps/player-runtime'
        if not runtime.exists():
            run('prepare_player.py', '--destination', runtime, '--cache-dir', work/'downloads/player',
                '--source-materials-dir', work/'source-materials/player')
    if not args.manifest_only:
        run('collect_python_licenses.py', '--output', work/'toolchain/licenses/python')
    write_provenance(work)
    if args.manifest_only:
        print(work/'toolchain/provenance.json')
        return
    env.update(DISCAUTHOR_TOOLCHAIN_DIR=str(work/'toolchain'), DISCAUTHOR_BUILD_DIR=str(work/'package'))
    subprocess.run([sys.executable, '-B', str(ROOT/'scripts/package_app.py')], check=True, env=env)


if __name__ == '__main__':
    main()
