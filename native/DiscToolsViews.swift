import SwiftUI

private func discText(_ language: String, _ ja: String, _ zh: String, _ en: String) -> String {
    language == "zh" ? zh : language == "en" ? en : ja
}
private func discSize(_ value: Int64?) -> String {
    value.map { ByteCountFormatter.string(fromByteCount: $0, countStyle: .decimal) } ?? "—"
}
private func discTime(_ seconds: Double) -> String {
    let s = max(0, Int(seconds.isFinite ? seconds : 0)); return String(format: "%d:%02d:%02d", s / 3600, s / 60 % 60, s % 60)
}

struct DiscToolsWorkspace: View {
    @EnvironmentObject private var model: DiscAuthorModel
    @ObservedObject var burn: DiscBurnState
    @ObservedObject var tools: DiscToolsState
    @State private var trayAction = ""
    private func tr(_ ja: String, _ zh: String, _ en: String) -> String { discText(model.language, ja, zh, en) }
    private var trayDisabled: Bool { tools.trayDeviceID.isEmpty || tools.protectsTermination || burn.protectsTermination }
    private var trayStatus: String? {
        if tools.trayBusy {
            return trayAction == "close"
                ? tr("トレイを閉じています…", "正在收起托盘…", "Closing the tray…")
                : tr("ディスクを取り出しています…", "正在弹出光盘…", "Ejecting the disc…")
        }
        if burn.protectsTermination || tools.protectsTermination {
            return tr("ディスクの作成・書き込み・検証が完了すると、トレイを操作できます。", "光盘制作、烧录或校验结束后可操作托盘。", "Tray controls are available when disc preparation, writing or verification finishes.")
        }
        if tools.trayDeviceID.isEmpty {
            return tr("光学ドライブを接続して選択すると、トレイを操作できます。", "连接并选择光驱后可操作托盘。", "Connect and select an optical drive to use the tray controls.")
        }
        return tools.trayMessage
    }
    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 12) {
            Picker(tr("ディスクの操作", "光盘操作", "Disc Action"), selection: $model.discToolTab) {
                Text(tr("ISO を書き込む", "ISO 烧录", "Burn ISO")).tag("iso")
                Text(tr("動画1本を書き込む", "单视频烧录", "Burn One Video")).tag("video")
                Text(tr("再生して確認", "播放验盘", "Play & Check")).tag("player")
            }.pickerStyle(.segmented).labelsHidden().frame(maxWidth: 510).accessibilityIdentifier("discToolTabs")
                Spacer(minLength: 8)
                HStack(spacing: 8) {
                    Button {
                        trayAction = "eject"
                        tools.controlTray("eject")
                    } label: {
                        Label(tr("ディスクを取り出す", "弹出光盘", "Eject Disc"), systemImage: "eject")
                    }.accessibilityIdentifier("discTrayEject")
                    Button {
                        trayAction = "close"
                        tools.controlTray("close")
                    } label: {
                        Label(tr("トレイを閉じる", "收起托盘", "Close Tray"), systemImage: "tray.and.arrow.down")
                    }.accessibilityIdentifier("discTrayClose")
                }.buttonStyle(.bordered).fixedSize(horizontal: true, vertical: false).disabled(trayDisabled)
            }.padding(.horizontal, 24).padding(.vertical, 16)
            if let message = trayStatus {
                HStack(spacing: 7) {
                    Spacer(minLength: 0)
                    if tools.trayBusy { ProgressView().controlSize(.small) }
                    Text(message).font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
                }.padding(.horizontal, 24).padding(.bottom, 10).accessibilityIdentifier("discTrayStatus")
            }
            Divider()
            if model.discToolTab == "video" { DirectVideoBurnWorkspace(tools: tools, burn: burn) }
            else if model.discToolTab == "player" { DiscPlaybackWorkspace(tools: tools, engine: tools.player, burn: burn) }
            else { BurnWorkspace(burn: burn) }
        }
    }
}

struct DirectVideoBurnWorkspace: View {
    @EnvironmentObject private var model: DiscAuthorModel
    @ObservedObject var tools: DiscToolsState
    @ObservedObject var burn: DiscBurnState
    private func tr(_ ja: String, _ zh: String, _ en: String) -> String { discText(model.language, ja, zh, en) }
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                VStack(alignment: .leading, spacing: 6) {
                    Text(tr("動画1本から再生できるディスクへ", "一段视频，直接制作可播放光盘", "One Video to a Playable Disc")).font(.title2.bold())
                    Text(tr("空のDVD／Blu-rayを自動判別し、メニューなしで自動再生するディスクを作成します。", "自动识别空白 DVD／Blu-ray，制作无菜单、插盘自动播放的光盘。", "Detect a blank DVD or Blu-ray and create an autoplay disc without a menu.")).foregroundStyle(.secondary)
                }
                if tools.direct.active, let job = tools.direct.job { DirectBurnProgress(tools: tools, job: job) }
                HStack(alignment: .top, spacing: 18) {
                    GroupBox {
                        VStack(alignment: .leading, spacing: 14) {
                            Label(tr("動画と再生", "视频与播放", "Video & Playback"), systemImage: "film").font(.headline)
                            TextField(tr("動画のパス", "视频路径", "Video Path"), text: $tools.video).textFieldStyle(.roundedBorder).accessibilityIdentifier("directVideoPath")
                            Button(tr("動画を1本選択…", "选择一个视频…", "Choose One Video…")) { tools.chooseVideo() }.accessibilityIdentifier("directChooseVideo")
                            Label(tr("自動再生・終了後に停止", "自动播放，结束后停止", "Autoplay · Stop at the End"), systemImage: "stop.circle").font(.callout).foregroundStyle(.secondary).accessibilityIdentifier("directEndAction")
                            if tools.endAction != "stop" {
                                Button(tr("再生後に停止へ変更", "改为播放结束后停止", "Change to Stop After Playback")) { tools.endAction = "stop" }
                            }
                            DisclosureGroup(tr("画質と作業フォルダ", "画质与临时目录", "Quality & Work Folder")) {
                            VStack(alignment: .leading, spacing: 14) {
                            Picker(tr("画質", "画质", "Picture Quality"), selection: $tools.quality) {
                                Text(tr("高画質", "高画质", "High Quality")).tag("high")
                                Text(tr("バランス", "均衡", "Balanced")).tag("balanced")
                                if tools.format == "bluray" {
                                    Text(tr("高効率（1パス・CPU）", "高效单遍（CPU）", "Efficient (Single Pass · CPU)")).tag("efficient")
                                } else if tools.quality == "efficient" && tools.format != "dvd" {
                                    Text(tr("高効率（Blu-rayを確認中）", "高效单遍（等待确认 Blu-ray）", "Efficient (Waiting for Blu-ray)")).tag("efficient").disabled(true)
                                }
                            }.accessibilityIdentifier("directEncodingQuality")
                            if tools.quality == "efficient" {
                                Text(tr("CPUによる1パスのソフトウェアエンコードです。ハードウェアエンコードは使用せず、処理速度と画質のバランスを重視します。", "使用 CPU 单遍软件编码，不使用硬件编码；在速度与画质之间取舍。", "Uses single-pass CPU software encoding, without hardware encoding, balancing speed and picture quality.")).font(.caption).foregroundStyle(.secondary)
                            }
                            Picker(tr("DVD 映像方式", "DVD 制式", "DVD Standard"), selection: $tools.standard) {
                                Text("NTSC · 16:9").tag("ntsc"); Text("PAL · 16:9").tag("pal")
                            }
                            Divider()
                            Text(tr("一時作業フォルダ", "临时工作文件夹", "Temporary Work Folder")).font(.headline)
                            TextField(tr("空き容量のあるフォルダ", "有足够空间的文件夹", "Folder with Sufficient Space"), text: $tools.workingDirectory).textFieldStyle(.roundedBorder).accessibilityIdentifier("directWorkingDirectory")
                            Button(tr("フォルダを選択…", "选择文件夹…", "Choose Folder…")) { tools.chooseWorkingDirectory() }
                            Text(tools.verifyAfterBurn ? tr("一時ISOは書き込みと検証の成功後に自動削除します。", "临时 ISO 在烧录及校验成功后自动删除。", "The temporary ISO is removed after successful burning and verification.") : tr("検証を省略する場合、一時ISOは書き込み成功後に削除します。", "关闭校验时，写入成功后清理临时 ISO，并标记为未校验。", "With verification off, the temporary ISO is removed after writing and the result is marked unverified.")).font(.caption).foregroundStyle(.secondary)
                            }.padding(.top, 12)
                            }
                        }.padding(12).frame(maxWidth: .infinity, alignment: .leading)
                    }.disabled(tools.protectsTermination)
                    GroupBox {
                        VStack(alignment: .leading, spacing: 14) {
                            HStack {
                                Label(tr("空のディスク", "空白光盘", "Blank Disc"), systemImage: "opticaldiscdrive").font(.headline)
                                Spacer()
                                Button { tools.refreshDrives() } label: { Image(systemName: "arrow.clockwise") }.help(tr("再検出", "重新检测", "Refresh Drives")).disabled(tools.refreshing || tools.protectsTermination).accessibilityIdentifier("directRefreshDrives")
                            }
                            if tools.drives.devices.isEmpty {
                                Label(tr("光学ドライブが見つかりません", "未检测到外接光驱", "No Optical Drive Found"), systemImage: "cable.connector").foregroundStyle(.secondary)
                                Text(tr("書き込み対応ドライブを接続し、空のDVDまたはBlu-rayを入れてください。", "连接支持烧录的光驱，放入空白 DVD 或 Blu-ray。", "Connect a writable drive and insert a blank DVD or Blu-ray.")).font(.callout)
                            } else {
                                Picker(tr("光学ドライブ", "光驱", "Optical Drive"), selection: $tools.deviceID) {
                                    ForEach(tools.drives.devices) { device in Text(device.name).tag(device.id) }
                                }.accessibilityIdentifier("directDevicePicker")
                                if let media = tools.device?.media {
                                    if media.state == "unknown" {
                                        Label(tr("ディスクの状態を確認できません", "暂时无法确认光盘状态", "Disc Status Unavailable"), systemImage: "exclamationmark.circle").foregroundStyle(.orange)
                                        Text(tr("「再検出」でドライブの状態を確認してください。", "点击“重新检测”获取光驱状态。", "Choose Refresh Drives to check the drive again.")).font(.caption).foregroundStyle(.secondary)
                                    } else if media.isProbing {
                                        HStack(spacing: 8) {
                                            ProgressView().controlSize(.small)
                                            Text(tr("ディスクを確認中…", "正在识别光盘…", "Detecting Disc…")).foregroundStyle(.secondary)
                                        }
                                        Text(tr("ドライブからディスク情報が届くと自動更新します。", "收到光驱的盘片信息后会自动更新。", "Updates automatically when the drive reports the disc information.")).font(.caption).foregroundStyle(.secondary)
                                    } else if media.busy {
                                        Label(tr("光学ドライブは使用中です", "光驱正在使用中", "Drive in Use"), systemImage: "opticaldiscdrive").foregroundStyle(.secondary)
                                    } else {
                                        LabeledContent(tr("自動判別", "自动识别", "Detected Format"), value: media.present ? (media.format == "dvd" ? "DVD-Video" : media.format == "bluray" ? "Blu-ray Video" : tr("未対応", "不支持", "Unsupported")) : tr("ディスクなし", "未插入光盘", "No Disc"))
                                        LabeledContent(tr("使用可能な容量", "可用容量", "Available Space"), value: discSize(media.freeBytes))
                                        if media.present && !media.blank { Text(tr("空のディスクに交換してください。既存のデータは消去しません。", "请换入空白光盘；不会擦除已有内容。", "Insert a blank disc. Existing contents will not be erased.")).foregroundStyle(.orange) }
                                    }
                                }
                                Picker(tr("書き込み速度", "烧录速度", "Write Speed"), selection: $tools.speed) {
                                    Text(tr("自動", "自动", "Automatic")).tag(0.0)
                                    ForEach([2.0, 4.0, 6.0, 8.0, 12.0, 16.0], id: \.self) { speed in Text("\(Int(speed))×").tag(speed) }
                                }
                            }
                            Toggle(tr("書き込み後に検証する", "烧录后校验完整性", "Verify Disc After Burning"), isOn: $tools.verifyAfterBurn).accessibilityIdentifier("directVerifyAfterBurn")
                            Text(tr("ディスクを読み戻し、書き込んだデータをISOと照合します。最初から最後まで再生する必要はありません。", "自动回读光盘并与镜像核对写入数据，无需从头播放到尾。", "Read the disc back and compare the written data with the image, without watching the entire video.")).font(.caption).foregroundStyle(.secondary)
                            if let message = tools.drives.error { Text(message).foregroundStyle(.red).textSelection(.enabled) }
                            Spacer(minLength: 12)
                            Text(tr("開始前と書き込み直前に、ディスクの種類と容量を再確認します。作成中はディスクを交換しないでください。", "开始前和写入前均检查盘片类型与容量。制作过程中请勿更换盘片。", "Disc type and space are checked before preparing and before writing. Keep the same disc inserted throughout.")).font(.caption).foregroundStyle(.secondary)
                        }.padding(12).frame(maxWidth: .infinity, alignment: .leading)
                    }.disabled(tools.protectsTermination)
                }
                if model.queue.running || model.queue.active != nil { Label(tr("新しい動画の変換は ISO キューの完了後に開始できます。完成済み ISO の書き込み・再試行は並行できます。", "新视频的转换需等导出队列结束；已有 ISO 的烧录或仅重试烧录可以同时进行。", "Preparing a new video waits for the export queue. Burning an existing ISO or retrying only the burn can run alongside exports."), systemImage: "clock").foregroundStyle(.secondary) }
                if let check = tools.check {
                    if let p = check.project, check.valid {
                        Label(tr("準備完了", "检查通过", "Ready") + " · \(discTime(p.duration)) · \(check.format == "dvd" ? "DVD-Video" : "Blu-ray") · " + String(format: tr("一時空き容量 %.1f GB", "需临时空间 %.1f GB", "Temporary Space %.1f GB"), p.requiredGB), systemImage: "checkmark.shield.fill").foregroundStyle(.green)
                    }
                    if tools.device?.media.isProbing != true {
                        ForEach(check.errors, id: \.self) { Text($0).foregroundStyle(.red).textSelection(.enabled) }
                    }
                }
                if let error = tools.error { Text(error).foregroundStyle(.red).textSelection(.enabled) }
                if let error = tools.connectionError { Text(error).foregroundStyle(.red).textSelection(.enabled) }
                HStack(spacing: 16) {
                    Button(tr("開始前チェック", "烧录前检查", "Preflight Check")) { tools.preflight() }
                        .disabled(tools.protectsTermination || tools.isChecking || tools.video.isEmpty || tools.deviceID.isEmpty).accessibilityIdentifier("directPreflight")
                    Button { tools.start() } label: { Label(tr("作成して書き込む", "制作并烧录", "Prepare & Burn"), systemImage: "opticaldisc.fill") }
                        .buttonStyle(.borderedProminent).controlSize(.large).disabled(!tools.canStart || burn.protectsTermination || tools.endAction != "stop").accessibilityIdentifier("directStartBurn")
                    if tools.isChecking || tools.isStarting { ProgressView().controlSize(.small) }
                }
                Text(tr("「作成して書き込む」で選択した空のディスクへ自動的に書き込みます。", "点击“制作并烧录”后，软件会自动制作并写入所选空白光盘。", "Prepare & Burn automatically authors and writes to the selected blank disc.")).font(.caption).foregroundStyle(.secondary)
                if !tools.direct.active, let job = tools.direct.job { DirectBurnProgress(tools: tools, job: job) }
            }.padding(24).frame(maxWidth: 1080, alignment: .leading).frame(maxWidth: .infinity)
        }.onAppear { tools.refreshDrives() }
    }
}

private struct DirectBurnProgress: View {
    @EnvironmentObject private var model: DiscAuthorModel
    @ObservedObject var tools: DiscToolsState
    let job: DirectBurnJob
    private func tr(_ ja: String, _ zh: String, _ en: String) -> String { discText(model.language, ja, zh, en) }
    var body: some View {
        GroupBox {
            VStack(alignment: .leading, spacing: 12) {
                HStack {
                    Label(job.message, systemImage: job.verified ? "checkmark.shield.fill" : "opticaldisc")
                    Spacer()
                    Button(tr("ログ", "日志", "Log")) { tools.showLog() }
                }
                if let progress = job.progress {
                    HStack { ProgressView(value: progress, total: 100); Text(String(format: "%.1f%%", progress)).monospacedDigit().frame(width: 65, alignment: .trailing) }.accessibilityIdentifier("directBurnProgress")
                } else if tools.direct.active { ProgressView().controlSize(.small) }
                if tools.direct.active, let stageProgress = job.stageProgress {
                    Text(tr("現在の工程", "当前阶段", "Current Stage") + " · " + String(format: "%.1f%%", stageProgress)).font(.caption).monospacedDigit().accessibilityIdentifier("directBurnStageProgress")
                }
                if let error = job.error { Text(error).foregroundStyle(.red).textSelection(.enabled) }
                if let error = job.cleanupError { Text(error).foregroundStyle(.orange).textSelection(.enabled) }
                if job.verified { Text(tr("書き込み後の検証に合格しました。再生タブからディスクを確認できます。", "写后校验通过，可以切换到播放验盘检查光盘。", "Post-burn verification passed. Open Play & Check Disc to inspect playback.")).foregroundStyle(.green) }
                else if job.status == "complete" && job.verificationSkipped == true { Text(tr("書き込み完了・検証なし", "已烧录，未校验", "Burned — Not Verified")).foregroundStyle(.orange) }
                if job.canRetry {
                    Text(tr("作成済みの一時ISOを残しています。空のディスクに交換して再試行できます。", "已保留临时 ISO；换入空白盘后可直接重试烧录，无需再次压制。", "The temporary ISO is retained. Insert a blank disc and retry without encoding again.")).font(.caption)
                    Button(tr("書き込みのみ再試行", "仅重试烧录", "Retry Burning Only")) { tools.retry() }.disabled(!tools.canRetry).accessibilityIdentifier("directRetryBurn")
                }
                if tools.direct.active && job.burnID == nil && !["burning", "verifying", "finishing", "cleaning"].contains(job.stage) {
                    Button(tr("作成を中止", "取消制作", "Cancel Preparation")) { tools.cancel() }.accessibilityIdentifier("directCancel")
                }
            }.padding(10)
        }
    }
}

struct DiscPlaybackWorkspace: View {
    @EnvironmentObject private var model: DiscAuthorModel
    @ObservedObject var tools: DiscToolsState
    @ObservedObject var engine: DiscPlayerEngine
    @ObservedObject var burn: DiscBurnState
    private func tr(_ ja: String, _ zh: String, _ en: String) -> String { discText(model.language, ja, zh, en) }
    var body: some View {
        VStack(spacing: 14) {
            HStack(spacing: 12) {
                Picker(tr("挿入したディスク", "已插入光盘", "Inserted Disc"), selection: $tools.readDeviceID) {
                    if tools.discs.devices.isEmpty { Text(tr("光学ドライブなし", "未检测到光驱", "No Optical Drive")).tag("") }
                    ForEach(tools.discs.devices) { disc in Text(disc.name + (disc.label.isEmpty ? "" : " · " + disc.label)).tag(disc.id) }
                }.frame(maxWidth: 360).accessibilityIdentifier("discPlaybackDevice")
                Button { tools.refreshDiscs() } label: { Image(systemName: "arrow.clockwise") }.disabled(tools.refreshingDiscs || tools.trayBusy).accessibilityIdentifier("discPlaybackRefresh")
                if tools.refreshingDiscs { ProgressView().controlSize(.small) }
                Button(tr("ディスクを再生", "播放光盘", "Play Disc")) { tools.openInsertedDisc() }
                    .buttonStyle(.borderedProminent).disabled(tools.readDevice?.playable != true || tools.loadingDisc || tools.protectsTermination || burn.protectsTermination).accessibilityIdentifier("playInsertedDisc")
                Spacer()
                Button(tr("ISO／フォルダを開く…", "打开 ISO／文件夹…", "Open ISO / Folder…")) { tools.chooseDiscSource() }
                    .disabled(tools.loadingDisc || tools.protectsTermination || burn.protectsTermination).accessibilityIdentifier("playDiscSource")
            }
            if let disc = tools.readDevice, !disc.playable, let reason = disc.reason { Text(model.t(reason)).font(.callout).foregroundStyle(.secondary).frame(maxWidth: .infinity, alignment: .leading) }
            if let error = tools.discs.error { Text(error).foregroundStyle(.red).textSelection(.enabled) }
            if let error = tools.readError { Text(error).foregroundStyle(.red).textSelection(.enabled) }
            if let error = engine.error { Text(model.t(error)).foregroundStyle(.red).textSelection(.enabled).accessibilityIdentifier("discPlayerError") }
            ZStack {
                Color.black
                DiscPlayerSurface(engine: engine)
                if engine.state == "idle" || engine.state == "stopped" {
                    VStack(spacing: 12) {
                        Image(systemName: "opticaldisc").font(.system(size: 42))
                        Text(tr("ディスクまたはISOを開いて、実際のメニューと映像を確認します。", "打开光盘或 ISO，检查其中的实际菜单与视频。", "Open a disc or ISO to inspect its actual menu and video."))
                    }.foregroundStyle(.white.opacity(0.75)).allowsHitTesting(false)
                }
                if tools.loadingDisc || engine.state == "opening" { ProgressView().tint(.white).allowsHitTesting(false) }
            }.frame(maxWidth: .infinity, maxHeight: .infinity).frame(minHeight: 260).clipShape(RoundedRectangle(cornerRadius: 10)).accessibilityIdentifier("discPlayerVideo")
            HStack(spacing: 14) {
                Button { engine.togglePause() } label: { Image(systemName: engine.state == "playing" ? "pause.fill" : "play.fill") }.disabled(engine.sourcePath.isEmpty).accessibilityIdentifier("discPlayerPause")
                Button { engine.stop() } label: { Image(systemName: "stop.fill") }.accessibilityIdentifier("discPlayerStop")
                Text(discTime(engine.currentTime)).monospacedDigit()
                Slider(value: Binding(get: { engine.currentTime }, set: { engine.seek(seconds: $0) }), in: 0...max(1, engine.duration)).disabled(engine.duration <= 0).accessibilityIdentifier("discPlayerSeek")
                Text(discTime(engine.duration)).monospacedDigit()
                Image(systemName: "speaker.wave.2")
                Slider(value: Binding(get: { Double(engine.volume) }, set: { engine.setVolume(Int($0)) }), in: 0...100).frame(width: 100).accessibilityIdentifier("discPlayerVolume")
            }
            HStack(spacing: 20) {
                VStack(alignment: .leading, spacing: 8) {
                    Text(tr("リモコン操作", "遥控器操作", "Remote Control")).font(.caption).foregroundStyle(.secondary)
                    Button(tr("トップメニュー", "主菜单", "Top Menu")) { engine.topMenu() }.accessibilityIdentifier("discPlayerTopMenu")
                }
                VStack(spacing: 4) {
                    Button { engine.navigate(.up) } label: { Image(systemName: "arrow.up").frame(width: 50, height: 20) }
                        .accessibilityLabel(tr("上へ", "向上", "Up")).accessibilityIdentifier("discRemoteUp")
                    HStack(spacing: 5) {
                        Button { engine.navigate(.left) } label: { Image(systemName: "arrow.left").frame(width: 28, height: 20) }
                            .accessibilityLabel(tr("左へ", "向左", "Left")).accessibilityIdentifier("discRemoteLeft")
                        Button { engine.navigate(.activate) } label: { Text(tr("決定", "确认", "Enter")).fontWeight(.medium).frame(width: 50, height: 20) }
                            .accessibilityIdentifier("discRemoteActivate")
                        Button { engine.navigate(.right) } label: { Image(systemName: "arrow.right").frame(width: 28, height: 20) }
                            .accessibilityLabel(tr("右へ", "向右", "Right")).accessibilityIdentifier("discRemoteRight")
                    }
                    Button { engine.navigate(.down) } label: { Image(systemName: "arrow.down").frame(width: 50, height: 20) }
                        .accessibilityLabel(tr("下へ", "向下", "Down")).accessibilityIdentifier("discRemoteDown")
                }.buttonStyle(.bordered)
                Spacer()
            }.disabled(engine.sourcePath.isEmpty)
            DisclosureGroup(tr("タイトルとチャプター", "标题与章节", "Titles & Chapters")) {
                HStack(spacing: 15) {
                Button(tr("ポップアップメニュー", "弹出菜单", "Pop-up Menu")) { engine.navigate(.popup) }.disabled(engine.format != "bluray")
                Spacer()
                if engine.titleCount > 0 {
                    Picker(tr("タイトル", "标题", "Title"), selection: Binding(get: { engine.currentTitle }, set: { engine.setTitle($0) })) {
                        ForEach(0..<engine.titleCount, id: \.self) { n in Text("\(n + 1)").tag(n) }
                    }.frame(width: 130).accessibilityIdentifier("discPlayerTitle")
                }
                Button { engine.previousChapter() } label: { Image(systemName: "backward.end") }
                Text(tr("チャプター", "章节", "Chapter") + " \(max(0, engine.currentChapter + 1))/\(max(0, engine.chapterCount))").font(.caption)
                Button { engine.nextChapter() } label: { Image(systemName: "forward.end") }
                }.padding(.top, 10)
            }.disabled(engine.sourcePath.isEmpty)
            HStack {
                Text(tools.readSource?.label ?? tr("DVD-Video / Blu-ray Video", "DVD-Video／Blu-ray Video", "DVD-Video / Blu-ray Video")).font(.caption).lineLimit(1)
                Spacer()
                Text(tr("画面をクリック、または方向キーとEnterで操作", "点击画面，或使用方向键和 Enter 操作", "Click the picture or use arrow keys and Enter")).font(.caption).foregroundStyle(.secondary)
            }
            Text(tr("市販の暗号化ディスクやBD-Jメニューは、このプレイヤーでは利用できない場合があります。", "加密商业光盘或 BD-J 菜单可能无法在此播放器中使用。", "Encrypted commercial discs and BD-J menus may not be supported by this player.")).font(.caption).foregroundStyle(.secondary).frame(maxWidth: .infinity, alignment: .leading)
        }.padding(20).onAppear { tools.refreshDiscs() }
    }
}
