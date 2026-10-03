"""Apply DiscAuthor's complete, repeatable tsMuxer vendor patch.

Base revision: c6b1186209e42c877052e762c9185f3226ef8ea2.
Includes interactive menus, PCR continuity and checked ISO I/O. A preflight
prevents partial mutation; an already-patched source tree is a successful no-op.
"""
import argparse
from pathlib import Path
import subprocess


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=root/'vendor/tsMuxer')
    args = parser.parse_args()
    source = args.source.resolve(strict=True)
    patch = root/'scripts/tsmuxer-changes.patch'

    def check(reverse=False):
        command = ['git', 'apply', '--check']
        if reverse:
            command.append('--reverse')
        return subprocess.run(command+[str(patch)], cwd=source, capture_output=True, text=True)

    forward = check()
    if forward.returncode == 0:
        subprocess.run(['git', 'apply', str(patch)], cwd=source, check=True)
        print('Applied complete DiscAuthor tsMuxer patch')
    elif check(reverse=True).returncode == 0:
        print('Complete DiscAuthor tsMuxer patch already present')
    else:
        raise SystemExit('tsMuxer source differs from the pinned base or has partial patches; '
                         'no files changed. Restore revision c6b1186209e42c877052e762c9185f3226ef8ea2 '
                         'in a separate source directory and apply again.\n'+forward.stderr)


if __name__ == '__main__':
    main()
