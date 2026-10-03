#!/usr/bin/env python3
"""Build the Community FFmpeg toolchain from pinned, retained source.

macOS 15+ arm64, Apple Command Line Tools and Python 3.12+. No Homebrew,
system installation, existing application, or user media is modified.
"""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tarfile
import time
import urllib.request

FFMPEG_VERSION = "9.0.2"
X264_COMMIT = "b35605ace3ddf7c1a5d67a2eb553f034aef41d55"
SOURCES = {
    "ffmpeg-9.0.2.tar.xz": (
        "https://ffmpeg.org/releases/ffmpeg-9.0.2.tar.xz",
        "8c3850283eb25fa026482078a04051e0be17347b09ef81a0849bec15a96e002e"),
    "pkgconf-3.0.7.tar.xz": (
        "https://distfiles.ariadne.space/pkgconf/pkgconf-3.0.7.tar.xz",
        "c926ff491cbd9a331a589160811bd97ab1749b4d5198a519338f2cdfabe6940a"),
}
X264_REPOSITORY = "https://code.videolan.org/videolan/x264.git"
ALIAS = Path("/tmp/discauthor-community-build")


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def clean_sidecars(directory):
    # AppleDouble metadata is not upstream source. exFAT ._*.idx files also
    # confuse Git's packfile enumeration. Only our private build tree is used.
    for p in directory.rglob("._*"):
        if p.is_file() or p.is_symlink():
            p.unlink()


def validate(root):
    """Synthetic-only codec/filter validation; never reads customer media."""
    materials = root / "source-materials/ffmpeg"
    test = root / "ffmpeg/validation"
    test.mkdir(parents=True, exist_ok=True)
    ffmpeg = root / "toolchain/bin/ffmpeg"
    ffprobe = root / "toolchain/bin/ffprobe"
    results = {}
    def execute(name, args):
        command = ["/usr/bin/nice", "-n", "15", *map(str, args)]
        proc = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        (test / (name + ".log")).write_text(proc.stdout + proc.stderr)
        if proc.returncode:
            raise RuntimeError("Validation failed: " + name)
        return proc.stdout + proc.stderr
    for name, binary in (("ffmpeg", ffmpeg), ("ffprobe", ffprobe)):
        libs = execute(name + "-libraries", ["/usr/bin/otool", "-L", binary])
        dependencies = [line.strip().split(" (")[0] for line in libs.splitlines()[1:]]
        if any(not p.startswith(("/usr/lib/", "/System/Library/")) for p in dependencies):
            raise RuntimeError("Unexpected non-system dynamic library")
        raw = binary.read_bytes()
        forbidden = [str(root.resolve()).encode(), b"/Users/", b"/Volumes/"]
        if any(marker in raw for marker in forbidden):
            raise RuntimeError("Private absolute path embedded in " + name)
        results[name] = {"sha256": sha256(binary), "system_dylibs_only": dependencies,
                         "private_absolute_path_scan": "passed"}
    encoders = execute("encoders", [ffmpeg, "-hide_banner", "-encoders"])
    filters = execute("filters", [ffmpeg, "-hide_banner", "-filters"])
    for name in ("libx264", "mpeg2video", "ac3", "ffv1", "pcm_s16le", "png"):
        if name not in encoders:
            raise RuntimeError("Missing encoder " + name)
    for name in ("colorspace", "scale", "bwdif", "tinterlace", "hqdn3d", "convolution", "telecine"):
        if name not in filters:
            raise RuntimeError("Missing filter " + name)
    if "reset_sar" not in execute("scale-help", [ffmpeg, "-hide_banner", "-h", "filter=scale"]):
        raise RuntimeError("Missing scale reset_sar")
    base = [ffmpeg, "-hide_banner", "-nostdin", "-y", "-threads", "2", "-filter_threads", "2"]
    inputs = ["-f", "lavfi", "-i", "testsrc2=size=960x540:rate=30000/1001:duration=1",
              "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=1"]
    color = "colorspace=ispace=bt709:iprimaries=bt709:itrc=bt709:irange=tv:space=bt709:primaries=bt709:trc=bt709:range=tv:format=yuv420p:dither=fsb"
    bd = test / "bd-1080i.mkv"
    bd_filter = ("bwdif=mode=send_field:parity=tff:deint=all," + color +
                 ",scale=1920:1080:force_original_aspect_ratio=decrease:force_divisible_by=2:reset_sar=1:flags=lanczos+accurate_rnd," +
                 "pad=1920:1080:trunc((ow-iw)/4)*2:trunc((oh-ih)/4)*2,setsar=1," +
                 "hqdn3d=1.5:1.5:3:3,fps=60000/1001,tinterlace=mode=interleave_top:flags=cvlpf")
    params = "bluray-compat=1:force-cfr=1:aud=1:nal-hrd=vbr:vbv-maxrate=30000:vbv-bufsize=30000:ref=3:b-pyramid=strict:keyint=30:min-keyint=1:open-gop=0:slices=4:tff=1:weightp=0"
    execute("bd-encode", [*base, *inputs, "-vf", bd_filter, "-c:v", "libx264", "-threads:v", "2",
            "-preset", "medium", "-profile:v", "high", "-level:v", "4.1", "-b:v", "4000k",
            "-x264-params", params, "-flags", "+ilme+ildct", "-c:a", "ac3", "-b:a", "384k",
            "-ar", "48000", "-ac", "2", "-t", "1", bd])
    dvd = test / "dvd.vob"
    execute("dvd-encode", [*base, *inputs, "-target", "ntsc-dvd", "-vf", "scale=720:480,setsar=32/27",
            "-threads:v", "2", "-b:v", "4500k", "-maxrate:v", "8500k", "-bufsize:v", "1835008",
            "-aspect", "16:9", "-c:a", "ac3", "-ar", "48000", "-ac", "2", "-t", "1", dvd])
    for name, sample in (("bd", bd), ("dvd", dvd)):
        probe = execute(name + "-probe", [ffprobe, "-v", "error", "-show_streams", "-of", "json", sample])
        results[name] = json.loads(probe)
        execute(name + "-decode", [*base, "-v", "error", "-i", sample, "-f", "null", "-"])
    streams = results["bd"]["streams"]
    video = next(s for s in streams if s["codec_type"] == "video")
    if (video["codec_name"], video["width"], video["height"], video["level"], video["avg_frame_rate"]) != ("h264", 1920, 1080, 41, "30000/1001"):
        raise RuntimeError("BD encode properties failed")
    frames = json.loads(execute("bd-decoded-fields", [ffprobe, "-v", "error", "-select_streams", "v:0",
        "-read_intervals", "%+#3", "-show_frames", "-show_entries",
        "frame=interlaced_frame,top_field_first", "-of", "json", bd]))["frames"]
    if not frames or any(f.get("interlaced_frame") != 1 or f.get("top_field_first") != 1 for f in frames):
        raise RuntimeError("Decoded BD frames are not top-field-first interlaced")
    results["bd_decoded_field_check"] = frames
    cache = test / "filter-cache.nut"
    execute("cache-encode", [*base, "-i", dvd, "-c:v", "ffv1", "-threads:v", "2", "-c:a", "pcm_s16le", cache])
    execute("cache-decode", [*base, "-v", "error", "-i", cache, "-f", "null", "-"])
    png = test / "menu.png"
    execute("png-encode", [*base, "-f", "lavfi", "-i", "color=c=blue:size=320x180", "-frames:v", "1", png])
    execute("png-decode", [*base, "-v", "error", "-i", png, "-f", "null", "-"])
    results["status"] = "passed"
    results["scope"] = "Synthetic encoder/filter/container checks only; not physical DVD/BD player compatibility certification."
    (materials / "validation.json").write_text(json.dumps(results, indent=2) + "\n")
    print("Synthetic FFmpeg validation passed", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--jobs", type=int, default=2, choices=range(1, 7))
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise SystemExit("This build recipe targets Apple Silicon macOS only")
    real_root = args.work_root.resolve()
    real_root.mkdir(parents=True, exist_ok=True)
    if ALIAS.is_symlink():
        if ALIAS.resolve() != real_root:
            raise SystemExit("Build alias already belongs to another directory")
    elif ALIAS.exists():
        raise SystemExit("Build alias is occupied; it will not be replaced")
    else:
        ALIAS.symlink_to(real_root, target_is_directory=True)
    if args.validate_only:
        validate(ALIAS)
        return
    work = ALIAS / "ffmpeg"
    source = work / "source"
    prefix = work / "prefix"
    materials = ALIAS / "source-materials/ffmpeg"
    archives = materials / "archives"
    logs = materials / "logs"
    output = ALIAS / "toolchain/bin"
    for p in (source, prefix, archives, logs, output, work / "tmp", materials / "licenses"):
        p.mkdir(parents=True, exist_ok=True)
    # Restrict discovery to Apple tools and this private prefix. The generic
    # symlink paths prevent user/customer directories appearing in -version.
    # Do not inherit session identifiers, private identity values or developer
    # package search paths: configure records its entire shell environment.
    env = {}
    env.update(PATH=f"{prefix}/bin:/usr/bin:/bin:/usr/sbin:/sbin",
               TMPDIR=str(work / "tmp"), COPYFILE_DISABLE="1",
               MACOSX_DEPLOYMENT_TARGET="15.0", LC_ALL="C",
               PKG_CONFIG_PATH=str(prefix / "lib/pkgconfig"),
               PKG_CONFIG_LIBDIR=str(prefix / "lib/pkgconfig"),
               PKG_CONFIG_ALLOW_SYSTEM_CFLAGS="1", PKG_CONFIG_ALLOW_SYSTEM_LIBS="1",
               CFLAGS="-O2 -g0 -mmacosx-version-min=15.0",
               LDFLAGS="-mmacosx-version-min=15.0")
    commands = []
    def run(command, cwd, name):
        commands.append({"argv": [str(x) for x in command], "cwd": str(cwd)})
        (materials / "commands.json").write_text(json.dumps(commands, indent=2) + "\n")
        print(name, flush=True)
        with (logs / (name + ".log")).open("w") as log:
            subprocess.run([str(x) for x in command], cwd=cwd,
                           env={**env, "PWD": str(cwd)}, check=True,
                           stdout=log, stderr=subprocess.STDOUT)

    for filename, (url, expected) in SOURCES.items():
        archive = archives / filename
        if not archive.exists():
            print("download " + filename, flush=True)
            temporary = archive.with_suffix(archive.suffix + ".part")
            with urllib.request.urlopen(url, timeout=90) as response, temporary.open("wb") as dest:
                shutil.copyfileobj(response, dest)
            temporary.replace(archive)
        if sha256(archive) != expected:
            raise SystemExit("Source checksum mismatch: " + filename)
        dirname = filename.removesuffix(".tar.xz")
        if not (source / dirname).exists():
            with tarfile.open(archive, "r:xz") as tar:
                tar.extractall(source, filter="data")

    x264 = source / "x264"
    if not x264.exists():
        run(["git", "clone", "--no-checkout", X264_REPOSITORY, x264], work, "x264-clone")
    clean_sidecars(x264)
    run(["git", "checkout", "--detach", X264_COMMIT], x264, "x264-checkout")
    found = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=x264, env=env, text=True).strip()
    if found != X264_COMMIT:
        raise SystemExit("Wrong x264 revision")
    # Preserve Git history as version.sh derives its version from revision
    # counts; a plain source tar alone does not reproduce that version string.
    clean_sidecars(x264)
    bundle = archives / f"x264-{X264_COMMIT}.bundle"
    if not bundle.exists():
        run(["git", "bundle", "create", bundle, "--all"], x264, "x264-source-bundle")
    tar_path = archives / f"x264-{X264_COMMIT}.tar"
    if not tar_path.with_suffix(".tar.gz").exists():
        run(["git", "archive", "--format=tar", f"--prefix=x264-{X264_COMMIT}/",
             f"--output={tar_path}", X264_COMMIT], x264, "x264-source-archive")
        with tar_path.open("rb") as inp, tar_path.with_suffix(".tar.gz").open("wb") as out:
            with gzip.GzipFile(filename="", mode="wb", fileobj=out, mtime=0) as gz:
                shutil.copyfileobj(inp, gz)
        tar_path.unlink()

    pkgconf = source / "pkgconf-3.0.7"
    if not (prefix / "bin/pkgconf").exists():
        run([pkgconf / "configure", f"--prefix={prefix}", "--disable-shared", "--enable-static",
             "--disable-dependency-tracking"], pkgconf, "pkgconf-configure")
        run(["nice", "-n", "15", "make", f"-j{args.jobs}"], pkgconf, "pkgconf-build")
        run(["make", "install"], pkgconf, "pkgconf-install")
    if not (prefix / "bin/pkg-config").exists():
        (prefix / "bin/pkg-config").symlink_to("pkgconf")

    xb = work / "build-x264"
    xb.mkdir(exist_ok=True)
    run([x264 / "configure", f"--prefix={prefix}", "--enable-static", "--disable-cli",
         "--disable-opencl", "--bit-depth=8", "--chroma-format=420",
         "--extra-cflags=-O2 -g0 -mmacosx-version-min=15.0",
         "--extra-ldflags=-mmacosx-version-min=15.0"], xb, "x264-configure")
    run(["nice", "-n", "15", "make", f"-j{args.jobs}"], xb, "x264-build")
    run(["make", "install-lib-static", "install-lib-dev"], xb, "x264-install")

    fb = work / "build-ffmpeg"
    fb.mkdir(exist_ok=True)
    ffsource = source / "ffmpeg-9.0.2"
    run([ffsource / "configure", "--prefix=/usr/local", "--arch=arm64", "--target-os=darwin",
         "--cc=clang", "--disable-autodetect", "--disable-shared", "--enable-static",
         "--enable-gpl", "--enable-libx264", "--enable-pthreads", "--enable-zlib",
         "--disable-debug", "--disable-doc", "--disable-ffplay",
         "--pkg-config=pkg-config", "--pkg-config-flags=--static",
         "--extra-cflags=-O2 -g0 -mmacosx-version-min=15.0",
         "--extra-ldflags=-mmacosx-version-min=15.0"], fb, "ffmpeg-configure")
    run(["nice", "-n", "15", "make", f"-j{args.jobs}", "ffmpeg", "ffprobe"], fb, "ffmpeg-build")
    for name in ("ffmpeg", "ffprobe"):
        shutil.copyfile(fb / name, output / name)
        (output / name).chmod(0o755)
        run(["strip", "-x", output / name], work, name + "-strip")
        run(["codesign", "--force", "--sign", "-", output / name], work, name + "-adhoc-sign")
        run([output / name, "-version"], work, name + "-version")
        run(["otool", "-L", output / name], work, name + "-libraries")
    notices = [(p, "FFmpeg-" + p.name) for p in ffsource.glob("COPYING.*")]
    notices += [(ffsource / "LICENSE.md", "FFmpeg-LICENSE.md"),
                      (x264 / "COPYING", "x264-COPYING"),
                      (pkgconf / "COPYING", "pkgconf-COPYING")]
    for src, name in notices:
        shutil.copyfile(src, materials / "licenses" / name)
        packaged_notices = ALIAS / "toolchain/licenses/ffmpeg"
        packaged_notices.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, packaged_notices / name)
    # bash initializes HOSTNAME even for a clean environment. It has no
    # relevance to compiling FFmpeg and must not identify the build operator.
    config_log = (fb / "ffbuild/config.log").read_text()
    if platform.node():
        config_log = config_log.replace(platform.node(), "build-host")
    (logs / "ffmpeg-config.log").write_text(config_log)
    shutil.copyfile(fb / "config.h", materials / "ffmpeg-config.h")
    shutil.copyfile(xb / "config.mak", materials / "x264-config.mak")
    shutil.copyfile(Path(__file__), materials / "build_ffmpeg.py")
    manifest = {
        "ffmpeg": FFMPEG_VERSION, "x264_commit": X264_COMMIT,
        "x264_repository": X264_REPOSITORY, "pkgconf": "3.0.7",
        "target": "arm64-apple-macosx15.0", "jobs": args.jobs,
        "clang": subprocess.check_output(["clang", "--version"], env=env, text=True),
        "sdk": subprocess.check_output(["xcrun", "--show-sdk-version"], env=env, text=True).strip(),
        "sources": {n: {"url": u, "sha256": h} for n, (u, h) in SOURCES.items()},
        "archive_sha256": {p.name: sha256(p) for p in archives.iterdir()
                           if p.is_file() and not p.name.startswith("._")},
        "binary_sha256": {n: sha256(output / n) for n in ("ffmpeg", "ffprobe")},
        "recipe_sha256": sha256(Path(__file__)),
        "diagnostic_redaction": "Host name in configure log replaced with build-host; build options unchanged",
        "note": "New source build; not a reproduction of an earlier opaque binary."
    }
    (materials / "build-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    validate(ALIAS)
    print("Build and synthetic validation completed.", flush=True)


if __name__ == "__main__":
    main()
