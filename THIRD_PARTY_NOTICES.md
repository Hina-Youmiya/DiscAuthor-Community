# Third-party notices — Community 0.4.16

Original DiscAuthor application code is licensed under GPL-3.0-only (see `LICENSE`). This license does not replace the licenses of fonts, upstream patches, libraries, toolchains, or system components.

The source checkout includes the Mochiy Pop P font under SIL OFL 1.1 (see `licenses/MochiyPop-OFL.txt`), original application sources, and the complete local tsMuxer patch. The upstream tsMuxer license is retained in `licenses/tsMuxer-LICENSE.txt`. Private company artwork, templates, proprietary fonts and customer media are excluded.

A complete local application bundle has been built separately from this source checkout. Its FFmpeg 9.0.2/x264, dvdauthor/spumux, mkisofs/cdrtools, libbluray, patched tsMuxer and M2TS helper were built from recorded sources. Original archives, patches, build recipes, manifests and upstream notices are retained under the build's `source-materials/` directory. The application's packaged notices are collected from `licenses/`, `toolchain/licenses/` and the prepared playback runtime. See [DEPENDENCIES.md](docs/DEPENDENCIES.md) for exact versions and hashes.

The FFmpeg build enables GPL components and libx264, and reports GPL version 2 or later. DVD Authoring Tools, cdrtools, libbluray, tsMuxer and their support libraries retain their individual upstream notices; cdrtools contains both GPL and CDDL components. Python, Pillow, Flask, Waitress, PyInstaller and build tools retain their licenses and exceptions. The Python collection records the installed active dependency closure and CPython notice. The application's GPL-3.0-only license does not replace any of these texts.

VLC 3.0.23 is a checksum-verified official arm64 runtime, prepared with 88 selected plugins. The official source release and identified contributed-library sources and notices have been obtained. The per-plugin audit mapping those 88 binaries to exact source/dependency builds and reproducing each mapping is not complete. Neither the presence of these materials nor the recorded hashes is presented as proof of a fully reproducible VLC runtime or complete application.

The local packaged backend passed four synthetic ISO exports: menu and single-video/no-menu modes for DVD and Blu-ray. Two actual native GUI startup/shutdown regression runs passed with no residual backend process. A limited native UI/playback smoke test also passed for the Japanese interface, DVD/BD menu ISO navigation into moving test footage, BD text-focus feedback and normal application exit. Eject/load buttons were visible; physical tray operation and burning were not tested in that smoke run. These checks are not full-feature certification, and a clean second Mac has not been verified. Panasonic DMP-BD65 compatibility remains unresolved. This repository publishes the source preview and retained dependency source materials; no public binary installer is attached while the native source/license inventory remains incomplete. Developer ID signing/notarization has not been performed; the local bundle uses the free ad-hoc signing route.

References:

- https://ffmpeg.org/legal.html
- https://www.videolan.org/legal.html
- https://github.com/justdan96/tsMuxer
- https://dvdauthor.sourceforge.net/
- https://code.videolan.org/videolan/libbluray
