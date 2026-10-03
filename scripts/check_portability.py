#!/usr/bin/env python3
"""Read-only bundle scan: Mach-O dependencies/rpaths, embedded paths, ZIP/PYZ data.

Run with the pinned build Python so PyInstaller's archive reader is available.
No bundled executable is launched, no signatures/xattrs are changed, and no disk
is mounted. JSON is printed, or saved to --report outside the inspected bundle.
Raw upstream build paths are warnings; --private-token matches are errors.
Exit 0: completed without errors, 1: findings/coverage gaps, 2: invocation failure.
"""
from __future__ import annotations

import argparse
import getpass
import io
import json
from pathlib import Path
import re
import subprocess
import sys
import unicodedata
import zipfile


MAGIC = {b'\xce\xfa\xed\xfe', b'\xcf\xfa\xed\xfe', b'\xfe\xed\xfa\xce', b'\xfe\xed\xfa\xcf',
         b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca', b'\xca\xfe\xba\xbf', b'\xbf\xba\xfe\xca'}
PATH_PATTERN = re.compile(rb'/(?:Users|Volumes|private/tmp|tmp|opt/homebrew|usr/local)/[^\x00\r\n\"\x01-\x1f]{1,1024}')
SYSTEM_PREFIXES = ('/usr/lib/', '/System/Library/', '/Library/Apple/System/Library/')


def command(*args):
    result = subprocess.run([str(a) for a in args], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f'{args[0]} failed: {result.stderr.strip()}')
    return result.stdout


def scan(root, private_tokens, required_arch='arm64'):
    root = root.resolve(strict=True)
    report = {'root_name': root.name, 'files_scanned': 0, 'archive_entries_scanned': 0,
              'macho': [], 'path_strings': [], 'errors': [], 'warnings': []}
    tokens = sorted({unicodedata.normalize(form, t).encode('utf-8')
                     for t in private_tokens if t for form in ('NFC', 'NFD')})
    seen_paths = set()
    seen_private = set()

    def inspect_bytes(data, label):
        for token in tokens:
            if token in data and (label, token) not in seen_private:
                seen_private.add((label, token))
                report['errors'].append({'type': 'private_token', 'file': label,
                                         'token': token.decode('utf-8', 'replace')})
        for match in PATH_PATTERN.finditer(data):
            value = match.group().decode('utf-8', 'replace')
            key = (label, value)
            if key not in seen_paths:
                seen_paths.add(key)
                report['path_strings'].append({'file': label, 'value': value})

    def inspect_stream(stream, label):
        tail = b''
        while True:
            block = stream.read(1024 * 1024)
            if not block:
                break
            inspect_bytes(tail + block, label)
            tail = block[-8192:]

    def inspect_zip(source, label):
        with zipfile.ZipFile(source) as archive:
            for entry in archive.infolist():
                if entry.is_dir():
                    continue
                report['archive_entries_scanned'] += 1
                with archive.open(entry) as member:
                    inspect_stream(member, label + '!' + entry.filename)

    def inspect_pyinstaller(path, label):
        try:
            from PyInstaller.archive.readers import CArchiveReader
        except ImportError:
            report['errors'].append({'type': 'coverage_gap', 'file': label,
                                     'detail': 'Use the build Python with PyInstaller installed to inspect compressed Python archives.'})
            return
        archive = CArchiveReader(str(path))
        for name, row in archive.toc.items():
            report['archive_entries_scanned'] += 1
            member_label = label + '!' + name
            if row[-1] == 'z':
                nested = archive.open_embedded_archive(name)
                for module in nested.toc:
                    raw = nested.extract(module, raw=True)
                    if raw is not None:
                        report['archive_entries_scanned'] += 1
                        inspect_bytes(raw, member_label + '!' + module)
            else:
                raw = archive.extract(name)
                inspect_bytes(raw, member_label)
                if row[-1] == 'Z' or raw.startswith(b'PK\x03\x04'):
                    inspect_zip(io.BytesIO(raw), member_label)

    binaries = []
    for path in sorted(root.rglob('*')):
        label = str(path.relative_to(root))
        if path.is_symlink():
            try:
                if not path.resolve(strict=True).is_relative_to(root):
                    report['errors'].append({'type': 'external_symlink', 'file': label})
            except OSError as exc:
                report['errors'].append({'type': 'broken_symlink', 'file': label, 'detail': str(exc)})
            inspect_bytes(str(path.readlink()).encode(), label + ' (symlink)')
            continue
        if not path.is_file():
            continue
        report['files_scanned'] += 1
        try:
            with path.open('rb') as stream:
                magic = stream.read(4)
                stream.seek(0)
                inspect_stream(stream, label)
            if path.name.startswith('._'):
                report['warnings'].append({'type': 'appledouble', 'file': label})
            if magic in MAGIC:
                binaries.append(path)
            if magic == b'PK\x03\x04':
                inspect_zip(path, label)
            if path.name == 'DiscAuthorBackend':
                inspect_pyinstaller(path, label)
        except Exception as exc:
            report['errors'].append({'type': 'read_or_archive_failure', 'file': label, 'detail': str(exc)})

    if not binaries:
        report['errors'].append({'type': 'coverage_gap', 'detail': 'No Mach-O files found.'})
    metadata = {}
    for path in binaries:
        label = str(path.relative_to(root))
        try:
            listing = command('/usr/bin/otool', '-L', path)
            loads = command('/usr/bin/otool', '-l', path)
            header = command('/usr/bin/otool', '-hv', path)
            dependencies = sorted(set(re.findall(r'^\s+(.+?) \(compatibility version', listing, re.M)))
            rpaths = re.findall(r'cmd LC_RPATH\s+cmdsize \d+\s+path (.*?) \(offset', loads)
            install_ids = re.findall(r'cmd LC_ID_DYLIB\s+cmdsize \d+\s+name (.*?) \(offset', loads)
            item = {'file': label, 'architectures': command('/usr/bin/lipo', '-archs', path).strip().split(),
                    'executable': bool(re.search(r'\bEXECUTE\b', header)), 'dependencies': dependencies,
                    'rpaths': rpaths, 'install_ids': install_ids}
            metadata[path] = item
            report['macho'].append(item)
            if required_arch and required_arch not in item['architectures']:
                report['errors'].append({'type': 'missing_architecture', 'file': label,
                                         'required': required_arch, 'found': item['architectures']})
        except Exception as exc:
            report['errors'].append({'type': 'macho_inspection_failure', 'file': label, 'detail': str(exc)})

    main_dir = root/'Contents/MacOS' if (root/'Contents/MacOS').is_dir() else root
    main_rpaths = []
    for path, item in metadata.items():
        if item['executable'] and path.parent == main_dir:
            main_rpaths.extend(item['rpaths'])

    def expand(value, loader, executable_dir):
        value = value.replace('@loader_path', str(loader.parent)).replace('@executable_path', str(executable_dir))
        if value.startswith('/'):
            return Path(value).resolve()
        return None

    for path, item in metadata.items():
        executable_dir = path.parent if item['executable'] else main_dir
        rpaths = item['rpaths'] + main_rpaths
        for rpath in item['rpaths']:
            expanded = expand(rpath, path, executable_dir)
            if expanded is None or (not str(expanded).startswith(SYSTEM_PREFIXES) and not expanded.is_relative_to(root)):
                report['errors'].append({'type': 'external_or_unresolved_rpath', 'file': item['file'], 'value': rpath})
        for dep in item['dependencies']:
            if dep in item['install_ids'] or dep.startswith(SYSTEM_PREFIXES):
                continue
            if dep.startswith('@rpath/'):
                suffix = dep[len('@rpath/'):]
                candidates = [expand(rpath + '/' + suffix, path, executable_dir) for rpath in rpaths]
            else:
                candidates = [expand(dep, path, executable_dir)]
            valid = [p for p in candidates if p is not None and p.is_relative_to(root) and p.is_file()]
            if not valid:
                report['errors'].append({'type': 'external_or_unresolved_dependency', 'file': item['file'], 'value': dep})
    if report['path_strings']:
        report['warnings'].append({'type': 'embedded_absolute_paths', 'count': len(report['path_strings']),
                                   'detail': 'String references can be upstream diagnostics, not load dependencies. Review individually; private-token matches are errors.'})
    report['scope'] = 'Every regular file byte stream, ZIP entries, PyInstaller CArchive/PYZ entries, and Mach-O load commands; no application execution.'
    report['limitations'] = 'Generic encrypted/custom compressed data, runtime-constructed paths and dlopen targets cannot be proven by static scanning. @rpath resolution uses each image and top-level executable search paths; inspect unusual nested launch contexts manually.'
    report['passed'] = not report['errors']
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', type=Path, required=True, help='app bundle or runtime directory to inspect')
    parser.add_argument('--private-token', action='append', default=[], help='additional sensitive path/name text; repeat as needed')
    parser.add_argument('--require-arch', default='arm64', help='required Mach-O architecture (default: arm64)')
    parser.add_argument('--report', type=Path, help='JSON destination outside the inspected directory')
    args = parser.parse_args(argv)
    if sys.platform != 'darwin':
        parser.error('Mach-O inspection uses macOS otool/lipo.')
    root = args.app.expanduser().resolve(strict=True)
    if not root.is_dir():
        parser.error('--app must be a directory.')
    if args.report and args.report.expanduser().resolve().is_relative_to(root):
        parser.error('--report must be outside the inspected directory.')
    tokens = ['/Users/' + getpass.getuser() + '/', *args.private_token]
    result = scan(root, tokens, args.require_arch)
    text = json.dumps(result, ensure_ascii=False, indent=2) + '\n'
    if args.report:
        args.report.expanduser().write_text(text)
        print(json.dumps({'report': str(args.report), 'passed': result['passed'], 'files': result['files_scanned'],
                          'macho': len(result['macho']), 'archive_entries': result['archive_entries_scanned'],
                          'errors': len(result['errors']), 'path_strings': len(result['path_strings'])}))
    else:
        print(text, end='')
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, RuntimeError, ValueError) as exc:
        print(f'PORTABILITY SCAN FAILED: {exc}', file=sys.stderr)
        sys.exit(2)
