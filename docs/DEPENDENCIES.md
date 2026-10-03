# Dependency provenance

Community 0.4.16 uses recorded upstream source and local build recipes for its encoding and authoring tools. These recipes and the complete application packaging have run on the preparation Mac; a clean second-Mac build has not been tested. The retained manifests identify actual outputs, compiler/SDK details and source hashes. They do not promise byte-identical results under a different Apple toolchain or establish complete reproduction of the VLC runtime. This repository publishes a source preview and retained dependency source materials; no public binary installer is attached.

The application is GPL-3.0-only. Third-party license texts and source obligations remain attached to their components. Free, unnotarized distribution and source provenance are independent of optional Apple Developer ID fees.

| Component | Recorded input | Community build |
| --- | --- | --- |
| FFmpeg / ffprobe | 9.0.2 official source release | New source build; static FFmpeg/x264, system dynamic libraries only |
| x264 | b35605ace3ddf7c1a5d67a2eb553f034aef41d55, version 0.165.3222 | 8-bit 4:2:0 static encoder with interlacing; source tar and Git bundle retained |
| tsMuxer | c6b1186209e42c877052e762c9185f3226ef8ea2 | Source build with complete scripts/tsmuxer-changes.patch |
| M2TS helper | src/reexport_m2ts.c | Original source; standard C and macOS CommonCrypto |
| dvdauthor / spumux | 0.7.2 | Source build with static FreeType/libpng/libdvdread support |
| mkisofs / cdrtools | 3.02a09 official source archive | Source build using source-built smake; no package-manager bottle reused |
| libbluray | 1.3.4 | Source-built dynamic library; BD-J and optional font/XML integrations disabled |
| VLC runtime | 3.0.23 official arm64 image | Verified upstream binary subset; identified VLC/contrib sources and notices collected separately |
| Python packages | requirements-build.txt, requirements-lock.txt | Versioned build environment; active package and CPython notices collected during packaging |

## Build entry point and evidence

Install requirements-build.txt in a Python 3.12+ virtual environment, then run scripts/build_community.py with --work-root and --jobs 2. CMake is included in the build requirements; Homebrew is not needed. See [BUILD.md](BUILD.md) for complete commands and output locations.

The work root preserves these component receipts:

- source-materials/ffmpeg/build-manifest.json: exact FFmpeg/x264/pkgconf inputs, source archive and executable hashes, compiler/SDK and the copied recipe.
- source-materials/authoring/authoring-build-manifest.json: DVD/ISO tool source archives, build options, output hashes, dynamic dependencies and notices.
- source-materials/navigation/manifest.json: tsMuxer commit, source archive, patch hash and the two navigation/helper output hashes.
- source-materials/player/source-manifest.json and the runtime's preparation-receipt.json: selected playback source collection, official image checksum and prepared file inventory.
- toolchain/licenses/python/manifest.json: the installed active Python dependency closure and collected notices.

The --reuse-toolchain option validates existing output hashes against these component receipts before packaging. The --manifest-only option performs that validation and writes toolchain/provenance.json without compiling or packaging. Neither mode blindly relabels arbitrary tools as source-built. The package builder independently checks the combined executable/library hashes and VLC file inventory before copying them into an application.

## Pinned primary inputs

| Source | Upstream URL / revision | SHA-256 |
| --- | --- | --- |
| FFmpeg 9.0.2 | https://ffmpeg.org/releases/ffmpeg-9.0.2.tar.xz | 8c3850283eb25fa026482078a04051e0be17347b09ef81a0849bec15a96e002e |
| x264 | https://code.videolan.org/videolan/x264.git at b35605ace3ddf7c1a5d67a2eb553f034aef41d55 | Git commit pinned; retained tar/bundle hashes are in the FFmpeg manifest |
| tsMuxer | https://github.com/justdan96/tsMuxer.git at c6b1186209e42c877052e762c9185f3226ef8ea2 | Retained source archive hash is in the navigation manifest |
| tsMuxer local patch | scripts/tsmuxer-changes.patch | a031cf358c715a160ee9d8524cb87a7a5fcf87f7016514390c47abb45c926265 |
| dvdauthor 0.7.2 | https://downloads.sourceforge.net/project/dvdauthor/dvdauthor-0.7.2.tar.gz | 3020a92de9f78eb36f48b6f22d5a001c47107826634a785a62dfcd080f612eb7 |
| cdrtools 3.02a09 | https://downloads.sourceforge.net/project/cdrtools/alpha/cdrtools-3.02a09.tar.gz | c7e4f732fb299e9b5d836629dadf5512aa5e6a5624ff438ceb1d056f4dcb07c2 |
| libbluray 1.3.4 | https://download.videolan.org/pub/videolan/libbluray/1.3.4/libbluray-1.3.4.tar.bz2 | 478ffd68a0f5dde8ef6ca989b7f035b5a0a22c599142e5cd3ff7b03bbebe5f2b |
| VLC 3.0.23 source | https://download.videolan.org/pub/videolan/vlc/3.0.23/vlc-3.0.23.tar.xz | e891cae6aa3ccda69bf94173d5105cbc55c7a7d9b1d21b9b21666e69eff3e7e0 |
| VLC 3.0.23 arm64 image | https://download.videolan.org/pub/videolan/vlc/3.0.23/macosx/vlc-3.0.23-arm64.dmg | fc6fac08d87f538517d44aca0c5e7a244b67c8c4cb589bf478363a7315fd5e0d |

Supporting source inputs are FreeType 2.13.3, libpng 1.6.47, libdvdread 6.1.3, Schily Tools 2024-03-21 for smake, and pkgconf 3.0.7. Their exact URLs and hashes are pinned in scripts/build_authoring_tools.py and scripts/build_ffmpeg.py and copied into the corresponding receipts. Retain these support-library sources together with the primary source packages.

The FFmpeg source hash was cross-checked with the upstream-source [Homebrew FFmpeg formula](https://raw.githubusercontent.com/Homebrew/homebrew-core/master/Formula/f/ffmpeg.rb), then verified against the downloaded official archive. The same approach was used for the [dvdauthor](https://raw.githubusercontent.com/Homebrew/homebrew-core/master/Formula/d/dvdauthor.rb) and [cdrtools](https://raw.githubusercontent.com/Homebrew/homebrew-core/master/Formula/c/cdrtools.rb) archives. These formulas serve as checksum references; their bottles are not used for the Community authoring tools.

## Component boundaries

FFmpeg 9.0.2 is a new documented build, with GPL/libx264 support and the required MPEG-2, AC-3, FFV1, PNG, colorspace, deinterlacing and scale reset_sar features. Optional external codec libraries are not autodetected. See [FFMPEG_BUILD.md](FFMPEG_BUILD.md) for the recipe and synthetic validation scope.

DVD Authoring Tools uses static FreeType, libpng and libdvdread; macOS supplies libxml2, iconv and zlib. libbluray remains a replaceable dynamic library with a portable install name. cdrtools uses the supported smake build process and documented compatibility flags; the complete recipe and any omitted profiling makefiles are recorded. See [AUTHORING_TOOLS.md](AUTHORING_TOOLS.md).

VLC 3.0.23 is prepared from its checksum-verified official image, not rebuilt by these scripts. The official runtime and identified corresponding source packages have been obtained and retained. The selected runtime contains 88 plugins. The audit mapping each plugin to its precise source/dependency build and reproducing those mappings is not complete. The collection receipt is evidence of the materials obtained, not proof that the entire application or all 88 plugins can be reproduced. Check the collection receipt and packaged subset for the specific release.

The Python lock records the inspected CPython 3.12 environment's package versions and active transitive closure. It does not replace the native-library source inventory for Python/Pillow, or a hash-locked package download archive. The Python notice collector runs in the actual packaging environment so shipped notices correspond to the installed distributions.

The local source-material collection also retains the 16 locked Python distribution source archives and CPython 3.12.14 source. Their receipt records download origins and checksums. Exact source/build mapping for native components inside the Python distribution and Pillow wheel remains incomplete; see the collection's `python/SOURCE_GAPS.json`. These archives are not a claim that those prebuilt native libraries have been reproduced.

The local 0.4.16 packaged backend passed four synthetic ISO exports: DVD and BD, each with a menu and with a single video/no menu. Two actual native GUI startup/shutdown regression runs also passed with no residual backend process. A limited native UI/playback smoke test passed for the default Japanese interface and DVD/BD menu ISO playback: menus displayed, direction/confirm actions started moving test footage, and the BD menu showed white normal text with yellow focus following Down navigation. Eject/load buttons were visible and the app exited normally with status 0. This does not establish all-feature coverage, physical tray operation or burning. A clean second-Mac installation remains untested, and the known Panasonic DMP-BD65 rejection is unresolved.
