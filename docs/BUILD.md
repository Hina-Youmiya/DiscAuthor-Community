# Building DiscAuthor Community

DiscAuthor Community is GPL-3.0-only. The free distribution path uses an ad-hoc-signed, unnotarized macOS application; no paid Apple account is required. Developer ID signing and notarization are optional later steps described in [SIGNING.md](SIGNING.md). Each third-party component retains its own notices.

Version 0.4.16 has been built as a complete application bundle on the preparation Mac. Its packaged backend passed four synthetic export cases: menu and single-video/no-menu modes for both DVD and Blu-ray. Two actual native GUI startup regression runs passed: the default preferences run became ready in 2.02 seconds and an isolated preferences-suite run in 0.89 seconds. Nine preview images were generated; both runs exited with status 0 and left no backend process running. A limited native UI/playback smoke test also passed as described below. A fresh second Mac has not been tested. Panasonic DMP-BD65 playback remains a known unresolved compatibility problem; these software checks do not fix or certify it. This repository publishes a source preview; no public binary installer is attached.

In the limited native UI/playback smoke test, the default Japanese interface displayed normally and direct eject/load buttons were visible. A DVD menu ISO and a BD menu ISO were opened through the application; both displayed menus and direction/confirm actions started moving synthetic video. On the BD menu, normal white text and yellow focus text were observed, with focus following Down navigation. The application closed normally with exit status 0. This establishes only the observed UI and ISO-playback paths, not complete visual coverage, all-feature validation, physical tray operation or physical burning.

## Prerequisites

- Apple Silicon Mac running macOS 15 or later.
- Apple Command Line Tools, installed with xcode-select --install if needed.
- Python 3.12 or later; the recorded build environment uses CPython 3.12.14.
- Internet access for the first dependency download, and space for source, intermediate files, application staging and matching source materials.

From the source checkout:

~~~sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-build.txt
~~~

The build requirements include the Python runtime dependencies, PyInstaller, its hooks and CMake. Homebrew is not required. For the recorded macOS/Python 3.12 distribution versions, install requirements-lock.txt instead. That file pins the active transitive dependency versions; it is not a hash-locked wheel archive, a complete native-library inventory, or a promise of identical results under other Python versions. Build with an isolated virtual environment.

## Full build

~~~sh
DISCAUTHOR_BUILD_ROOT="$PWD/../DiscAuthor-community-build"
.venv/bin/python -B scripts/build_community.py \
  --work-root "$DISCAUTHOR_BUILD_ROOT" --jobs 2
~~~

The top-level recipe runs the component builders, collects Python notices, checks component receipts, freezes the backend, compiles the native Swift frontend and creates the application ZIP. It does not install over an existing application, use production project data, start a burn, or publish a release. One or two compilation jobs can be selected; native tool compilation uses a lower scheduling priority.

The authoring components are built from pinned source: FFmpeg/ffprobe and x264, DVD Authoring Tools, mkisofs/cdrtools, libbluray, patched tsMuxer and the original M2TS helper. Build dependencies such as FreeType and smake are also retained. See [DEPENDENCIES.md](DEPENDENCIES.md), [FFMPEG_BUILD.md](FFMPEG_BUILD.md) and [AUTHORING_TOOLS.md](AUTHORING_TOOLS.md).

The player is a separate case: prepare_player.py verifies the pinned official VLC 3.0.23 arm64 image and prepares 88 selected runtime plugins, collecting identified source archives and notices. It does not compile VLC from source. The per-plugin mapping and reproduction audit across all 88 plugins and their dependencies is not complete; the complete application is not claimed fully reproducible.

The source builders use /tmp/discauthor-community-build as a generic symlink to the selected work root. This avoids spaces in legacy build paths and keeps personal folder names out of executable configuration strings. If that alias already belongs to a different build directory, the recipe stops instead of replacing it. Build output can reside on an external volume. Final app signing uses local APFS staging to avoid exFAT AppleDouble metadata entering a signature.

## Outputs and rebuilds

The work root contains:

~~~text
toolchain/
  provenance.json
  licenses/                    # FFmpeg, authoring, navigation and Python notices
  bin/ffmpeg
  bin/ffprobe
  bin/tsmuxer
  bin/mkisofs
  bin/reexport-m2ts
  deps/bin/dvdauthor
  deps/bin/spumux
  deps/lib/libbluray.2.dylib
  deps/player-runtime/         # Selected official VLC runtime and notices
source-materials/
  ffmpeg/                     # Source archives, x264 bundle, recipe and manifest
  authoring/                  # DVD/ISO dependencies, recipe and manifest
  navigation/                 # tsMuxer source, complete patch and helper source
  player/                     # VLC/contrib source collection and notices
package/dist/DiscAuthor Community.app
~~~

The ZIP is written to the checkout's release directory with UNNOTARIZED in its name. Retain the matching source-material directories and upstream notices alongside the application and source release.

To rebuild only the application around an existing completed toolchain:

~~~sh
.venv/bin/python -B scripts/build_community.py \
  --work-root "$DISCAUTHOR_BUILD_ROOT" --reuse-toolchain
~~~

The --reuse-toolchain option skips component compilation but still collects Python notices and validates the tools against their component-build hashes. VLC's file inventory is checked against its preparation receipt. It does not assign new trusted hashes to arbitrary replacement binaries.

To validate a completed toolchain and write its combined manifest without compiling or packaging:

~~~sh
.venv/bin/python -B scripts/build_community.py \
  --work-root "$DISCAUTHOR_BUILD_ROOT" --manifest-only
~~~

This mode requires the existing component manifests and player receipt. Missing or mismatched evidence fails the operation. It does not prepare missing dependencies or collect Python notices. The package builder separately checks the combined manifest before copying tools into the app.

## Application verification and DMG

Run focused source tests with:

~~~sh
.venv/bin/python -m unittest discover -s tests -p 'test_*.py'
~~~

Then exercise the actual packaged backend in a **new** synthetic work directory:

~~~sh
.venv/bin/python -B scripts/smoke_bundle.py \
  --app "$DISCAUTHOR_BUILD_ROOT/package/dist/DiscAuthor Community.app" \
  --work-dir "$DISCAUTHOR_BUILD_ROOT/packaged-smoke"
~~~

The test checks backend authentication, previews in three UI languages, selection/activation feedback, and queued menu/single-title DVD and BD exports. It records report.json, synthetic ISOs and logs. The local 0.4.16 packaged backend passed all four export cases. This backend test does not start the native GUI, burn a disc or move a tray. Native startup and limited UI/ISO playback were checked separately as described above. These checks do not establish complete visual coverage or physical-player success. Rerun the backend test after rebuilding the backend and retain the report for the actual bundle being distributed.

After the app passes the applicable checks, create a new drag-to-Applications image and checksum:

~~~sh
.venv/bin/python -B scripts/package_free_dmg.py \
  --app "$DISCAUTHOR_BUILD_ROOT/package/dist/DiscAuthor Community.app" \
  --output "$DISCAUTHOR_BUILD_ROOT/DiscAuthor-Community-UNNOTARIZED.dmg"
~~~

The DMG script checks the app signature and resulting image, but does not notarize it. Include [INSTALL_UNSIGNED.md](INSTALL_UNSIGNED.md), SHA-256 values, matching sources and license notices when distributing. End users of a complete bundle do not need Python, Homebrew or a developer toolchain. A clean second-Mac installation and real drive/playback checks remain separate release validation.
