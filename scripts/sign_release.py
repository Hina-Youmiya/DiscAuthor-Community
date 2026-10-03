#!/usr/bin/env python3
"""Stage, Developer ID sign, notarize, and archive a built DiscAuthor.app.

The default is a read-only preflight. Each mutating/network stage is explicit.
Only a staging copy is changed; this script never installs an app or reads secrets.
See docs/SIGNING.md. Python 3.10+ and macOS are required.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shlex
import shutil
import struct
import subprocess
import sys
import tempfile


class ReleaseError(RuntimeError):
    pass


def run(*argv, check=True):
    command = [str(arg) for arg in argv]
    print('+ ' + shlex.join(command), flush=True)
    result = subprocess.run(command, capture_output=True, text=True)
    if check and result.returncode:
        raise ReleaseError(f'Command failed ({result.returncode}): '
                           f'{shlex.join(command)}\n{result.stdout}{result.stderr}')
    return result


def write_json(path, value):
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')
    temporary.replace(path)


def digest(path):
    checksum = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            checksum.update(chunk)
    return checksum.hexdigest()


def tree_digest(app):
    """Bind later phases to the exact staged files, modes, and symlink targets."""
    checksum = hashlib.sha256()
    for path in sorted(app.rglob('*')):
        row = [str(path.relative_to(app)), path.lstat().st_mode]
        if path.is_symlink():
            row.append(os.readlink(path))
        elif path.is_file():
            row.append(digest(path))
        checksum.update(json.dumps(row, ensure_ascii=True).encode() + b'\n')
    return checksum.hexdigest()


def macho_types(path):
    """Read thin/fat Mach-O headers without running any bundled executable."""
    thin = {b'\xce\xfa\xed\xfe': '<', b'\xcf\xfa\xed\xfe': '<',
            b'\xfe\xed\xfa\xce': '>', b'\xfe\xed\xfa\xcf': '>'}
    fat = {b'\xca\xfe\xba\xbe': ('>', False), b'\xbe\xba\xfe\xca': ('<', False),
           b'\xca\xfe\xba\xbf': ('>', True), b'\xbf\xba\xfe\xca': ('<', True)}
    with path.open('rb') as stream:
        header = stream.read(16)
        if len(header) < 16:
            return set()
        magic = header[:4]
        if magic in thin:
            return {struct.unpack(thin[magic] + 'I', header[12:16])[0]}
        if magic not in fat:
            return set()
        endian, wide = fat[magic]
        count = struct.unpack(endian + 'I', header[4:8])[0]
        if not 1 <= count <= 32:
            raise ReleaseError(f'Invalid fat Mach-O header: {path}')
        types = set()
        for index in range(count):
            stream.seek(8 + index * (32 if wide else 20) + 8)
            offset_bytes = stream.read(8 if wide else 4)
            offset = struct.unpack(endian + ('Q' if wide else 'I'), offset_bytes)[0]
            stream.seek(offset)
            item = stream.read(16)
            if len(item) != 16 or item[:4] not in thin:
                raise ReleaseError(f'Invalid Mach-O slice: {path}')
            types.add(struct.unpack(thin[item[:4]] + 'I', item[12:16])[0])
        return types


def inventory(app):
    binaries, bundles = [], [app]
    for path in sorted(app.rglob('*')):
        if path.name.startswith('._'):
            raise ReleaseError(f'Remove AppleDouble sidecars in the build before release: {path}')
        if path.is_symlink():
            target = path.resolve(strict=True)
            if not target.is_relative_to(app):
                raise ReleaseError(f'Bundle symlink points outside the app: {path}')
            continue
        if path.is_dir() and path.suffix in ('.app', '.framework', '.xpc', '.appex', '.bundle'):
            # A .bundle may contain only data. Sign it only when it declares code.
            info_paths = [path/'Contents/Info.plist', path/'Resources/Info.plist', path/'Info.plist']
            info_path = next((item for item in info_paths if item.is_file()), None)
            if path.suffix == '.framework' or (info_path and plistlib.loads(info_path.read_bytes()).get('CFBundleExecutable')):
                bundles.append(path)
        if path.is_file():
            types = macho_types(path)
            if types:
                if not types <= {2, 6, 8} or (2 in types and len(types) != 1):
                    raise ReleaseError(f'Unsupported Mach-O type(s) {types}: {path}')
                binaries.append((path, 2 in types))
    if not binaries:
        raise ReleaseError('No Mach-O code found in the app.')
    return sorted(binaries, key=lambda item: len(item[0].parts), reverse=True), sorted(bundles, key=lambda p: len(p.parts), reverse=True)


def app_info(app):
    if app.suffix != '.app' or not app.is_dir():
        raise ReleaseError('--app must be a built .app directory.')
    info = plistlib.loads((app/'Contents/Info.plist').read_bytes())
    for field in ('CFBundleIdentifier', 'CFBundleExecutable', 'CFBundleShortVersionString'):
        if not isinstance(info.get(field), str) or not info[field]:
            raise ReleaseError(f'Info.plist is missing {field}.')
    executable = app/'Contents/MacOS'/info['CFBundleExecutable']
    if not executable.resolve().is_relative_to(app) or macho_types(executable) != {2}:
        raise ReleaseError('CFBundleExecutable must point to a bundled Mach-O executable.')
    return info


def identity(requested):
    result = run('/usr/bin/security', 'find-identity', '-v', '-p', 'codesigning')
    found = re.findall(r'\b([A-Fa-f0-9]{40})\s+"(Developer ID Application: [^"]+)"', result.stdout)
    if not requested:
        raise ReleaseError(f'{len(found)} valid Developer ID Application identity/identities found. '
                           'Specify an exact --identity name or certificate SHA-1; no signing occurred.')
    matches = [(fingerprint, name) for fingerprint, name in found
               if requested == name or requested.upper() == fingerprint.upper()]
    if len(matches) != 1:
        raise ReleaseError('The requested identity must match exactly one valid Developer ID Application '
                           'certificate with its private key in Keychain. No upload occurred.')
    fingerprint, name = matches[0]
    team = re.search(r'\(([A-Z0-9]{10})\)$', name)
    if not team:
        raise ReleaseError('Cannot determine Team ID from the Developer ID Application identity.')
    return fingerprint, team.group(1)


def local_output(output):
    existing = output
    while not existing.exists():
        existing = existing.parent
    result = run('/usr/sbin/diskutil', 'info', '-plist', existing)
    volume = plistlib.loads(result.stdout.encode())
    if volume.get('FilesystemType', '').lower() not in ('apfs', 'hfs'):
        raise ReleaseError('Use an APFS or Mac OS Extended output directory for signing, '
                           'then copy completed ZIP/DMG files to other disks.')


def tools_available():
    for name in ('/usr/bin/codesign', '/usr/bin/security', '/usr/bin/ditto', '/usr/bin/hdiutil',
                 '/usr/bin/xcrun', '/usr/sbin/spctl', '/usr/sbin/diskutil'):
        if not Path(name).is_file():
            raise ReleaseError(f'Required macOS tool is missing: {name}')
    run('/usr/bin/xcrun', '--find', 'notarytool')
    run('/usr/bin/xcrun', '--find', 'stapler')


def verify_code(app, team):
    binaries, bundles = inventory(app)
    for path, executable in binaries:
        run('/usr/bin/codesign', '--verify', '--strict', '--all-architectures', path)
        result = run('/usr/bin/codesign', '--display', '--verbose=4', path)
        details = result.stdout + result.stderr
        if f'TeamIdentifier={team}\n' not in details or 'Authority=Developer ID Application:' not in details:
            raise ReleaseError(f'Wrong Developer ID/Team ID: {path}')
        if 'Timestamp=' not in details:
            raise ReleaseError(f'Secure timestamp is missing: {path}')
        if executable and not re.search(r'flags=.*\bruntime\b', details):
            raise ReleaseError(f'Hardened Runtime is missing: {path}')
        result = run('/usr/bin/codesign', '--display', '--entitlements', '-', path)
        payload = result.stdout.strip()
        if payload and plistlib.loads(payload.encode()):
            raise ReleaseError(f'Unexpected entitlements: {path}. This pipeline uses no runtime exceptions.')
    for bundle in bundles:
        run('/usr/bin/codesign', '--verify', '--strict', '--all-architectures', bundle)
    # --deep is for verification only; every Mach-O above was also verified explicitly.
    run('/usr/bin/codesign', '--verify', '--deep', '--strict', '--all-architectures', app)


def make_zip(app, target):
    if target.exists():
        raise ReleaseError(f'Refusing to overwrite archive: {target}')
    run('/usr/bin/ditto', '-c', '-k', '--sequesterRsrc', '--keepParent', app, target)


def gatekeeper(app):
    run('/usr/sbin/spctl', '--assess', '--type', 'execute', '--verbose=4', app)


def prepare(args, output, info):
    work = output/'work'
    if work.exists() or (output/'release-state.json').exists():
        raise ReleaseError('This output directory already has a release. Use a fresh directory.')
    output.mkdir(parents=True, exist_ok=True)
    work.mkdir()
    staged = work/args.app.name
    run('/usr/bin/ditto', args.app, staged)
    inventory(staged)
    state = {'schema': 1, 'phase': 'prepared', 'source_app': str(args.app), 'app_name': args.app.name,
             'format': args.format, 'bundle_id': info['CFBundleIdentifier'],
             'version': info['CFBundleShortVersionString'], 'tree_sha256': tree_digest(staged)}
    write_json(output/'release-state.json', state)
    print(f'Prepared copy: {staged}. No signing or upload occurred.')


def load_state(args, output):
    state = json.loads((output/'release-state.json').read_text())
    if state.get('schema') != 1 or state.get('source_app') != str(args.app) or state.get('format') != args.format:
        raise ReleaseError('Arguments do not match this release directory; keep --app and --format unchanged.')
    if state.get('app_name') != args.app.name:
        raise ReleaseError('Invalid staged app name.')
    app = output/'work'/args.app.name
    if tree_digest(app) != state['tree_sha256']:
        raise ReleaseError('Staged bundle changed since the last successful phase. '
                           'Prepare a fresh output directory; never modify signed contents.')
    return state, app


def sign(args, output, state, app):
    if state['phase'] != 'prepared':
        raise ReleaseError('sign requires a prepared release. Use a fresh directory to sign again.')
    fingerprint, team = identity(args.identity)
    binaries, bundles = inventory(app)
    empty = output/'work/empty-entitlements.plist'
    empty.write_bytes(plistlib.dumps({}, fmt=plistlib.FMT_XML))
    # Failure after signing starts invalidates this staging tree; rebuild a fresh copy.
    for path, executable in binaries:
        relative = str(path.relative_to(app))
        identifier = state['bundle_id'] + '.code.' + hashlib.sha256(relative.encode()).hexdigest()[:24]
        command = ['/usr/bin/codesign', '--force', '--sign', fingerprint, '--timestamp', '--identifier', identifier]
        if executable:
            command += ['--options', 'runtime', '--entitlements', str(empty)]
        run(*command, path)
    for bundle in bundles:
        command = ['/usr/bin/codesign', '--force', '--sign', fingerprint, '--timestamp']
        if bundle.suffix in ('.app', '.xpc', '.appex'):
            command += ['--options', 'runtime', '--entitlements', str(empty)]
        run(*command, bundle)
    verify_code(app, team)
    state.update(phase='signed', identity_sha1=fingerprint, team_id=team, tree_sha256=tree_digest(app))
    write_json(output/'release-state.json', state)
    print('Developer ID signatures verified. Not yet notarized. Test the staged app before the next phase.')


def submission_container(args, output, state, app):
    target = output/'work'/('submission.' + state['format'])
    if state['format'] == 'zip':
        make_zip(app, target)
    else:
        fingerprint, team = identity(args.identity)
        if fingerprint != state['identity_sha1'] or team != state['team_id']:
            raise ReleaseError('DMG signing must use the same Developer ID identity as the app.')
        with tempfile.TemporaryDirectory(prefix='dmg-content-', dir=output/'work') as temporary:
            root = Path(temporary)
            run('/usr/bin/ditto', app, root/app.name)
            (root/'Applications').symlink_to('/Applications')
            run('/usr/bin/hdiutil', 'create', '-volname', 'DiscAuthor ' + state['version'],
                '-srcfolder', root, '-format', 'UDZO', '-fs', 'HFS+', target)
        run('/usr/bin/hdiutil', 'verify', target)
        run('/usr/bin/codesign', '--sign', fingerprint, '--timestamp', '--identifier',
            state['bundle_id'] + '.distribution-dmg', target)
        run('/usr/bin/codesign', '--verify', '--strict', target)
    return target


def notarize(args, output, state, app):
    if state['phase'] != 'signed':
        raise ReleaseError('notarize requires a signed release.')
    if not args.keychain_profile:
        raise ReleaseError('--keychain-profile is required; configure credentials yourself with notarytool store-credentials.')
    verify_code(app, state['team_id'])
    container = output/'work'/('submission.' + state['format'])
    if 'submission_sha256' not in state:
        if container.exists():
            raise ReleaseError('An incomplete submission container exists. Prepare a fresh release directory.')
        submission_container(args, output, state, app)
        state['submission_sha256'] = digest(container)
        write_json(output/'release-state.json', state)
    if digest(container) != state['submission_sha256']:
        raise ReleaseError('Submission container changed. Prepare a fresh release directory.')
    if args.submission_id:
        if not re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', args.submission_id):
            raise ReleaseError('--submission-id must be the UUID returned for this exact submission.')
        if state.get('submission_id', args.submission_id) != args.submission_id:
            raise ReleaseError('Recovered submission ID differs from the ID already recorded.')
        if not state.get('submission_attempted'):
            raise ReleaseError('--submission-id is only for recovering an attempted upload.')
        state['submission_id'] = args.submission_id
        write_json(output/'release-state.json', state)
    if 'submission_id' not in state:
        if state.get('submission_attempted'):
            raise ReleaseError('A previous upload has an unknown outcome. Inspect notarytool history; '
                               'resume with --submission-id UUID for this exact container. No duplicate upload was sent.')
        state['submission_attempted'] = True
        write_json(output/'release-state.json', state)
        result = run('/usr/bin/xcrun', 'notarytool', 'submit', container, '--keychain-profile',
                     args.keychain_profile, '--output-format', 'json', check=False)
        (output/'notary-submit.json').write_text(result.stdout)
        (output/'notary-submit.stderr.txt').write_text(result.stderr)
        try:
            response = json.loads(result.stdout)
            submission_id = response['id']
        except (ValueError, KeyError) as exc:
            raise ReleaseError('No submission ID was returned. Inspect notary-submit files and use '
                               'notarytool history before retrying; the upload outcome may be unknown.') from exc
        if not re.fullmatch(r'[0-9a-fA-F-]{36}', submission_id):
            raise ReleaseError('Unexpected notarization submission ID.')
        state['submission_id'] = submission_id
        write_json(output/'release-state.json', state)
        if result.returncode:
            raise ReleaseError('Submission returned an error; ID retained. Retry notarize to wait on the same ID.')
    submission_id = state['submission_id']
    result = run('/usr/bin/xcrun', 'notarytool', 'wait', submission_id, '--keychain-profile',
                 args.keychain_profile, '--timeout', '15m', '--output-format', 'json', check=False)
    (output/'notary-status.json').write_text(result.stdout)
    (output/'notary-status.stderr.txt').write_text(result.stderr)
    try:
        response = json.loads(result.stdout)
    except ValueError as exc:
        raise ReleaseError('Cannot read notary status. Submission ID is retained; retry notarize.') from exc
    status = response.get('status')
    if status in ('Accepted', 'Invalid', 'Rejected'):
        run('/usr/bin/xcrun', 'notarytool', 'log', submission_id, '--keychain-profile',
            args.keychain_profile, output/'notary-log.json')
    if result.returncode or status != 'Accepted':
        raise ReleaseError(f'Notarization is not accepted (status={status!r}). Inspect notary logs. '
                           'For pending submissions, retry notarize to wait on the same ID. No release archive was published.')
    log = json.loads((output/'notary-log.json').read_text())
    if log.get('status') != 'Accepted' or str(log.get('jobId', '')).lower() != submission_id.lower():
        raise ReleaseError('Notarization log does not confirm Accepted for the recorded submission.')
    for issue in log.get('issues') or []:
        print('Notary issue: ' + json.dumps(issue, ensure_ascii=False))
        if issue.get('severity') == 'error':
            raise ReleaseError('The notarization log contains an error; no release archive was published.')
    staple_target = app if state['format'] == 'zip' else container
    run('/usr/bin/xcrun', 'stapler', 'staple', staple_target)
    run('/usr/bin/xcrun', 'stapler', 'validate', staple_target)
    verify_code(app, state['team_id'])
    gatekeeper(app)
    if state['format'] == 'dmg':
        run('/usr/bin/codesign', '--verify', '--strict', container)
        run('/usr/bin/hdiutil', 'verify', container)
        run('/usr/sbin/spctl', '--assess', '--type', 'open', '--context',
            'context:primary-signature', '--verbose=4', container)
    state.update(phase='notarized', tree_sha256=tree_digest(app),
                 stapled_container_sha256=digest(container))
    write_json(output/'release-state.json', state)
    print('Apple returned Accepted; stapling and local signature/Gatekeeper checks passed. Run archive next.')


def archive(output, state, app):
    if state['phase'] != 'notarized':
        raise ReleaseError('archive requires accepted notarization and successful stapling/verification.')
    verify_code(app, state['team_id'])
    gatekeeper(app)
    version = re.sub(r'[^A-Za-z0-9._-]', '_', state['version'])
    final = output/f'DiscAuthor-{version}-macOS.{state["format"]}'
    if final.exists() or final.with_suffix(final.suffix + '.sha256').exists():
        raise ReleaseError(f'Refusing to overwrite release: {final}')
    with tempfile.TemporaryDirectory(prefix='archive-', dir=output/'work') as temporary:
        candidate = Path(temporary)/final.name
        if state['format'] == 'zip':
            run('/usr/bin/xcrun', 'stapler', 'validate', app)
            make_zip(app, candidate)
            extracted = Path(temporary)/'verify'
            run('/usr/bin/ditto', '-x', '-k', candidate, extracted)
            unpacked = extracted/app.name
            verify_code(unpacked, state['team_id'])
            run('/usr/bin/xcrun', 'stapler', 'validate', unpacked)
            gatekeeper(unpacked)
        else:
            container = output/'work/submission.dmg'
            if digest(container) != state['stapled_container_sha256']:
                raise ReleaseError('Stapled DMG changed; no release archive was published.')
            run('/usr/bin/xcrun', 'stapler', 'validate', container)
            run('/usr/bin/codesign', '--verify', '--strict', container)
            run('/usr/sbin/spctl', '--assess', '--type', 'open', '--context',
                'context:primary-signature', '--verbose=4', container)
            shutil.copyfile(container, candidate)
        checksum = digest(candidate)
        # Same-volume atomic publication, with exclusive creation (no partial final filename).
        with candidate.open('rb') as stream:
            os.fsync(stream.fileno())
        os.link(candidate, final)
    final.with_suffix(final.suffix + '.sha256').write_text(f'{checksum}  {final.name}\n')
    state.update(phase='archived', archive=final.name, archive_sha256=checksum)
    write_json(output/'release-state.json', state)
    print(f'Archive: {final}\nSHA-256: {checksum}\nTest this exact download on a clean Mac before publishing.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', type=Path, required=True, help='built input app; never modified')
    parser.add_argument('--identity', help='exact Developer ID Application identity name or SHA-1')
    parser.add_argument('--keychain-profile', help='existing notarytool Keychain profile name; never a password')
    parser.add_argument('--submission-id', help='recover an upload with unknown outcome using its existing UUID')
    parser.add_argument('--output-dir', type=Path, required=True, help='separate APFS/HFS+ release directory')
    parser.add_argument('--format', choices=('zip', 'dmg'), default='zip', help='retain this choice across phases')
    parser.add_argument('--stage', choices=('preflight', 'prepare', 'sign', 'notarize', 'archive'),
                        default='preflight', help='default: read-only preflight; notarize explicitly uploads to Apple')
    args = parser.parse_args(argv)  # --help exits before any external command or filesystem mutation.
    if sys.platform != 'darwin':
        raise ReleaseError('Release signing is supported only on macOS.')
    args.app = args.app.expanduser().resolve(strict=True)
    output = args.output_dir.expanduser().resolve()
    if output == args.app or output.is_relative_to(args.app) or args.app.is_relative_to(output):
        raise ReleaseError('Output and input app must be separate, non-overlapping paths.')
    tools_available()
    local_output(output)
    info = app_info(args.app)
    binaries, bundles = inventory(args.app)
    if args.stage == 'preflight':
        print(f'Bundle: {info["CFBundleIdentifier"]} {info["CFBundleShortVersionString"]}; '
              f'{len(binaries)} Mach-O files, {len(bundles)} code bundles.')
        identity(args.identity)
        print('Local preflight passed; credentials were not accessed or validated. No files changed or uploaded.')
    elif args.stage == 'prepare':
        prepare(args, output, info)
    else:
        state, app = load_state(args, output)
        if args.stage == 'sign':
            sign(args, output, state, app)
        elif args.stage == 'notarize':
            notarize(args, output, state, app)
        else:
            archive(output, state, app)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ReleaseError, OSError, ValueError, KeyError, struct.error) as exc:
        print(f'RELEASE STOPPED: {exc}', file=sys.stderr)
        sys.exit(2)
