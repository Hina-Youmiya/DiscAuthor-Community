#!/usr/bin/env python3
"""Collect installed build/runtime Python licenses without importing application code.

Run with the same Python environment used by PyInstaller. CMake and pip are
build tools and are not collected as distributed application dependencies.
Native library source/license completeness is a separate review.
"""
from __future__ import annotations

import argparse
import importlib.metadata as metadata
import json
from pathlib import Path
import re
import sys


ROOTS = ('Flask', 'pillow', 'waitress', 'pyinstaller', 'pyinstaller-hooks-contrib')


def canonical(name):
    return re.sub(r'[-_.]+', '-', name).lower()


def collect(output):
    from packaging.markers import default_environment
    from packaging.requirements import Requirement
    environment = default_environment()
    environment['extra'] = ''
    installed = {}
    for distribution in metadata.distributions():
        # exFAT AppleDouble directories are not distribution metadata.
        if Path(str(getattr(distribution, '_path', ''))).name.startswith('._'):
            continue
        name = distribution.metadata.get('Name')
        if name:
            installed[canonical(name)] = distribution
    closure, queue = {}, list(ROOTS)
    while queue:
        name = canonical(queue.pop())
        if name in closure:
            continue
        if name not in installed:
            raise RuntimeError(f'Missing build dependency: {name}; install requirements-lock.txt first.')
        distribution = installed[name]
        closure[name] = distribution
        for requirement in distribution.requires or ():
            parsed = Requirement(requirement)
            if parsed.marker and not parsed.marker.evaluate(environment):
                continue
            dependency = installed.get(canonical(parsed.name))
            if dependency is None or dependency.version not in parsed.specifier:
                raise RuntimeError(f'Unsatisfied dependency: {name}: {requirement}')
            queue.append(parsed.name)
    output.mkdir(parents=True, exist_ok=True)
    records = []

    def save(distribution, kind):
        name = distribution.metadata['Name']
        dist_path = Path(distribution._path)
        target = output/kind/(name + '-' + distribution.version)
        copied = []
        for source in sorted(dist_path.rglob('*')):
            if not source.is_file() or source.name.startswith('._'):
                continue
            if not any(word in source.name.lower() for word in ('license', 'licence', 'copying', 'notice', 'copyright')):
                continue
            relative = source.relative_to(dist_path)
            destination = target/relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(source.read_bytes())
            copied.append(str(relative))
        if not copied:
            raise RuntimeError(f'No license text found for {name} {distribution.version}; inspect the installed distribution.')
        records.append({'name': name, 'version': distribution.version, 'kind': kind,
                        'license': distribution.metadata.get('License-Expression', distribution.metadata.get('License')),
                        'license_files': copied})

    for name, distribution in sorted(closure.items()):
        save(distribution, 'distribution')
        if name == 'setuptools':
            vendor = Path(distribution.locate_file('setuptools/_vendor'))
            for dist_path in sorted(vendor.glob('*.dist-info')):
                if not dist_path.name.startswith('._'):
                    save(metadata.PathDistribution(dist_path), 'setuptools-vendored')
            header = vendor/'autocommand/__init__.py'
            if header.is_file():
                (output/'autocommand-copyright-header.txt').write_text('\n'.join(header.read_text().splitlines()[:18]) + '\n')
    version = f'{sys.version_info.major}.{sys.version_info.minor}'
    candidates = [Path(sys.base_prefix)/f'lib/python{version}/LICENSE.txt',
                  Path(sys.base_prefix)/'LICENSE.txt', Path(sys.base_prefix)/'LICENSE']
    python_license = next((p for p in candidates if p.is_file()), None)
    if python_license is None:
        raise RuntimeError('CPython LICENSE text was not found in this runtime; collect the matching Python source license before packaging.')
    (output/f'CPython-{sys.version.split()[0]}-LICENSE.txt').write_bytes(python_license.read_bytes())
    manifest = {'python_version': sys.version.split()[0], 'distributions': records,
                'scope': 'Installed active Python dependency closure plus setuptools vendors and CPython license. Native dependencies need separate source/license review.'}
    (output/'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps({'output': str(output), 'python': manifest['python_version'],
                      'active_distributions': len(closure), 'license_groups': len(records)}))
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    collect(args.output.expanduser().resolve())


if __name__ == '__main__':
    try:
        main()
    except (OSError, RuntimeError, ValueError) as exc:
        print(f'PYTHON LICENSE COLLECTION STOPPED: {exc}', file=sys.stderr)
        sys.exit(2)
