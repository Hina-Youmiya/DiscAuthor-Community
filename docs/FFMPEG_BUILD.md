# Community FFmpeg source build

The Community toolchain uses a new build of FFmpeg 9.0.2 and x264
0.165.3222, compiled from retained upstream source. It does not reuse or
claim to reproduce a previously supplied FFmpeg executable.

## Build

Requirements: Apple Silicon, macOS 15 or later, Apple Command Line Tools,
and Python 3.12 or later. No Homebrew installation is required. This recipe
does not install into system directories or replace an installed DiscAuthor.

```sh
python3 scripts/build_ffmpeg.py --work-root ./community-build-work --jobs 2
```

The build uses `nice 15` and defaults to two simultaneous compiler jobs.
`--jobs` accepts 1–6. A generic `/tmp/discauthor-community-build` symlink points
to the supplied build directory; an existing unrelated path is never
replaced. This also avoids embedding personal directory names in the
executables. `/usr/local` in FFmpeg's configuration is its recorded installation
prefix only: the script copies the finished executables to the private output
directory and does not install anything into `/usr/local`.

Outputs relative to the work root:

- `toolchain/bin/ffmpeg`, `toolchain/bin/ffprobe`: arm64 executables.
- `source-materials/ffmpeg/archives`: original source tarballs, x264 source
  archive, and Git bundle retaining the history used by x264's version script.
- `source-materials/ffmpeg/licenses`: upstream license notices.
- `toolchain/licenses/ffmpeg`: the same notices copied for application packaging.
- `source-materials/ffmpeg/build-manifest.json`: source and executable hashes,
  compiler, SDK, deployment target and component versions.
- `source-materials/ffmpeg/commands.json`, `logs/`, and `build_ffmpeg.py`:
  command record, build logs, and the recipe used for the build.
- `source-materials/ffmpeg/validation.json`: synthetic validation results.

For an offline reconstruction, place the verified tarballs into `archives`
and clone the retained x264 Git bundle into `ffmpeg/source/x264` before running
the recipe. The script verifies the two tarball hashes and checks out the
pinned x264 commit. A plain x264 tar archive alone does not reproduce its
Git-derived version string; use the accompanying Git bundle.

## Pinned sources

Verified on 2026-10-03. Archive hashes were cross-checked against Homebrew's
upstream-source formulas, then calculated again after downloading directly
from the upstream servers. This is not a claim that an OpenPGP signature was
verified.

| Component | Source |
| --- | --- |
| FFmpeg 9.0.2 | https://ffmpeg.org/releases/ffmpeg-9.0.2.tar.xz |
| x264 | https://code.videolan.org/videolan/x264.git |
| pkgconf 3.0.7, build tool only | https://distfiles.ariadne.space/pkgconf/pkgconf-3.0.7.tar.xz |

```text
ffmpeg-9.0.2.tar.xz SHA256:
8c3850283eb25fa026482078a04051e0be17347b09ef81a0849bec15a96e002e

x264 Git commit:
b35605ace3ddf7c1a5d67a2eb553f034aef41d55

pkgconf-3.0.7.tar.xz SHA256:
c926ff491cbd9a331a589160811bd97ab1749b4d5198a519338f2cdfabe6940a
```

The FFmpeg project's official source and release verification instructions are
at https://ffmpeg.org/download.html. The cross-check formulas are
https://raw.githubusercontent.com/Homebrew/homebrew-core/master/Formula/f/ffmpeg.rb,
https://raw.githubusercontent.com/Homebrew/homebrew-core/master/Formula/x/x264.rb,
and https://raw.githubusercontent.com/Homebrew/homebrew-core/master/Formula/p/pkgconf.rb.
The checked hashes and commit above remain fixed even if those formulas change.

## Configuration and distribution

FFmpeg and libx264 are statically linked into the two executables. Only macOS
system libraries/frameworks remain dynamically linked. Optional third-party
libraries are not autodetected. x264 builds its 8-bit 4:2:0 encoder with
interlacing support; FFmpeg retains its built-in decoders, encoders and filters,
including MPEG-2, AC-3, FFV1, PNG, scale, colorspace, bwdif and tinterlace.
The build does not enable FFmpeg's optional hardware acceleration libraries.

The recipe explicitly enables `--enable-gpl --enable-libx264`; FFmpeg reports
`GPL version 2 or later`. Preserve its and x264's corresponding source and
license notices when distributing the executables. The application's GPLv3
license does not replace upstream notices. pkgconf is an ISC-licensed build
tool and is not copied into the application. All original license texts remain
in the source archives as well as the separate notice directory.

This recipe specifies the source revision, build options, Apple SDK and
compiler record. It does not claim byte-for-byte reproducibility across
different Apple toolchains, or claim Apple's toolchain itself is included.

## Validation boundary

```sh
python3 scripts/build_ffmpeg.py --work-root ./community-build-work --validate-only
```

Validation checks required encoders and filters, `scale`'s `reset_sar` option,
dynamic library paths, and absence of personal absolute paths in the binaries.
It encodes and decodes synthetic DVD MPEG-2/AC-3 and 1080i H.264/AC-3 samples,
checks decoded interlaced frames, exercises FFV1/NUT cache and PNG paths, and
never reads customer media. These tests do not certify an authored ISO or a
physical disc's compatibility with any standalone player. The application
still needs its own integration and physical-disc testing.
