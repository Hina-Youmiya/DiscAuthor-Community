"""Build the generic arm64 app; user project assets are never app resources."""
from pathlib import Path
import argparse,hashlib,json,plistlib,shutil,subprocess,sys,tempfile,os

ROOT=Path(__file__).resolve().parents[1]
VERSION='0.4.16'
# Bundled disc-authoring binaries require macOS 15, even if SwiftUI supports older systems.
MINIMUM_MACOS='15.0'
APP_NAME='DiscAuthor Community'


def toolchain_root():
    """Only bundle explicitly supplied tools with a hash manifest."""
    configured=os.environ.get('DISCAUTHOR_TOOLCHAIN_DIR')
    if not configured:
        raise RuntimeError('Set DISCAUTHOR_TOOLCHAIN_DIR to a prepared toolchain. See docs/BUILD.md.')
    root=Path(configured).expanduser().resolve()
    manifest=root/'provenance.json'
    if not manifest.is_file():raise RuntimeError('Toolchain is missing provenance.json; see docs/BUILD.md')
    data=json.loads(manifest.read_text())
    files=data.get('files',{})
    required=['bin/ffmpeg','bin/ffprobe','bin/tsmuxer','bin/mkisofs','bin/reexport-m2ts',
              'deps/bin/dvdauthor','deps/bin/spumux','deps/lib/libbluray.2.dylib']
    for relative in required:
        path=root/relative
        record=files.get(relative,{})
        if not path.is_file() or not record.get('source') or not record.get('sha256'):
            raise RuntimeError('Missing tool or provenance entry: '+relative)
        with path.open('rb') as stream: digest=hashlib.file_digest(stream,'sha256').hexdigest()
        if digest!=record['sha256']:raise RuntimeError('Toolchain hash mismatch: '+relative)
    if not (root/'licenses').is_dir():raise RuntimeError('Toolchain license notices are missing')
    runtime=root/'deps/player-runtime'
    if not (runtime/'lib/libvlc.dylib').is_file():
        raise RuntimeError('Prepared VLC runtime missing; see docs/BUILD.md')
    expected_player=data.get('player_files',{})
    actual_player={str(p.relative_to(runtime)):p for p in runtime.rglob('*')
                   if p.is_file() and not p.name.startswith('._')}
    if not expected_player or set(expected_player)!=set(actual_player):
        raise RuntimeError('VLC runtime file inventory differs from provenance.json')
    for name,path in actual_player.items():
        with path.open('rb') as stream: actual=hashlib.file_digest(stream,'sha256').hexdigest()
        if actual!=expected_player[name]:raise RuntimeError('VLC runtime hash mismatch: '+name)
    return root


def prepare_source(stage):
    toolchain=toolchain_root()
    # A fresh staging directory prevents old assets surviving a later rebuild.
    stage=Path(stage);stage.mkdir(parents=True,exist_ok=True)
    source=Path(tempfile.mkdtemp(prefix='src-',dir=stage))
    for name in ['application.py','paths.py','server.py','author.py','bluray.py','bd_ig.py','bd_avc.py','bd_avc_repair.py','bd_validation.py','fonts.py','localization.py','menu_feedback.py','menu_text.py','menu_templates.py','preflight.py','encoding.py','progress.py','queue_manager.py','burner.py','bluray_reexport.py','reexport_validation.py','iso_export.py','direct_burn.py','disc_reader.py']:
        shutil.copyfile(ROOT/name,source/name)
    for name in ['licenses','docs']:
        shutil.copytree(ROOT/name,source/name,ignore=shutil.ignore_patterns('._*','.DS_Store','fonts.json'))
    shutil.copytree(toolchain/'licenses',source/'licenses/toolchain',ignore=shutil.ignore_patterns('._*'))
    shutil.copyfile(toolchain/'provenance.json',source/'licenses/toolchain-provenance.json')
    (source/'web').mkdir()
    shutil.copyfile(ROOT/'web/translations.json',source/'web/translations.json')
    # Only generic assets are allowed into the public-edition application.
    for name in ['default-project.json','fonts/MochiyPopP-Regular.otf','templates/general-preview.png']:
        target=source/'assets'/name;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(ROOT/'assets'/name,target)
    preset=json.loads((source/'assets/default-project.json').read_text())
    if preset.get('background') or preset.get('layout')!='auto':
        raise ValueError('The packaged new-project preset must use a generic automatic layout with no background image.')
    for name in ['bin/tsmuxer','bin/mkisofs','bin/reexport-m2ts','deps/bin/dvdauthor','deps/bin/spumux']:
        target=source/name;target.parent.mkdir(exist_ok=True,parents=True)
        shutil.copyfile(toolchain/name,target);target.chmod(0o755)
    for name in ['ffmpeg','ffprobe']:
        target=source/'bin'/name
        shutil.copyfile(toolchain/'bin'/name,target);target.chmod(0o755)
    # The read-only ISO inspector uses libbluray's UDF reader. No Homebrew or
    # development-directory lookup is needed on a colleague's Mac.
    target=source/'deps/lib/libbluray.2.dylib';target.parent.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(toolchain/'deps/lib/libbluray.2.dylib',target);target.chmod(0o755)
    subprocess.run(['/usr/bin/install_name_tool','-id','@rpath/libbluray.2.dylib',str(target)],check=True)
    for name in ['README.md','THIRD_PARTY_NOTICES.md','LICENSE']:
        shutil.copyfile(ROOT/name,source/name)
    # exFAT may create AppleDouble while copying executables. Do not let
    # PyInstaller collect these as binaries or turn them into bundle symlinks.
    for sidecar in source.rglob('._*'):
        if sidecar.is_symlink() or sidecar.is_file():sidecar.unlink()
    return source


def main():
    parser=argparse.ArgumentParser(description='Build a local, unnotarized Community app from an explicit toolchain.')
    parser.add_argument('--native-only',action='store_true',help='Reuse an existing bundled backend in the configured build directory')
    args=parser.parse_args()
    stage=Path(os.environ.get('DISCAUTHOR_BUILD_DIR',str(ROOT/'.build/package'))).expanduser().resolve()
    if args.native_only:
        finalize_native(stage)
        return
    source=prepare_source(stage)
    cmd=[sys.executable,'-m','PyInstaller','--noconfirm','--clean','--windowed','--name',APP_NAME,'--osx-bundle-identifier','jp.discauthor.community','--distpath',str(stage/'dist'),'--workpath',str(stage/'work'),'--specpath',str(stage),'--add-data',str(source/'licenses')+':licenses','--add-data',str(source/'docs')+':docs','--add-data',str(source/'LICENSE')+':.','--add-data',str(source/'README.md')+':.','--add-data',str(source/'THIRD_PARTY_NOTICES.md')+':.','--add-data',str(source/'web')+':web','--add-data',str(source/'assets')+':assets','--add-binary',str(source/'bin')+':bin','--add-binary',str(source/'deps/bin')+':deps/bin',str(source/'application.py')]
    cmd[-1:-1]=['--add-binary',str(source/'deps/lib')+':deps/lib']
    subprocess.run(cmd,check=True,cwd=source)
    bundle=stage/'dist'/(APP_NAME+'.app')
    executable=bundle/'Contents/MacOS'/APP_NAME
    executable.rename(executable.with_name('DiscAuthorBackend'))
    finalize_native(stage)


def finalize_native(stage):
    stage=Path(stage).resolve()
    bundle=stage/'dist'/(APP_NAME+'.app')
    executable=bundle/'Contents/MacOS/DiscAuthor'
    if not executable.with_name('DiscAuthorBackend').is_file():
        raise RuntimeError('Build the bundled authoring engine before compiling the native frontend.')
    native_source=Path(tempfile.mkdtemp(prefix='native-',dir=stage))
    native_files=('DiscAuthorModel.swift','DiscAuthorViews.swift','DiscToolsModels.swift','DiscToolsViews.swift','DiscPlayerEngine.swift')
    for name in native_files:
        shutil.copyfile(ROOT/'native'/name,native_source/name)
    # Keep imported Swift/Clang modules local to this compile. Reusing a cache
    # through different symlink spellings can invalidate Foundation imports.
    module_cache=native_source/'module-cache';module_cache.mkdir()
    subprocess.run(['/usr/bin/swiftc','-swift-version','5','-parse-as-library','-O','-module-cache-path',str(module_cache),'-target',f'arm64-apple-macosx{MINIMUM_MACOS}','-framework','AVKit',*[str(native_source/name) for name in native_files],'-o',str(executable)],check=True)
    info_path=bundle/'Contents/Info.plist'
    with info_path.open('rb') as stream:info=plistlib.load(stream)
    icon=ROOT/'assets/AppIcon.icns'
    if not icon.is_file():raise RuntimeError('Missing native application icon')
    shutil.copyfile(icon,bundle/'Contents/Resources/AppIcon.icns')
    info.update(CFBundleDisplayName=APP_NAME,CFBundleName=APP_NAME,CFBundleIdentifier='jp.discauthor.community',CFBundleShortVersionString=VERSION,CFBundleVersion=VERSION,CFBundleExecutable='DiscAuthor',CFBundleIconFile='AppIcon.icns',NSHighResolutionCapable=True,LSMinimumSystemVersion=MINIMUM_MACOS,LSApplicationCategoryType='public.app-category.video')
    with info_path.open('wb') as stream:plistlib.dump(info,stream)
    from package_vlc import package_runtime
    package_runtime(toolchain_root()/'deps/player-runtime',bundle/'Contents/Resources/VLC')
    for sidecar in bundle.rglob('._*'):
        if sidecar.is_symlink() or sidecar.is_file():sidecar.unlink()
    for assets in bundle.glob('Contents/*/assets'):
        shipped={p.name for p in assets.iterdir()}
        if shipped!={'default-project.json','templates','fonts'}:
            raise RuntimeError('Unexpected bundled project assets: '+', '.join(sorted(shipped)))
        actual={str(p.relative_to(assets/'templates')) for p in (assets/'templates').rglob('*') if p.is_file()}
        if actual!={'general-preview.png'}:raise RuntimeError('Unexpected template resources')
        if {p.name for p in (assets/'fonts').iterdir()}!={'MochiyPopP-Regular.otf'}:
            raise RuntimeError('Unexpected font resources')
    release=ROOT/'release';release.mkdir(exist_ok=True)
    archive=release/f'DiscAuthor-Community-{VERSION}-macOS-arm64-UNNOTARIZED.zip'
    sign_and_archive(bundle,archive)
    print('Ad-hoc build; not notarized. See docs/INSTALL_UNSIGNED.md and complete the license/source audit before distribution.')
    print(archive)


def sign_and_archive(bundle,archive):
    # Build volumes can be exFAT: do final signing on the local macOS volume,
    # where AppleDouble cannot become part of the sealed resource inventory.
    bundle=Path(bundle);archive=Path(archive)
    # Native-only rebuilds must carry current notices too. Do this before
    # signing so the resource seal covers exactly what the user receives.
    resources=bundle/'Contents/Resources'
    for name in ('LICENSE','README.md','THIRD_PARTY_NOTICES.md'):
        shutil.copyfile(ROOT/name,resources/name)
    shutil.copytree(ROOT/'docs',resources/'docs',dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('._*','.DS_Store'))
    with tempfile.TemporaryDirectory(prefix='discauthor-sign-',dir='/private/tmp') as temporary:
        signed=Path(temporary)/bundle.name
        shutil.copytree(bundle,signed,symlinks=True,ignore=shutil.ignore_patterns('._*'))
        subprocess.run(['/usr/bin/codesign','--force','--deep','--sign','-',str(signed)],check=True)
        subprocess.run(['/usr/bin/codesign','--verify','--deep','--strict',str(signed)],check=True)
        subprocess.run(['/usr/bin/ditto','-c','-k','--sequesterRsrc','--keepParent',str(signed),str(archive)],check=True)
        def remove_error(function,path,exception):
            # exFAT removes an AppleDouble entry with its associated file;
            # a directory enumeration can still contain the now-absent entry.
            if not isinstance(exception,FileNotFoundError):raise exception
        shutil.rmtree(bundle,onexc=remove_error)
        shutil.copytree(signed,bundle,symlinks=True)
        subprocess.run(['/usr/bin/codesign','--verify','--deep','--strict',str(bundle)],check=True)


if __name__=='__main__':main()
