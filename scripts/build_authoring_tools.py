#!/usr/bin/env python3
"""Build the public DVD and ISO tools from pinned source archives (macOS arm64).

Requires Python 3.12+, CMake, Xcode Command Line Tools; no Homebrew prefix is
written. Build outputs are staged separately from the source application.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import urllib.request

SOURCES = {
    "freetype-2.13.3.tar.xz": ("https://download.savannah.gnu.org/releases/freetype/freetype-2.13.3.tar.xz", "0550350666d427c74daeb85d5ac7bb353acba5f76956395995311a9c6f063289"),
    "libpng-1.6.47.tar.xz": ("https://downloads.sourceforge.net/project/libpng/libpng16/1.6.47/libpng-1.6.47.tar.xz", "b213cb381fbb1175327bd708a77aab708a05adde7b471bc267bd15ac99893631"),
    "libdvdread-6.1.3.tar.bz2": ("https://download.videolan.org/pub/videolan/libdvdread/6.1.3/libdvdread-6.1.3.tar.bz2", "ce35454997a208cbe50e91232f0e73fb1ac3471965813a13b8730a8f18a15369"),
    "dvdauthor-0.7.2.tar.gz": ("https://downloads.sourceforge.net/project/dvdauthor/dvdauthor-0.7.2.tar.gz", "3020a92de9f78eb36f48b6f22d5a001c47107826634a785a62dfcd080f612eb7"),
    "libbluray-1.3.4.tar.bz2": ("https://download.videolan.org/pub/videolan/libbluray/1.3.4/libbluray-1.3.4.tar.bz2", "478ffd68a0f5dde8ef6ca989b7f035b5a0a22c599142e5cd3ff7b03bbebe5f2b"),
    "cdrtools-3.02a09.tar.gz": ("https://downloads.sourceforge.net/project/cdrtools/alpha/cdrtools-3.02a09.tar.gz", "c7e4f732fb299e9b5d836629dadf5512aa5e6a5624ff438ceb1d056f4dcb07c2"),
    "schilytools-2024-03-21.tar.gz": ("https://codeberg.org/schilytools/schilytools/archive/2024-03-21.tar.gz", "fd3db00278caecd94b3802e1f903bf235a7137511ac0a450b31d855e3a52917b"),
}


def digest(path):
    with open(path, "rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


class Builder:
    def __init__(self, args):
        self.args = args
        # Use a caller-provided generic absolute alias when an external disk's
        # real path includes spaces. Do not resolve that alias for build tools.
        self.work = Path(os.path.abspath(args.work))
        self.sources = Path(os.path.abspath(args.sources))
        self.output = Path(os.path.abspath(args.output))
        self.prefix = self.work / "prefix"
        self.src = self.work / "src"
        self.logs = self.work / "logs"
        for directory in (self.work, self.sources, self.output, self.src, self.logs, self.prefix):
            directory.mkdir(parents=True, exist_ok=True)
        self.env = {**os.environ, "MACOSX_DEPLOYMENT_TARGET": "15.0", "CC": "/usr/bin/clang", "CXX": "/usr/bin/clang++"}
        self.flags = "-O2 -g0 -mmacosx-version-min=15.0"
        # __FILE__ and debug info must not embed local customer/work paths.
        if any(character.isspace() for character in str(self.work)):
            raise ValueError("Use a generic build path without spaces (a symlink to an external disk is supported)")
        self.flags += f" -ffile-prefix-map={self.work}=/build/discauthor-authoring"
        self.env["CFLAGS"] = self.flags
        self.env["CXXFLAGS"] = self.flags
        self.env["PATH"] = str(self.prefix / "bin") + os.pathsep + self.env.get("PATH", "")
        self.jobs = str(min(2, max(1, args.jobs)))
        os.setpriority(os.PRIO_PROCESS, 0, max(10, os.getpriority(os.PRIO_PROCESS, 0)))

    def run(self, command, cwd, label, env=None):
        print(label, flush=True)
        with (self.logs / (label + ".log")).open("w") as log:
            result = subprocess.run([str(item) for item in command], cwd=cwd,
                                    env=env or self.env, stdout=log, stderr=subprocess.STDOUT)
        if result.returncode:
            raise RuntimeError(f"{label} failed; see {self.logs / (label + '.log')}")

    def extract(self, filename, directory):
        archive = self.sources / filename
        url, expected = SOURCES[filename]
        if not archive.is_file():
            temporary = archive.with_suffix(archive.suffix + ".partial")
            with urllib.request.urlopen(url, timeout=60) as response, temporary.open("wb") as out:
                shutil.copyfileobj(response, out)
            if digest(temporary) != expected:
                raise RuntimeError("Source hash mismatch: " + filename)
            temporary.replace(archive)
        if digest(archive) != expected:
            raise RuntimeError("Source hash mismatch: " + filename)
        target = self.src / directory
        if not target.is_dir():
            with tarfile.open(archive) as source:
                source.extractall(self.src, filter="data")
        return target

    def cmake_build(self, name, source, options):
        build = self.work / "build" / name
        self.run([self.args.cmake, "-S", source, "-B", build,
                  "-DCMAKE_BUILD_TYPE=Release", "-DCMAKE_OSX_DEPLOYMENT_TARGET=15.0",
                  "-DCMAKE_OSX_ARCHITECTURES=arm64", "-DCMAKE_INSTALL_PREFIX=" + str(self.prefix),
                  "-DCMAKE_C_FLAGS=" + self.flags, *options], self.work, name + "-configure")
        self.run([self.args.cmake, "--build", build, "--parallel", self.jobs], self.work, name + "-build")
        self.run([self.args.cmake, "--install", build], self.work, name + "-install")

    def autotools(self, name, source, options, extra_env=None):
        build = self.work / "build" / name
        build.mkdir(parents=True, exist_ok=True)
        env = {**self.env, **(extra_env or {})}
        self.run([source / "configure", "--prefix=" + str(self.prefix), *options], build, name + "-configure", env)
        self.run(["/usr/bin/make", "-j" + self.jobs], build, name + "-build", env)
        self.run(["/usr/bin/make", "install"], build, name + "-install", env)

    def dependencies(self):
        self.cmake_build("freetype", self.extract("freetype-2.13.3.tar.xz", "freetype-2.13.3"),
                         ["-DBUILD_SHARED_LIBS=OFF", "-DFT_DISABLE_BROTLI=ON", "-DFT_DISABLE_BZIP2=ON",
                          "-DFT_DISABLE_HARFBUZZ=ON", "-DFT_DISABLE_PNG=ON"])
        self.cmake_build("libpng", self.extract("libpng-1.6.47.tar.xz", "libpng-1.6.47"),
                         ["-DPNG_SHARED=OFF", "-DPNG_STATIC=ON", "-DPNG_TESTS=OFF", "-DPNG_TOOLS=OFF"])
        self.autotools("libdvdread", self.extract("libdvdread-6.1.3.tar.bz2", "libdvdread-6.1.3"),
                       ["--enable-static", "--disable-shared"])
        # dvdauthor 0.7.2 expects the pre-pkg-config FreeType helper.
        # Its complete content is generated here from our local static prefix.
        helper = self.prefix / "bin/freetype-config"
        helper.parent.mkdir(parents=True, exist_ok=True)
        helper.write_text('#!/bin/sh\ncase "$1" in\n'
                          f' --cflags) echo "-I{self.prefix}/include/freetype2" ;;\n'
                          f' --libs) echo "-L{self.prefix}/lib -lfreetype -lz" ;;\n'
                          ' *) exit 1 ;;\nesac\n')
        helper.chmod(0o755)
        sdk = subprocess.check_output(["/usr/bin/xcrun", "--show-sdk-path"], text=True).strip()
        env = {"CPPFLAGS": f"-I{self.prefix}/include -I{sdk}/usr/include/libxml2",
               "LDFLAGS": f"-L{self.prefix}/lib", "FREETYPECONFIG": str(self.prefix / "bin/freetype-config"),
               "LIBPNG_CFLAGS": f"-I{self.prefix}/include", "LIBPNG_LIBS": f"-L{self.prefix}/lib -lpng16 -lz",
               "PKG_CONFIG": "/usr/bin/false", "ac_cv_prog_MAGICKCONFIG": ""}
        self.autotools("dvdauthor", self.extract("dvdauthor-0.7.2.tar.gz", "dvdauthor"), [], env)
        self.autotools("libbluray", self.extract("libbluray-1.3.4.tar.bz2", "libbluray-1.3.4"),
                       ["--disable-bdjava-jar", "--disable-doxygen-doc", "--without-fontconfig",
                        "--without-freetype", "--without-libxml2", "--disable-examples"])
        for filename in ("dvdauthor", "spumux"):
            destination = self.output / "deps/bin" / filename
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self.prefix / "bin" / filename, destination)
            destination.chmod(0o755)
        destination = self.output / "deps/lib/libbluray.2.dylib"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.prefix / "lib/libbluray.2.dylib", destination)
        destination.chmod(0o755)
        self.run(["/usr/bin/install_name_tool", "-id", "@rpath/libbluray.2.dylib", destination], self.work, "libbluray-install-name")
        self.run(["/usr/bin/codesign", "--force", "--sign", "-", destination], self.work, "libbluray-adhoc-sign")

    def cdrtools(self):
        schily = self.extract("schilytools-2024-03-21.tar.gz", "schilytools")
        cdr = self.extract("cdrtools-3.02a09.tar.gz", "cdrtools-3.02")
        env = {**self.env, "CFLAGS": self.flags + " -DNO_SCANSTACK -Wno-implicit-int -Wno-implicit-function-declaration"}
        self.run(["/bin/sh", "./MAKE-all"], schily / "psmake", "smake-bootstrap", env)
        smake = schily / "psmake/smake"
        for directory in ("libschily", "smake"):
            self.run([smake], schily / directory, "smake-" + directory, env)
        smake = next((schily / "smake/OBJ").glob("*/smake"))
        # Profiling archives are not installed or used by mkisofs.
        for path in cdr.glob("lib*/*_p.mk"):
            if not path.name.startswith("._"):
                path.unlink(missing_ok=True)
        self.run([smake, "-j", self.jobs, "INS_BASE=" + str(self.prefix), "INS_RBASE=" + str(self.prefix),
                  "CFLAGS=" + env["CFLAGS"], "install"], cdr, "cdrtools-build-install", env)
        destination = self.output / "bin/mkisofs"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.prefix / "bin/mkisofs", destination)
        destination.chmod(0o755)

    def manifest(self):
        result = {"target": "arm64-apple-macos15.0", "max_parallel_jobs": int(self.jobs),
                  "built_from_source": True, "sources": [], "outputs": [], "licenses": [],
                  "source_modifications": ["No source code patch. cdrtools profiling makefiles lib*/*_p.mk are omitted from this build."],
                  "build_details": ["Static FreeType, libpng and libdvdread; dynamic libbluray with BD-J and optional font/XML libraries disabled.",
                                    "macOS SDK supplies libxml2, iconv and zlib; these are system dependencies, not bundled binaries.",
                                    "cdrtools uses its documented NO_SCANSTACK switch on Apple Silicon; no physical writing tools are shipped."]}
        result["sdk_version"] = subprocess.check_output(["/usr/bin/xcrun", "--show-sdk-version"], text=True).strip()
        result["compiler_version"] = subprocess.check_output(["/usr/bin/clang", "--version"], text=True).splitlines()[0]
        for name, (url, sha) in SOURCES.items():
            if (self.sources / name).is_file():
                result["sources"].append({"filename": name, "url": url, "sha256": sha})
        for name in ("bin/mkisofs", "deps/bin/dvdauthor", "deps/bin/spumux", "deps/lib/libbluray.2.dylib"):
            path = self.output / name
            if path.is_file():
                dependencies = subprocess.check_output(["/usr/bin/otool", "-L", path], text=True).splitlines()[1:]
                result["outputs"].append({"path": name, "sha256": digest(path),
                                          "dynamic_dependencies": [line.strip() for line in dependencies]})
        license_files = {
            "freetype": ("freetype-2.13.3", ["LICENSE.TXT", "docs/FTL.TXT", "docs/GPLv2.TXT"]),
            "libpng": ("libpng-1.6.47", ["LICENSE"]),
            "libdvdread": ("libdvdread-6.1.3", ["COPYING"]),
            "dvdauthor": ("dvdauthor", ["COPYING"]),
            "libbluray": ("libbluray-1.3.4", ["COPYING"]),
            "cdrtools": ("cdrtools-3.02", ["COPYING", "CDDL.Schily.txt", "GPL-2.0.txt", "LGPL-2.1.txt", "mkisofs/COPYING"]),
            "smake-build-tool": ("schilytools", ["COPYING", "CDDL.Schily.txt"]),
        }
        for component, (directory, files) in license_files.items():
            for filename in files:
                source = self.src / directory / filename
                if not source.is_file():
                    continue  # --only dependencies may not have extracted build tools yet.
                relative = Path(component) / filename.replace("/", "-")
                for base in (self.sources / "licenses", self.output / "licenses/authoring"):
                    target = base / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target)
                result["licenses"].append({"path": str(relative), "sha256": digest(source)})
        notice = ("Authoring tool notices\n\n"
                  "The application license does not replace each component license.\n"
                  "FreeType is used under the FreeType License (FTL).\n"
                  "Portions of this software are copyright © 1996–2024 The FreeType Project\n"
                  "(www.freetype.org). All rights reserved.\n"
                  "See the component directories for unchanged upstream license texts.\n")
        for base in (self.sources / "licenses", self.output / "licenses/authoring"):
            base.mkdir(parents=True, exist_ok=True)
            (base / "NOTICE.txt").write_text(notice)
        result["licenses"].append({"path": "NOTICE.txt", "sha256": digest(self.sources / "licenses/NOTICE.txt")})
        recipe = self.sources / "build_authoring_tools.py"
        shutil.copyfile(Path(__file__), recipe)
        result["recipe_sha256"] = digest(recipe)
        (self.sources / "authoring-build-manifest.json").write_text(json.dumps(result, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", required=True)
    parser.add_argument("--work", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--cmake", default="cmake")
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--only", choices=("all", "dependencies", "cdrtools"), default="all")
    args = parser.parse_args()
    if os.uname().sysname != "Darwin" or os.uname().machine != "arm64":
        parser.error("This recipe currently targets Apple Silicon macOS")
    builder = Builder(args)
    if args.only in ("all", "dependencies"):
        builder.dependencies()
    if args.only in ("all", "cdrtools"):
        builder.cdrtools()
    builder.manifest()


if __name__ == "__main__":
    main()
