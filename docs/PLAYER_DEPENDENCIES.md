# VLC runtime source and license inventory

The community build uses VideoLAN's official **VLC 3.0.23 arm64** image, with
88 selected plugins, `libvlc.dylib`, `libvlccore.dylib`, and the bundled HRTF data.
The source app, standalone VLC interface, updater, unrelated plugins and Java
runtime are not shipped. End users do not need Python, Homebrew or VLC installed.

The application is distributed under GPLv3; upstream components retain their own
licenses. Selecting GPLv3 for DiscAuthor does not replace third-party notices or
the obligation to provide the applicable corresponding source.

## Recreate the runtime from public inputs

Run from the source root using the build environment:

```sh
python scripts/prepare_player.py \
  --destination toolchain/deps/player-runtime \
  --cache-dir .cache/player-downloads \
  --source-materials-dir source-materials/player
```

The script verifies the pinned official DMG, mounts it read-only, calls
`package_vlc.py` to select and relocate the libraries, and detaches its mount.
VLC is never installed or launched. The helper applies the local ad-hoc signatures
needed after changing Mach-O install names; it does not use a paid certificate or
submit anything to Apple. An existing destination is refused rather than replaced.
`--sources-only` collects sources, patches and notices without preparing binaries.

The runtime receipt records the official DMG hash and the hashes of the actual
prepared files. Do not generate a new official-origin receipt for an arbitrary
pre-existing runtime. Check `preparation-receipt.json` before accepting a cached
toolchain. The final app may have different library hashes after its final local
signing, so retain both preparation and final-package inventories.

| Pinned upstream file | SHA-256 |
| --- | --- |
| `vlc-3.0.23-arm64.dmg` | `fc6fac08d87f538517d44aca0c5e7a244b67c8c4cb589bf478363a7315fd5e0d` |
| `vlc-3.0.23.tar.xz` | `e891cae6aa3ccda69bf94173d5105cbc55c7a7d9b1d21b9b21666e69eff3e7e0` |

The hashes were checked against VideoLAN's
[official DMG checksum](https://download.videolan.org/pub/videolan/vlc/3.0.23/macosx/vlc-3.0.23-arm64.dmg.sha256)
and [official source checksum](https://download.videolan.org/pub/videolan/vlc/3.0.23/vlc-3.0.23.tar.xz.sha256).
Every collected contrib archive is additionally checked against the SHA-512 in
that exact VLC source release's `contrib/src/PACKAGE/SHA512SUMS`.

## Collected source scope

The collection contains the full VLC source archive (26,486,988 bytes), all 24
identified contrib archives below (52,659,990 bytes in total), the relevant
contrib recipes/patches, and macOS build entry points. These are sources and
build instructions, not merely links to project homepages. The exact licenses
and copyright notices are retained in `THIRD_PARTY_LICENSES.txt`, individual
upstream source files, and `license-manifest.json`; the short labels below are
only a navigation aid.

| Component / version | Selected-runtime connection | Main upstream terms |
| --- | --- | --- |
| VLC 3.0.23 | Core, plugins and build scripts | LGPL-2.1-or-later core; GPL/LGPL and file-specific plugin terms |
| FFmpeg 4.4.5 | `avcodec`, `swscale`, VideoToolbox integration | This binary enables GPL; retain FFmpeg GPL/LGPL notices |
| GSM 1.0.13 | FFmpeg `--enable-libgsm` | GSM permissive notice |
| OpenJPEG 2.5.0 | FFmpeg `--enable-libopenjpeg` | BSD-2-Clause |
| LAME 3.100 | FFmpeg `--enable-libmp3lame` | LGPL; upstream COPYING and source headers |
| libdvdnav 6.1.1 | DVD menus | GPL-2.0-or-later |
| libdvdread 6.1.3 | DVD access; dvdnav dependency | GPL-2.0-or-later |
| libdvdcss 1.4.3 | Statically present in DVD plugins | GPL-2.0-or-later |
| libbluray 1.4.0 | Blu-ray access / HDMV navigation | LGPL-2.1-or-later; embedded subcomponents retain their notices |
| libxml2 2.9.14 | libbluray metadata | MIT-style / file-specific notices |
| FreeType 2.13.1 | Subtitles and libbluray text | FreeType License or GPL option; both texts retained |
| HarfBuzz 11.5.0 | Text shaping | Old MIT and internal component notices |
| FriBidi 1.0.12 | Bidirectional text | LGPL-2.1-or-later |
| libpng 1.6.50 | PNG / subtitles | libpng license |
| IJG JPEG 9b | JPEG module | IJG notice in README |
| zlib 1.3.1 | Compression; FFmpeg/fonts/PNG | Zlib |
| libsamplerate 0.1.9 | Resampling | BSD-2-Clause (this release's COPYING) |
| FLAC 1.3.4 | FLAC decoder | BSD library license; other source tools have their own terms |
| Ogg 1.3.6 | FLAC contrib dependency | BSD-3-Clause |
| SpeexDSP 1.2.1 | `speex_resampler` | BSD-style Xiph notices |
| libmad 0.15.1b | MPEG audio | GPL-2.0-or-later |
| mpg123 1.30.1 | MPEG audio | LGPL-2.1 library / upstream file-specific terms |
| FAAD2 2.10.0 | AAC module | GPL-2.0-or-later and Nero copyright notice |
| libdvbpsi 1.3.3 | Transport stream parsing | LGPL-2.1-or-later |
| aribb24 1.0.3 | Optional TS caption linkage from the release recipe | LGPL-2.1-or-later code headers; supplied COPYING contains GPLv3, so preserve both source notices |
| 3D Sound Labs HRTF data | SOFA data copied with runtime | LGPL-2.1-or-later; embedded License attribute retained |

Code from FAAD2 is copyright (c) Nero AG, www.nero.com.

Portions of this software are copyright © 2023 The FreeType Project
(www.freetype.org). All rights reserved.

The HRTF bytes were checked against the copy in the VLC source archive. The
libbluray source archive includes its libudfread source subtree; Java-related
source is retained although no Java runtime is distributed. Source archives can
include optional programs/components that do not end up in the selected runtime;
retaining their notices does not assert that all of them are linked.

## Remaining verification gap

**The 24 identified archives have all been obtained and hash-verified. No archive
in that list remains missing. This is not a claim that every statically linked
component of the upstream binary has been independently reconstructed.**

The official binaries expose useful evidence: their embedded configure strings
identify FFmpeg 4.4.5 with static GSM/OpenJPEG/LAME and GPL/postproc enabled; sampled
library version strings agree with the VLC 3.0.23 contrib recipes. Those recipes,
patches and source archives are retained. An `otool -L` scan alone cannot enumerate
statically linked third-party code or prove every configured component version.

Before describing a source bundle as complete corresponding source, finish the
binary-to-source inventory for the 88 selected modules, including optional
auto-detected/transitive dependencies, and verify that the retained build recipes
cover their actual configuration. Retain any additional sources/notices identified
by that check. A clean build from the retained inputs or an upstream component
manifest/build record can resolve the remaining uncertainty. Linker maps and
generated configuration from VideoLAN's original build were not supplied with
the DMG; no such files have been invented here.

The source collector records this limitation in `source-manifest.json`. Preserve
that qualification when sharing the prepared materials. Other bundled authoring
tools and the native libraries inside CPython/Pillow have separate source and
license inventories; the VLC list does not cover them.

## Python notices and portability checks

`requirements-lock.txt` pins the actual 15-package Python runtime/freezer
dependency closure, plus CMake as a separate native-build-only tool. It also lists
the 12 packages vendored inside the pinned setuptools. Collect their actual
license texts, PyInstaller's bootloader exception, and CPython's license using:

```sh
python scripts/collect_python_licenses.py --output toolchain/licenses/python
```

The collector does not treat pip or CMake as application runtime dependencies.
PyInstaller's exception applies to its compiled bootloader/loader combination;
it does not relicense VLC or any other dependency.

After packaging, scan the actual app using the same build environment:

```sh
python scripts/check_portability.py --app /path/to/DiscAuthor.app \
  --private-token "YOUR_PRIVATE_PROJECT_NAME" --report portability.json
```

This scan reads every regular file, compressed ZIP/PYZ payload, and Mach-O load
command. It checks for external library references and reports embedded build
paths without executing the app or changing signatures. A missing archive reader
or unreadable binary is a coverage failure. Upstream diagnostic paths such as a
VideoLAN builder's home directory are reported separately from actual dynamic
loader dependencies. Review them; do not erase bytes from a signed binary.
Static scanning cannot prove runtime-created paths or arbitrary `dlopen` behavior,
so normal launch, playback, authoring and export tests are still needed.
