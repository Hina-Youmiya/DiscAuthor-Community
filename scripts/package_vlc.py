"""Embed VideoLAN's verified arm64 playback runtime without requiring VLC.app.

Usage: python scripts/package_vlc.py --source /path/VLC.app --destination /path/Contents/Resources/VLC
Existing source can also be an already prepared runtime directory (lib/plugins/share).
This helper intentionally excludes VLC's standalone Cocoa UI/update/notification plugins.
"""
from pathlib import Path
import argparse,hashlib,json,shutil,subprocess,urllib.request

VERSION='3.0.23'
DMG_SHA256='fc6fac08d87f538517d44aca0c5e7a244b67c8c4cb589bf478363a7315fd5e0d'
SOURCE_URL=f'https://download.videolan.org/pub/videolan/vlc/{VERSION}/vlc-{VERSION}.tar.xz'
# Keep only optical-disc access, DVD/HDMV menus, mandatory disc codecs,
# native output, subtitle, color conversion and audio conversion modules.
# Avoid loading hundreds of unrelated capture/network/encoder/effect plugins.
DISC_MODULES=set("""dvdnav dvdread libbluray filesystem ps ts es h26x vc1 mpgv
avcodec videotoolbox lpcm araw mad mpg123 faad flac spudec cvdsub dvbsub vobsub textst subsdec cc
packetizer_a52 packetizer_dts packetizer_mlp packetizer_h264 packetizer_hevc packetizer_vc1
packetizer_mpegvideo packetizer_mpegaudio packetizer_mpeg4audio packetizer_copy packetizer_flac
vout_macosx ca opengllayer glconv_cvpx cvpx swscale yuv yuvp i420_rgb i420_yuy2 i420_nv12 i420_10_p010
i422_i420 i422_yuy2 yuy2_i420 yuy2_i422 grey_yuv chain blend deinterlace freetype png jpeg image
auhal float_mixer integer_mixer audio_format simple_channel_mixer trivial_channel_mixer
headphone_channel_mixer samplerate ugly_resampler speex_resampler scaletempo tospdif spdif
cache_read cache_block prefetch inflate idummy dummy console_logger file_logger logger
vmem amem marq logo adjust equalizer subtitle
""".replace("ca opengllayer", "caopengllayer").split())
EXCLUDED={'libmacosx_plugin.dylib','libosx_notifications_plugin.dylib'}

def package_runtime(source,destination):
    source=Path(source).resolve();destination=Path(destination).resolve()
    if source==destination or source in destination.parents:
        raise ValueError('Runtime destination must be separate from source')
    app=source/'Contents/MacOS'
    prepared=not app.is_dir()
    runtime=source if prepared else app
    for name in ['lib/libvlc.dylib','lib/libvlccore.dylib','plugins/libdvdnav_plugin.dylib','plugins/liblibbluray_plugin.dylib','plugins/libvout_macosx_plugin.dylib']:
        if not (runtime/name).is_file():raise ValueError('Playback runtime is missing '+name)
    destination.mkdir(parents=True,exist_ok=True)
    for directory in ['lib','plugins','share','licenses']:
        (destination/directory).mkdir(exist_ok=True)
    for name in ['libvlc.dylib','libvlccore.dylib']:
        shutil.copyfile(runtime/'lib'/name,destination/'lib'/name)
    for p in (runtime/'plugins').glob('*.dylib'):
        if p.name in {f'lib{name}_plugin.dylib' for name in DISC_MODULES}:shutil.copyfile(p,destination/'plugins'/p.name)
    # Only the HRTF audio data is needed; scripting, network UI and localization
    # are handled by DiscAuthor, so do not ship VLC's script interpreters/data.
    if (runtime/'share/hrtfs').exists():
        shutil.copytree(runtime/'share/hrtfs',destination/'share/hrtfs',dirs_exist_ok=True)
    if prepared and (runtime/'licenses').exists():
        shutil.copytree(runtime/'licenses',destination/'licenses',dirs_exist_ok=True)
    else:
        for name,url in {
            'VLC-COPYING.txt':f'https://raw.githubusercontent.com/videolan/vlc/{VERSION}/COPYING',
            'VLC-COPYING.LIB.txt':f'https://raw.githubusercontent.com/videolan/vlc/{VERSION}/COPYING.LIB',
            'VLC-AUTHORS.txt':f'https://raw.githubusercontent.com/videolan/vlc/{VERSION}/AUTHORS',
            'VLC-THANKS.txt':f'https://raw.githubusercontent.com/videolan/vlc/{VERSION}/THANKS',
        }.items():
            with urllib.request.urlopen(url,timeout=30) as response:(destination/'licenses'/name).write_bytes(response.read())
    binaries=[p for p in list((destination/'lib').glob('*.dylib'))+list((destination/'plugins').glob('*.dylib')) if not p.name.startswith('._')]
    for binary in binaries:
        binary.chmod(0o755)
        listing=subprocess.check_output(['/usr/bin/otool','-L',str(binary)],text=True)
        commands=['/usr/bin/install_name_tool','-id','@loader_path/'+binary.name]
        for line in listing.splitlines()[2:]:
            dependency=line.strip().split(' (')[0]
            if dependency.startswith(('@rpath/libvlc','@loader_path/')):
                name=Path(dependency).name
                if not (destination/'lib'/name).is_file():raise ValueError('Unresolved VLC library '+dependency)
                target='@loader_path/'+('' if binary.parent.name=='lib' else '../lib/')+name
                commands+=['-change',dependency,target]
            elif not dependency.startswith(('/usr/lib/','/System/Library/')):
                raise ValueError(f'Nonportable VLC dependency: {binary.name}: {dependency}')
        subprocess.run(commands+[str(binary)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        subprocess.run(['/usr/bin/codesign','--force','--sign','-',str(binary)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    (destination/'licenses/SOURCE-AND-NOTICES.txt').write_text(f'''DiscAuthor disc playback runtime: VideoLAN VLC {VERSION}, arm64.
Official binary: https://download.videolan.org/pub/videolan/vlc/{VERSION}/macosx/vlc-{VERSION}-arm64.dmg
Official binary SHA-256: {DMG_SHA256}
Corresponding VLC source: {SOURCE_URL}
Contributed library source/version recipes: https://github.com/videolan/vlc/tree/{VERSION}/contrib/src
VLC core and libVLC are LGPL-2.1-or-later; plugins include GPL-2.0-or-later code and third-party contributions. See accompanying COPYING, COPYING.LIB, AUTHORS and THANKS.
The bundled dylibs remain replaceable; only macOS install names and ad-hoc signatures were changed. The standalone VLC user interface, Sparkle updater and Growl notifications are excluded. No Java runtime is included. BD-J and protected commercial discs are not guaranteed.
The runtime contains independently distributed VideoLAN components. Redistribution requires the applicable notices and corresponding source for VLC and its contributed libraries. Source URLs alone do not constitute a completed redistribution audit.
''')
    size=sum(p.stat().st_size for p in destination.rglob('*') if p.is_file() and not p.name.startswith('._'))
    result={'version':VERSION,'source_dmg_sha256':DMG_SHA256,'runtime_bytes':size,'plugins':len(binaries)-2,'source_url':SOURCE_URL}
    (destination/'runtime.json').write_text(json.dumps(result,indent=2)+'\n')
    return result

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--source',required=True);parser.add_argument('--destination',required=True)
    args=parser.parse_args();print(json.dumps(package_runtime(args.source,args.destination),indent=2))
