# Free distribution without an Apple certificate

**Publishing source code and distributing a macOS app do not require paying Apple.** Developer ID signing and notarization are optional ways to improve the first-run experience. This project can provide source and a self-contained, ad-hoc-signed app without a paid certificate. “Ad-hoc signed” is not an Apple-verified developer identity and does not mean notarized.

DiscAuthor Community 0.4.16 is a GPL-3.0-only source preview with a locally tested application build. A complete application bundle has been built, and its packaged backend passed four synthetic menu/no-menu DVD/BD export cases. Two actual native GUI startup and shutdown regression runs passed, leaving no backend process running. A limited native UI/playback smoke test also passed: the Japanese interface displayed normally, DVD/BD ISO menus opened, direction/confirm actions played moving test footage, and the app exited normally. This does not establish all-feature or physical-burn success. A clean second Mac has not been verified. This repository publishes the source preview; no public binary installer is attached.

## Installing an unnotarized test build

1. Obtain the app and matching SHA-256 from the project maintainer. The source preview release does not include an installer; these instructions apply to a build supplied separately. Check its checksum before installing.
2. Extract the ZIP, or open the DMG if one is supplied, and move `DiscAuthor Community.app` to Applications. The current target is Apple Silicon and macOS 15+.
3. Try to open it. If macOS says the developer cannot be verified or Apple cannot check it, close the alert.
4. For a copy you trust, open **System Settings → Privacy & Security → Open Anyway**, then confirm **Open**. macOS remembers this exception.

Apple describes this per-app procedure in [Safely open apps on your Mac](https://support.apple.com/en-us/102445). An administrator-managed Mac may not permit exceptions. A damaged-file or malware alert is a different problem: do not treat it as a routine unsigned-app warning. Re-download/verify or report it instead. No global Gatekeeper disabling or quarantine-removal command is needed in these instructions.

A complete packaged build includes its runtime tools; users do not need Python, Homebrew or a separate encoder. Building from source is documented separately in [BUILD.md](BUILD.md).

The known Panasonic DMP-BD65 playback failure remains unresolved. Passing backend export tests or allowing an app through Gatekeeper does not establish standalone-player compatibility. The VLC runtime is from the verified official 3.0.23 image with identified source materials retained; mapping and reproducing all 88 selected plugins has not been completed, so full reproducibility is not claimed.

## 中文说明

免费开源、免费提供安装包都不需要 Apple 开发者证书。没有公证的安装包首次运行可能被 macOS 拦住；确认来源后，在“系统设置 → 隐私与安全性 → 仍要打开”中允许这个应用即可。不要关闭整个系统的安全检查。受公司管理员管控的电脑可能不允许手动例外。

目前 0.4.16 Community 的完整应用包已在本机构建，打包后的后端已通过 DVD／BD 各自有菜单、单视频无菜单共四种 ISO 导出测试。两轮实际原生界面启动与退出回归已通过，退出后没有后端进程残留。有限原生界面／播放冒烟测试也已通过：默认日语正常，DVD／BD 菜单 ISO 可显示菜单，方向键加确认可进入动态测试视频；BD 菜单可观察到白色普通文字、黄色焦点随向下操作移动，正常退出代码为 0。弹出／收起按钮可见，但本次没有实测托盘动作或刻录，不代表全功能验证完成。另一台干净 Mac 未验证。GitHub 首版公开源码和已收集的依赖源码材料，未附公开安装包。DMP-BD65 的播放兼容问题仍未解决。应用采用 GPL-3.0-only；正式签名、公证可以以后按需增加。
