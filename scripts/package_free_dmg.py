#!/usr/bin/env python3
"""Make an unnotarized drag-to-Applications DMG; no paid certificate required.

Input must already pass a strict ad-hoc signature check. This script modifies
only a fresh staging copy and writes a new image plus checksum.
"""
import argparse
import hashlib
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    app = args.app.resolve()
    output = args.output.resolve()
    if not app.is_dir() or app.suffix != '.app':
        parser.error('--app must be an existing .app bundle')
    if output.exists() or output.suffix.lower() != '.dmg':
        parser.error('--output must be a new .dmg file')
    subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(app)], check=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='discauthor-community-dmg-', dir='/private/tmp') as directory:
        staging = Path(directory)/'image'
        staging.mkdir()
        destination = staging/app.name
        # Preserve PyInstaller's resource symlinks exactly; copying from exFAT
        # with ditto --norsrc can invalidate the already sealed bundle.
        shutil.copytree(app, destination, symlinks=True,
                        ignore=shutil.ignore_patterns('._*'))
        subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(destination)], check=True)
        (staging/'Applications').symlink_to('/Applications')
        shutil.copyfile(ROOT/'docs/INSTALL_UNSIGNED.md', staging/'READ ME - Installation.txt')
        shutil.copyfile(ROOT/'LICENSE', staging/'LICENSE.txt')
        subprocess.run(['/usr/bin/hdiutil', 'create', '-volname', 'DiscAuthor Community',
                        '-srcfolder', str(staging), '-format', 'UDZO', '-ov', str(output)], check=True)
    subprocess.run(['/usr/bin/hdiutil', 'verify', str(output)], check=True)
    with output.open('rb') as stream:
        checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
    output.with_suffix(output.suffix+'.sha256').write_text(checksum+'  '+output.name+'\n')
    print(f'Unnotarized image: {output}')
    print(f'SHA-256: {checksum}')


if __name__ == '__main__':
    main()
