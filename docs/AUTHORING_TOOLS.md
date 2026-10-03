# Building the authoring tools

The community build uses source-built DVD and ISO tools. It does not reuse a
package-manager bottle or the internal application's binaries. The application
license does not replace the individual tools' licenses.

## Requirements

- Apple Silicon Mac, macOS 15 or later, Xcode Command Line Tools.
- Python 3.12 or later and CMake available on PATH (or pass `--cmake`).
- Internet access for a first build, or the exact source archives listed below.

From the public source checkout:

```sh
python3 scripts/build_authoring_tools.py \
  --sources source-materials/authoring \
  --work /tmp/discauthor-authoring-build \
  --output toolchain \
  --jobs 2
```

The script downloads missing source archives and checks their pinned SHA-256
before extraction. Supplying the same archives in `--sources` supports offline
builds. It installs only into its build prefix and the requested output folder;
it does not install anything into `/usr/local`, Homebrew or system settings.
Build processes use nice priority 10 and at most two compilation jobs. A generic
build path without whitespace is required by the older upstream build systems;
a symlink to an external working folder can be used. Source and output paths
may otherwise be selected by the caller.

The tools are staged at:

- `toolchain/bin/mkisofs`
- `toolchain/deps/bin/dvdauthor`
- `toolchain/deps/bin/spumux`
- `toolchain/deps/lib/libbluray.2.dylib`
- `toolchain/licenses/authoring/`

The separate navigation-tool recipe builds tsMuxer and `reexport-m2ts`. It can
use the static FreeType library from this recipe's `prefix` directory.

## Pinned sources

| Source | Version | Upstream |
| --- | --- | --- |
| cdrtools (mkisofs) | 3.02a09 | [cdrtools releases](https://sourceforge.net/projects/cdrtools/files/alpha/) |
| Schily Tools (build-time smake) | 2024-03-21 | [maintainer archive](https://codeberg.org/schilytools/schilytools) |
| dvdauthor / spumux | 0.7.2 | [DVD Authoring Tools](https://sourceforge.net/projects/dvdauthor/files/) |
| libdvdread | 6.1.3 | [VideoLAN releases](https://download.videolan.org/pub/videolan/libdvdread/6.1.3/) |
| libbluray | 1.3.4 | [VideoLAN releases](https://download.videolan.org/pub/videolan/libbluray/1.3.4/) |
| FreeType | 2.13.3 | [FreeType releases](https://download.savannah.gnu.org/releases/freetype/) |
| libpng | 1.6.47 | [libpng releases](https://sourceforge.net/projects/libpng/files/libpng16/1.6.47/) |

Exact download URLs and SHA-256 values are in `SOURCES` in the recipe and in the
resulting `source-materials/authoring/authoring-build-manifest.json`. Retain that
manifest, all original source archives, the copied build recipe and licenses
alongside a binary distribution. Do not substitute a different version's source
for an existing binary. The complete cdrtools archive, including its support
libraries and build files, is retained; mkisofs is not presented as a standalone
single-source-file project.

## Configuration and modifications

The target is `arm64-apple-macos15.0`. FreeType, libpng and libdvdread are built
as static dependencies. FreeType's optional PNG, Brotli, bzip2 and HarfBuzz
integrations are disabled. The SDK provides system zlib, libxml2 and iconv;
these system libraries are dynamically referenced, not copied into the app.
The recipe supplies the small legacy `freetype-config` helper required by
DVD Authoring Tools 0.7.2; its complete generated content is in the recipe.
Optional GraphicsMagick, Fontconfig and FriBidi integrations are not selected.

libbluray is a dynamic library with BD-J, optional font/XML dependencies and
example programs disabled. Its install name is changed to
`@rpath/libbluray.2.dylib`, then it is ad-hoc signed again. Keep this library
replaceable when packaging; do not statically relink it into the application.

cdrtools uses the upstream-documented `NO_SCANSTACK` switch for the legacy stack
scanner, and compiler compatibility warning flags for its older C sources.
Profiling-only `lib*/*_p.mk` build files are omitted, following
[Homebrew’s published cdrtools build recipe](https://github.com/Homebrew/homebrew-core/blob/master/Formula/c/cdrtools.rb). No application source code patch is applied. The recipe builds smake
from its full pinned source archive. Only mkisofs is staged for the application;
cdrecord and other physical recording utilities are not distributed by this
recipe.

Compiler debug information is disabled and file-prefix mapping removes the
generic build root from embedded source filenames. Check staged binaries for
private paths before distribution. The build manifest records output hashes,
SDK/compiler versions and dynamic dependencies. Rebuilding under a different
SDK or signing identity is not claimed to produce bit-identical binaries.

## Licenses and validation limits

Original notices are copied unchanged into `toolchain/licenses/authoring/` and
`source-materials/authoring/licenses/`. cdrtools contains GPL and CDDL components;
its complete `COPYING`, GPL, LGPL and CDDL texts are retained. DVD Authoring
Tools and libdvdread retain their GPL notices; libbluray retains its LGPL
notice. FreeType's FTL and alternative GPL texts, and libpng's license, are
retained. FreeType is distributed under the FreeType License (FTL). Portions
of this software are copyright © 1996–2024 The FreeType Project
(www.freetype.org). All rights reserved. smake is a build tool and its
notices/source are also retained.

A successful build and dependency check establish that these particular tools
can be built and loaded. They do not certify the resulting optical disc or
resolve the outstanding physical Blu-ray player compatibility issues. The
application's separate synthetic export tests and physical playback checks
must still be reported accurately.
