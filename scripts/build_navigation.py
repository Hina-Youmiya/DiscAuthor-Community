#!/usr/bin/env python3
"""Build patched tsMuxer and the M2TS helper from recorded source inputs."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
COMMIT = 'c6b1186209e42c877052e762c9185f3226ef8ea2'
REPOSITORY = 'https://github.com/justdan96/tsMuxer.git'


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work-root', required=True, type=Path)
    parser.add_argument('--freetype-prefix', required=True, type=Path)
    parser.add_argument('--cmake', default='cmake')
    parser.add_argument('--jobs', type=int, default=2, choices=range(1, 5))
    args = parser.parse_args()
    work = Path(os.path.abspath(args.work_root))
    source = work/'navigation/source'
    build = work/'navigation/build'
    materials = work/'source-materials/navigation'
    output = work/'toolchain/bin'
    for directory in (materials, output, build):
        directory.mkdir(parents=True, exist_ok=True)
    os.setpriority(os.PRIO_PROCESS, 0, max(10, os.getpriority(os.PRIO_PROCESS, 0)))
    env = {**os.environ, 'MACOSX_DEPLOYMENT_TARGET': '15.0', 'COPYFILE_DISABLE': '1'}
    commands = []

    def run(argv, cwd=work, stdout=None):
        commands.append([str(arg) for arg in argv])
        subprocess.run([str(arg) for arg in argv], cwd=cwd, env=env, check=True, stdout=stdout)

    if not (source/'.git').is_dir():
        source.mkdir(parents=True, exist_ok=True)
        run(['git', 'init', source])
        run(['git', '-C', source, 'remote', 'add', 'origin', REPOSITORY])
    current = subprocess.run(['git', '-C', str(source), 'rev-parse', '--verify', 'HEAD'], capture_output=True, text=True)
    if current.returncode:
        # Resume a fresh checkout whose first network fetch was interrupted.
        run(['git', '-C', source, 'fetch', '--depth=1', REPOSITORY, COMMIT])
        run(['git', '-C', source, 'checkout', '--detach', COMMIT])
    elif current.stdout.strip() != COMMIT:
        raise RuntimeError('Navigation source checkout does not match the pinned commit')
    archive = materials/f'tsmuxer-{COMMIT}.tar.gz'
    run(['git', '-C', source, 'archive', '--format=tar.gz', '--prefix=tsmuxer/', '-o', archive, COMMIT])
    patch = ROOT/'scripts/tsmuxer-changes.patch'
    shutil.copyfile(patch, materials/patch.name)
    run([sys.executable, ROOT/'scripts/patch_tsmuxer.py', '--source', source])
    prefix = Path(os.path.abspath(args.freetype_prefix))
    if not (prefix/'lib/libfreetype.a').is_file():
        raise RuntimeError('Build the authoring dependencies first: static FreeType is missing')
    flags = '-O2 -g0 -mmacosx-version-min=15.0 -ffile-prefix-map='+str(source)+'=/build/tsmuxer'
    run([args.cmake, '-S', source, '-B', build, '-DCMAKE_BUILD_TYPE=Release',
         '-DCMAKE_OSX_ARCHITECTURES=arm64', '-DCMAKE_OSX_DEPLOYMENT_TARGET=15.0',
         '-DCMAKE_POLICY_VERSION_MINIMUM=3.5', '-DTSMUXER_GUI=OFF',
         '-DWITHOUT_PKGCONFIG=ON', '-DCMAKE_PREFIX_PATH='+str(prefix),
         '-DCMAKE_CXX_FLAGS='+flags, '-DCMAKE_C_FLAGS='+flags])
    run([args.cmake, '--build', build, '--parallel', str(args.jobs)])
    shutil.copyfile(build/'tsMuxer/tsmuxer', output/'tsmuxer')
    (output/'tsmuxer').chmod(0o755)
    helper_source = materials/'reexport_m2ts.c'
    shutil.copyfile(ROOT/'src/reexport_m2ts.c', helper_source)
    run(['/usr/bin/clang', '-O2', '-g0', '-mmacosx-version-min=15.0',
         '-Wno-deprecated-declarations', helper_source, '-o', output/'reexport-m2ts'])
    for name in ('tsmuxer', 'reexport-m2ts'):
        run(['/usr/bin/strip', '-x', output/name])
        run(['/usr/bin/codesign', '--force', '--sign', '-', output/name])
    licenses = work/'toolchain/licenses/navigation'
    licenses.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source/'LICENSE', licenses/'tsMuxer-LICENSE.txt')
    result = {'repository': REPOSITORY, 'commit': COMMIT, 'source_archive': archive.name,
              'source_sha256': digest(archive), 'patch_sha256': digest(patch),
              'outputs': {name: digest(output/name) for name in ('tsmuxer', 'reexport-m2ts')}}
    (materials/'manifest.json').write_text(json.dumps(result, indent=2)+'\n')
    (work/'navigation/commands.json').write_text(json.dumps(commands, indent=2)+'\n')


if __name__ == '__main__':
    main()
