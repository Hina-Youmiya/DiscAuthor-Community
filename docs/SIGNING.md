# Developer ID release / 正式签名与公证

**This workflow is optional.** An Apple Developer membership or Developer ID
certificate is not a prerequisite for publishing open source or offering an
unnotarized download. Those free distribution routes remain available; see the
project's installation/build instructions. This document covers only the optional
Developer ID signing and notarization route for a smoother Gatekeeper experience.

**本流程完全可选。开源发布及提供未经公证的下载包都不要求付费会员或 Developer ID
证书。** 没有证书不会阻止免费发布路线；安装方法见项目的安装及构建说明。
本文只介绍可选的 Developer ID 签名与公证流程。

This is a release procedure, not evidence of an already notarized build. The
script defaults to read-only preflight. It never installs an app, creates an
Apple account, buys membership, exports a private key, or stores a password.
Only `--stage notarize` uploads the prepared distribution to Apple.

这是正式发布流程说明，**不表示当前版本已通过 Apple 公证**。默认仅检查。
脚本只修改输出目录内的副本，不修改输入应用、已安装应用或用户项目。

## Prerequisites / 前提

- An Apple Developer Program membership and a valid **Developer ID Application**
  certificate with its matching private key in your macOS Keychain. An ad-hoc,
  Apple Development, or Developer ID Installer identity is not a substitute.
- macOS, Python 3.10+, and selected Xcode command line tools providing `codesign`,
  `notarytool`, `stapler`, `ditto`, `hdiutil`, and `spctl`.
- A complete, tested `.app` built from the public source. Complete all binary,
  install-name, resource, and Info.plist changes before signing. Use a separate
  **APFS or Mac OS Extended** output directory, not exFAT. Only copy completed
  ZIP/DMG artifacts to other filesystems.

需要 Apple Developer Program 会员，以及钥匙串中的 **Developer ID Application**
证书和对应私钥。没有证书仍可执行 `prepare`，但不能执行正式签名或公证。
先完成公开版构建、素材与许可证审核，再进入此流程。

List valid signing identities yourself; this lists certificate identifiers, not
private keys:

```sh
security find-identity -v -p codesigning
```

Set up notarization credentials yourself in Keychain. The following command
prompts for the app-specific password; do not put it into a script, source
control, command argument, or this document. The release script uses only the
profile name. Keychain may ask you to authorize use of the signing key.

公证凭据由用户在钥匙串内设置。下面的命令会交互式询问专用密码，不在脚本中填写密码。

```sh
xcrun notarytool store-credentials "DiscAuthor-notary" \
  --apple-id "YOUR_APPLE_ACCOUNT" --team-id "YOUR_TEAM_ID"
```

Apple documents this interactive setup in
[TN3147](https://developer.apple.com/documentation/technotes/tn3147-migrating-to-the-latest-notarization-tool).

## Run each phase / 分阶段执行

Run from the public source root. Replace the app path and identity with your own
values. Use a **new output directory for each release and container format**.
Keep `--app`, `--output-dir`, and `--format` identical across phases.

```sh
python3 scripts/sign_release.py --help

# zsh/bash array: contains paths/profile names, never passwords.
release_args=(
  --app "/path/to/build/DiscAuthor.app"
  --identity "Developer ID Application: YOUR NAME (YOUR_TEAM_ID)"
  --keychain-profile "DiscAuthor-notary"
  --output-dir "$HOME/DiscAuthor-releases/0.4.15-zip"
  --format zip
)

python3 scripts/sign_release.py "${release_args[@]}"                  # preflight only
python3 scripts/sign_release.py "${release_args[@]}" --stage prepare # staging copy
python3 scripts/sign_release.py "${release_args[@]}" --stage sign    # uses certificate

# Test the signed app in OUTPUT_DIR/work/DiscAuthor.app before uploading.
python3 scripts/sign_release.py "${release_args[@]}" --stage notarize # uploads to Apple
python3 scripts/sign_release.py "${release_args[@]}" --stage archive  # final archive + SHA-256
```

`--identity` also accepts the exact certificate SHA-1 from `security find-identity`.
`--help` runs no external command. Preflight reads local identity metadata but
does not sign, contact the notary service, inspect password values, or change files.
It exits with code 2 if no matching certificate is available; this is expected
on a machine without a configured Developer ID identity. Credential validity is
checked by Apple when the explicitly requested notarization runs.

先执行检查、准备副本和签名，测试副本后再明确执行上传。默认检查不验证公证账户登录。
`prepare` 不需要证书；`sign` 需要证书和网络时间戳；`notarize` 需要现有公证配置及网络。
任何检查失败都会以非零状态停止，不会标记为发布成功。

## What is signed / 签名范围与权限

The script enumerates Mach-O files across the whole bundle, including the Swift
frontend, PyInstaller backend, Python extensions/framework, command line tools,
libbluray, and VLC libraries/plugins under Resources. It signs individual code
items first, then nested bundles and finally the outer app. Symlinks must resolve
inside the app. `--deep` is used for verification only. All distributed Mach-O
files receive the same Developer ID and secure timestamp; executable processes
also receive Hardened Runtime. Each file is verified, including code stored in
resource directories. This follows Apple's
[distribution signing guidance](https://developer.apple.com/documentation/xcode/creating-distribution-signed-code-for-the-mac).

No entitlement exception is enabled by default. In particular, the script does
**not disable library validation**: embedded VLC/Python code is re-signed with the
host's Team ID. Apple allows Apple-signed or same-Team-ID libraries under
[library validation](https://developer.apple.com/documentation/bundleresources/entitlements/com.apple.security.cs.disable-library-validation).
External replacement libraries with other signatures will not pass this policy.

PyInstaller and `ctypes` alone do not establish a need for JIT or unsigned
executable memory. Test the actual backend, libbluray file access, disc device
discovery, export, VLC playback and DVD/BD menus under Hardened Runtime. The
backend uses `ctypes` function pointers, so test these paths with the exact
bundled Python/libffi versions. If testing proves an entitlement is necessary,
review and scope it to the relevant executable in a new pipeline revision;
never apply exceptions to all libraries. Apple's
[Hardened Runtime guide](https://developer.apple.com/documentation/security/hardened-runtime)
explains that libraries inherit their host process's entitlements. No App Sandbox,
debug entitlement, DYLD environment exception, JIT exception, or blanket memory
exception is added here.

默认不额外放宽权限。动态库与主程序使用相同 Team ID；不要为解决未签名动态库的问题
直接关闭库验证。正式证书可用后，仍需实测后端、播放、导出及光盘相关功能。

## ZIP and DMG order / 容器顺序

For ZIP: sign and verify the app → create a submission ZIP → submit and require
`Accepted` → download/check the notary log → staple/validate the **app** → run
Gatekeeper assessment → rebuild the final ZIP from that stapled app. The final
ZIP is extracted and its app verified again before the archive is published.
A ZIP itself cannot be signed or stapled. See Apple's
[custom notarization workflow](https://developer.apple.com/documentation/security/customizing-the-notarization-workflow).

For DMG: use `--format dmg` and a separate output directory. The script signs the
app, builds a UDZO DMG, signs the DMG with Developer ID Application, submits that
outer container, then staples/validates the DMG and assesses both app and DMG.
The archived DMG is that exact stapled image. It is not rebuilt after notarization.
The inner app's ticket is available from Apple's service; this flow staples the
distribution DMG. Test installation and first launch from the DMG, including an
offline test, on a clean Mac. See Apple's
[distribution packaging guide](https://developer.apple.com/documentation/xcode/packaging-mac-software-for-distribution)
and [DMG assessment command](https://developer.apple.com/library/archive/technotes/tn2206/).

ZIP 需先给应用附加票据再重建 ZIP。DMG 则先生成并签名外层映像，再提交和附加票据。
一个 ZIP 获得公证并不代表后来新建的 DMG 也已公证。不要把原有面向内部构建的
`package_dmg.py` 输出当作本流程的正式公证产物。

## Failures and release evidence / 失败处理与交付证据

`release-state.json` records phases, Team ID, submission ID and hashes. The script
rejects changed staged contents and refuses to overwrite an existing release.
An interrupted signing or partial packaging attempt requires a fresh output
directory. Never edit the signed app or the state file to bypass a failed check.

Pending notarization can be resumed by repeating `--stage notarize`; the recorded
submission ID is reused. If upload succeeded but its response was lost, the script
blocks duplicate uploads. Inspect your own history with
`xcrun notarytool history --keychain-profile DiscAuthor-notary`, identify the exact
submission, and repeat the notarize command with `--submission-id UUID`.
For rejection, inspect `notary-log.json`, fix the build, and start a new directory.
If a failure occurs after stapling modified an artifact, use a fresh directory;
never assume a stopped run completed. Logs may contain local paths and are for
release operators, not part of the public download.

Only successful `archive` writes `DiscAuthor-VERSION-macOS.zip` or `.dmg` and its
`.sha256` file. Keep logs and state as evidence; publish only the final archive,
checksum and applicable release/source/license materials. Notarization is not
functional testing. Test the exact downloaded artifact on another Mac with normal
Gatekeeper settings, including first launch, an upgrade, playback, authoring and
export. No actual Developer ID signature or notarization result is claimed by
this source package.

最终仅在 Apple 返回 `Accepted`、票据校验、签名校验及本机 Gatekeeper 检查全部通过后
生成发布归档和 SHA-256。仍须在另一台 Mac 测试实际下载文件，不要关闭 Gatekeeper。
