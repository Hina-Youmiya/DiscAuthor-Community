import SwiftUI
import AppKit
import AVKit

@MainActor final class DiscAuthorAppDelegate: NSObject, NSApplicationDelegate {
    weak var model: DiscAuthorModel?
    private var terminationPending = false
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        NSApp.activate(ignoringOtherApps: true)
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if terminationPending { return .terminateLater }
        if let model, model.burnProtectsTermination {
            let alert = NSAlert()
            alert.messageText = localized(model.language, "ディスクの書き込み中は終了できません", "烧录或校验完成前无法退出", "Wait for Disc Burning to Finish")
            alert.informativeText = localized(model.language, "書き込みや検証が終わるまでアプリを開いたままにしてください。進行中のディスク作成を保護しています。", "请保持应用打开，等待写入和校验完成，以保护正在制作的光盘。", "Keep the app open until writing and verification finish to protect the disc in progress.")
            alert.addButton(withTitle: localized(model.language, "書き込みを続ける", "继续烧录", "Keep Burning"))
            alert.runModal()
            model.showBurn = true; model.showQueue = false
            model.discToolTab = model.discTools.protectsTermination ? "video" : "iso"
            return .terminateCancel
        }
        if let model, model.buildIsRunning {
            let alert = NSAlert()
            alert.messageText = localized(model.language, "作成中の処理を中止して終了しますか？", "停止当前制作并退出？", "Stop the current build and quit?")
            alert.informativeText = localized(model.language, "作成途中のファイルとログは出力先に残ります。待機中のタスクは保存され、次回は手動で再開できます。", "中间文件和日志会保留。等待任务会保存，下次打开后可手动继续。", "Intermediate files and logs remain. Pending tasks are saved and can be started next time.")
            alert.addButton(withTitle: localized(model.language, "作成を続ける", "继续制作", "Keep Building"))
            alert.addButton(withTitle: localized(model.language, "中止して終了", "停止并退出", "Stop and Quit"))
            if alert.runModal() == .alertFirstButtonReturn { return .terminateCancel }
        }
        if let model, model.hasUnsavedChanges {
            let alert = NSAlert()
            alert.messageText = localized(model.language, "変更を保存して終了しますか？", "退出前保存更改？", "Save changes before quitting?")
            alert.informativeText = model.project.title
            alert.addButton(withTitle: localized(model.language, "保存して終了", "保存并退出", "Save and Quit"))
            alert.addButton(withTitle: localized(model.language, "保存せずに終了", "不保存并退出", "Discard and Quit"))
            alert.addButton(withTitle: localized(model.language, "キャンセル", "取消", "Cancel"))
            let response = alert.runModal()
            if response == .alertThirdButtonReturn { return .terminateCancel }
            if response == .alertFirstButtonReturn {
                terminationPending = true
                Task { @MainActor in
                    let saved = await model.saveForTermination()
                    let mayQuit = saved && !model.burnProtectsTermination
                    if mayQuit { model.shutdown() }
                    self.terminationPending = false
                    sender.reply(toApplicationShouldTerminate: mayQuit)
                }
                return .terminateLater
            }
        }
        model?.shutdown()
        return .terminateNow
    }
}

private func localized(_ language: String, _ ja: String, _ zh: String, _ en: String) -> String {
    language == "zh" ? zh : language == "en" ? en : ja
}

@main struct DiscAuthorNativeApp: App {
    @NSApplicationDelegateAdaptor(DiscAuthorAppDelegate.self) private var delegate
    @StateObject private var model = DiscAuthorModel()
    var body: some Scene {
        WindowGroup("DiscAuthor Community") {
            DiscAuthorRootView()
                .environmentObject(model)
                .frame(minWidth: 1200, minHeight: 760)
                .onAppear { delegate.model = model }
                .task { await model.start() }
        }
        .defaultSize(width: 1460, height: 920)
        .commands {
            CommandGroup(replacing: .help) {
                Button(localized(model.language, "ライセンス（GPLv3）", "许可证（GPLv3）", "License (GPLv3)")) {
                    if let url = Bundle.main.url(forResource: "LICENSE", withExtension: nil) { NSWorkspace.shared.open(url) }
                }
                Button(localized(model.language, "サードパーティ情報", "第三方组件声明", "Third-Party Notices")) {
                    if let url = Bundle.main.url(forResource: "THIRD_PARTY_NOTICES", withExtension: "md") { NSWorkspace.shared.open(url) }
                }
            }
            CommandGroup(replacing: .newItem) {
                Button(localized(model.language, "新規プロジェクト", "新建项目", "New Project")) { model.newProject() }.keyboardShortcut("n")
                Button(localized(model.language, "素材を追加…", "添加素材…", "Add Videos…")) { model.chooseVideos() }.keyboardShortcut("i")
                Button(localized(model.language, "プロジェクトを保存", "保存项目", "Save Project")) { model.saveProject() }.keyboardShortcut("s")
            }
        }
    }
}

struct DiscAuthorRootView: View {
    @EnvironmentObject private var model: DiscAuthorModel
    private func tr(_ ja: String, _ zh: String, _ en: String) -> String { localized(model.language, ja, zh, en) }
    var body: some View {
        VStack(spacing: 0) {
            header
            Divider()
            HStack(spacing: 0) {
                workspaceSidebar
                Divider()
                VStack(spacing: 0) {
                    if !model.showQueue && !model.showBurn { workflowBar; Divider() }
                    if !model.isReady {
                        VStack(spacing: 18) { ProgressView(); Text(model.message.isEmpty ? tr("作成エンジンを起動中…", "正在启动制作引擎…", "Starting authoring engine…") : model.message) }
                            .frame(maxWidth: .infinity, maxHeight: .infinity)
                    } else if model.showBurn { DiscToolsWorkspace(burn: model.burn, tools: model.discTools) }
                    else if model.showQueue { QueueWorkspace(queue: model.queue) }
                    else { professionalWorkspace }
                }.frame(maxWidth: .infinity, maxHeight: .infinity)
            }
            Divider()
            QueueStatusBar(queue: model.queue)
        }
        .background(Color(nsColor: .windowBackgroundColor))
        .alert(tr("確認してください", "请检查", "Please Check"), isPresented: Binding(get: { !(model.errorMessage ?? "").isEmpty }, set: { if !$0 { model.errorMessage = nil } })) {
            Button("OK") { model.errorMessage = nil }
        } message: { Text(model.errorMessage ?? "") }
        .sheet(isPresented: $model.showLogWindow) {
            VStack(alignment: .leading, spacing: 14) {
                HStack { Text(tr("ログ", "日志", "Log")).font(.title2); Spacer(); Button(tr("閉じる", "关闭", "Close")) { model.showLogWindow = false } }
                ScrollView([.horizontal, .vertical]) { Text(model.logs).font(.system(size: 11, design: .monospaced)).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading) }
            }.padding(24).frame(width: 880, height: 560)
        }
    }
    private var header: some View {
        HStack(spacing: 14) {
            Image(nsImage: NSApp.applicationIconImage).resizable().frame(width: 32, height: 32)
            Text("DiscAuthor Community").font(.system(size: 18, weight: .semibold))
            Text("DVD-Video · Blu-ray").font(.caption).foregroundStyle(.secondary)
            Spacer()
            Text(model.project.title).font(.callout).foregroundStyle(.secondary).lineLimit(1).frame(maxWidth: 300, alignment: .trailing)
            Menu {
                Button(tr("新規プロジェクト", "新建项目", "New Project")) { model.newProject() }
                Button(tr("開く…", "打开…", "Open…")) { model.chooseProject() }
                Button(tr("保存", "保存", "Save")) { model.saveProject() }
                if !model.projects.isEmpty {
                    Divider()
                    ForEach(model.projects, id: \.self) { name in Button(name) { model.loadProject(name: name) } }
                }
            } label: { Label(tr("プロジェクト", "项目", "Project"), systemImage: "folder") }.frame(width: 130)
            Picker("Language", selection: $model.language) { Text("日本語").tag("ja"); Text("中文").tag("zh"); Text("English").tag("en") }.labelsHidden().frame(width: 110).accessibilityIdentifier("interfaceLanguage")
        }.padding(.horizontal, 22).padding(.vertical, 12)
    }
    private var workspaceSidebar: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(tr("ワークスペース", "工作区", "WORKSPACE")).font(.caption2).fontWeight(.medium).foregroundStyle(.secondary).padding(.horizontal, 12).padding(.bottom, 8)
            sidebarEntry(tr("ディスクを作成", "制作光盘", "Create"), icon: "rectangle.stack", selected: !model.showQueue && !model.showBurn, identifier: "editorWorkspace") {
                model.showQueue = false; model.showBurn = false
            }
            sidebarEntry(tr("書き出しキュー", "任务队列", "Export Queue"), icon: "list.bullet.rectangle", selected: model.showQueue, identifier: "queueToggle") {
                model.showQueue = true; model.showBurn = false
            }
            sidebarEntry(tr("ディスク", "光盘工具", "Disc Tools"), icon: "opticaldiscdrive", selected: model.showBurn, identifier: "burnToggle") {
                if !model.showBurn { model.openBurnWorkspace() }
            }
            Spacer()
            VStack(alignment: .leading, spacing: 8) {
                Image(systemName: "opticaldisc").font(.title2).foregroundStyle(.tertiary)
                Text(tr("素材から、再生できる一枚へ。", "从视频素材，到可播放的光盘。", "From your videos to a playable disc.")).font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }.padding(12)
        }.padding(.horizontal, 10).padding(.top, 24).padding(.bottom, 14).frame(width: 174).background(Color(nsColor: .underPageBackgroundColor).opacity(0.6))
    }
    private func sidebarEntry(_ title: String, icon: String, selected: Bool, identifier: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Label(title, systemImage: icon).font(.system(size: 13, weight: selected ? .semibold : .regular))
                .frame(maxWidth: .infinity, alignment: .leading).padding(.horizontal, 12).padding(.vertical, 12)
                .foregroundStyle(selected ? Color.accentColor : Color.primary)
                .background(selected ? Color.accentColor.opacity(0.10) : Color.clear, in: RoundedRectangle(cornerRadius: 9))
        }.buttonStyle(.plain).disabled(!model.isReady).accessibilityIdentifier(identifier)
    }
    private var workflowBar: some View {
        HStack(spacing: 10) {
            workflowButton("source", "1", tr("素材", "素材", "Source"), "film.stack")
            Image(systemName: "chevron.right").foregroundStyle(.tertiary)
            if model.project.menuEnabled {
                workflowButton("menu", "2", tr("メニュー", "菜单", "Menu"), "rectangle.3.group")
                Image(systemName: "chevron.right").foregroundStyle(.tertiary)
            }
            workflowButton("simulation", model.project.menuEnabled ? "3" : "2", tr("シミュレーション", "模拟", "Simulation"), "play.rectangle")
            Image(systemName: "chevron.right").foregroundStyle(.tertiary)
            workflowButton("output", model.project.menuEnabled ? "4" : "3", tr("出力", "输出", "Output"), "opticaldisc")
            Spacer(minLength: 12)
            Picker(tr("ワークスペース", "工作模式", "Workspace"), selection: Binding(get: { model.mode }, set: { model.setMode($0) })) {
                Text(tr("クイック", "快速", "Quick")).tag("quick")
                Text(tr("プロ", "专业", "Pro")).tag("professional")
            }.pickerStyle(.segmented).labelsHidden().frame(width: 155).accessibilityIdentifier("workspaceMode")
        }.padding(.horizontal, 18).padding(.vertical, 12)
    }
    private func workflowButton(_ key: String, _ number: String, _ title: String, _ icon: String) -> some View {
        Button { model.setStep(key) } label: {
            Label("\(number)  \(title)", systemImage: icon).font(.system(size: 13, weight: .medium))
                .padding(.horizontal, 13).padding(.vertical, 8)
                .background(model.step == key ? Color.accentColor.opacity(0.14) : Color.clear, in: RoundedRectangle(cornerRadius: 8))
                .foregroundStyle(model.step == key ? Color.accentColor : Color.primary)
        }.buttonStyle(.plain).accessibilityIdentifier("step_" + key)
    }
    @ViewBuilder private var professionalWorkspace: some View {
        switch model.step {
        case "menu":
            HSplitView {
                ScrollView {
                    VStack(alignment: .leading, spacing: 24) {
                        MenuSettings()
                        Divider()
                        DisclosureGroup(tr("再生とリモコン", "播放与遥控", "Playback & Remote")) { PlaybackSettings().padding(.top, 14) }
                    }.padding(22)
                }.frame(minWidth: 285, idealWidth: 310, maxWidth: 360)
                MenuWorkspace(simulation: false, compact: model.mode == "quick").frame(minWidth: 620)
            }
        case "simulation":
            HSplitView {
                ScrollView {
                    VStack(alignment: .leading, spacing: 26) {
                        if model.project.menuEnabled {
                            RemoteControl()
                            DisclosureGroup(tr("再生設定", "播放设置", "Playback Settings")) { PlaybackSettings().padding(.top, 14) }
                            Text(tr("リモコン操作と実際の映像再生を確認できます。", "检查遥控器顺序并实际播放视频。", "Check remote navigation and play the actual videos.")).font(.callout).foregroundStyle(.secondary)
                        } else { NoMenuPlaybackSettings() }
                    }.padding(22)
                }.frame(minWidth: 270, idealWidth: 290, maxWidth: 340)
                Group {
                    if model.project.menuEnabled { MenuWorkspace(simulation: true, compact: false) }
                    else { NoMenuWorkspace() }
                }.frame(minWidth: 620)
            }
        case "output":
            HSplitView {
                ScrollView { VStack(alignment: .leading, spacing: 24) { OutputSettings(); Divider(); QualitySettings(); ExportShapeNotice(); buildButtons }.padding(22) }.frame(minWidth: 300, idealWidth: 325, maxWidth: 370)
                OutputSummary().frame(minWidth: 600)
            }
        default:
            HSplitView {
                ScrollView { VStack(alignment: .leading, spacing: 24) { ProjectSettings(); Divider(); sourceGuide }.padding(22) }.frame(minWidth: 285, idealWidth: 310, maxWidth: 360)
                VideoListView(compact: false).frame(minWidth: 620)
            }
        }
    }
    private var sourceGuide: some View {
        VStack(alignment: .leading, spacing: 12) {
            Label(!model.project.menuEnabled && model.project.videos.count <= 1 ? tr("1 本の動画をディスクに", "一段视频制作光盘", "One Video to Disc") : tr("素材の順番が再生順です", "素材顺序就是播放顺序", "Source order is playback order"), systemImage: "film").font(.headline)
            if model.project.menuEnabled {
                Text(tr("番組名を編集し、各素材の「…」で順番を変更します。メニューでは各番組に対応するボタンを作成します。", "编辑节目名称，在每个素材的“…”中调整顺序。菜单按钮会对应到每个节目。", "Edit program names and reorder from each source’s actions menu. Each program becomes a selectable menu button.")).font(.callout).foregroundStyle(.secondary)
                Button(tr("メニューへ進む", "继续设计菜单", "Continue to Menu")) { model.setStep("menu") }.buttonStyle(.borderedProminent)
            } else {
                if model.project.videos.isEmpty {
                    Text(tr("動画を 1 本追加し、DVD-Video または Blu-ray を選べば作成できます。", "导入一段视频，选择 DVD-Video 或 Blu-ray 即可制作。", "Add one video and choose DVD-Video or Blu-ray to create your disc.")).font(.callout).foregroundStyle(.secondary)
                    Button(tr("動画を追加…", "导入一段视频…", "Add One Video…")) { model.chooseVideos() }.buttonStyle(.borderedProminent)
                } else if model.project.videos.count == 1 {
                    Text(tr("ディスクを入れると、この動画を自動で再生します。", "插入光盘后自动播放这段视频。", "This video plays automatically when the disc is inserted.")).font(.callout).foregroundStyle(.secondary)
                    Button(tr("動画の再生を確認", "检查视频播放", "Preview Video Playback")) { model.setStep("simulation") }.buttonStyle(.borderedProminent)
                } else {
                    Text(tr("動画が複数あります。「メニューあり」に切り替えるか、素材を 1 本にしてください。", "当前有多个视频。请选择“有菜单”，或将素材保留为一个视频。", "Several videos are present. Choose With Menu or keep a single video.")).font(.callout).foregroundStyle(.orange)
                }
            }
        }
    }
    private var buildButtons: some View {
        VStack(spacing: 10) {
            Button { model.startBuild() } label: { Label(tr("ISO を作成", "制作 ISO", "Build ISO"), systemImage: "opticaldisc").frame(maxWidth: .infinity) }.buttonStyle(.borderedProminent)
                .disabled(!canExport || model.queueIsBusy).accessibilityIdentifier("buildISO")
            HStack {
                Button { model.addCurrentToQueue() } label: { Label(tr("キューに追加", "添加到队列", "Add to Queue"), systemImage: "text.badge.plus") }
                    .disabled(!canExport || model.queueIsBusy)
                Menu {
                    Button(tr("出力前チェック", "制作前检查", "Preflight Check")) { model.checkProject() }
                    Button(tr("DVD と Blu-ray をキューに追加", "同时添加 DVD 和 Blu-ray", "Queue DVD and Blu-ray")) { model.addBothFormatsToQueue() }.disabled(!canExport || model.queueIsBusy)
                } label: { Image(systemName: "ellipsis") }.frame(width: 38)
            }.controlSize(.regular)
            Text(tr("キューへの追加後も次のプロジェクトを編集できます。", "加入队列后可继续配置下一个项目。", "After queuing, continue editing your next project.")).font(.caption).foregroundStyle(.secondary)
            if model.mode == "quick", let check = model.preflight { EncodingPreflightNotes(check: check) }
        }.controlSize(.large)
    }
    private var canExport: Bool {
        !model.project.videos.isEmpty && (model.project.menuEnabled || (model.project.videos.count == 1 && model.project.noMenuEndAction == .stop))
    }

}

private func queuePercentage(_ value: Double) -> String {
    String(format: "%.1f%%", min(100, max(0, value.isFinite ? value : 0)))
}

private func queueStatus(_ item: QueueItem, language: String) -> String {
    switch item.status {
    case "pending": return localized(language, "待機中", "等待中", "Pending")
    case "running": return localized(language, "作成中", "制作中", "Running")
    case "paused": return localized(language, "現在のタスクを一時停止中", "当前任务已暂停", "Current Task Paused")
    case "complete": return localized(language, "完了", "已完成", "Complete")
    case "failed": return localized(language, "失敗", "失败", "Failed")
    case "cancelled": return localized(language, "中止", "已取消", "Cancelled")
    case "interrupted": return localized(language, "中断・再試行が必要", "已中断・需要重试", "Interrupted · Retry Needed")
    default: return item.status
    }
}

struct QueueHeaderButton: View {
    @EnvironmentObject private var model: DiscAuthorModel
    @ObservedObject var queue: DiscQueueState
    var body: some View {
        Button { model.showQueue.toggle(); model.showBurn = false } label: {
            Label(model.showQueue ? localized(model.language, "編集に戻る", "返回编辑器", "Back to Editor") : localized(model.language, "キュー", "任务队列", "Queue") + " (\(queue.pendingCount + (queue.active == nil ? 0 : 1)))", systemImage: model.showQueue ? "rectangle.and.pencil.and.ellipsis" : "list.bullet.rectangle")
        }.accessibilityIdentifier("queueToggle").disabled(!model.isReady)
    }
}

struct QueueStatusBar: View {
    @EnvironmentObject private var model: DiscAuthorModel
    @ObservedObject var queue: DiscQueueState
    private func tr(_ ja: String, _ zh: String, _ en: String) -> String { localized(model.language, ja, zh, en) }
    var body: some View {
        HStack(spacing: 12) {
            if model.isBusy { ProgressView().controlSize(.small) }
            if let item = queue.displayItem {
                if item.isActive {
                    ProgressView(value: min(100, max(0, item.progress)), total: 100).frame(width: 135)
                    Text(queuePercentage(item.progress)).monospacedDigit().frame(width: 54, alignment: .trailing)
                }
                Text(item.isPaused || item.message.isEmpty ? queueStatus(item, language: model.language) : item.message).lineLimit(1)
            } else { Text(model.message.isEmpty ? tr("準備完了", "就绪", "Ready") : model.message).lineLimit(1) }
            Spacer(minLength: 12)
            BackgroundDiscActivity(burn: model.burn, tools: model.discTools)
            if queue.pendingCount > 0 {
                Button(String(format: tr("待機 %d 件", "等待 %d 项", "%d Pending"), queue.pendingCount)) { model.showQueue = true; model.showBurn = false }
            }
            if queue.displayItem != nil {
                Menu {
                    Button(tr("ログ", "日志", "Log")) { model.showLogs() }
                    Button(tr("Finder で表示", "在 Finder 中显示", "Show in Finder")) { model.revealOutput() }
                } label: { Image(systemName: "ellipsis") }.frame(width: 36)
            }
        }.font(.callout).padding(.horizontal, 22).padding(.vertical, 10)
    }
}

struct BackgroundDiscActivity: View {
    @EnvironmentObject private var model: DiscAuthorModel
    @ObservedObject var burn: DiscBurnState
    @ObservedObject var tools: DiscToolsState
    private func tr(_ ja: String, _ zh: String, _ en: String) -> String { localized(model.language, ja, zh, en) }
    var body: some View {
        if burn.protectsTermination || tools.direct.active || tools.isStarting || tools.awaitingStatus {
            let direct = tools.direct.active || tools.isStarting || tools.awaitingStatus
            let starting = direct ? tools.isStarting || tools.awaitingStatus : burn.isStarting || burn.awaitingStartStatus
            let stage = starting ? "preparing" : direct ? tools.direct.job?.stage : burn.snapshot.job?.stage
            let progress = starting ? nil : direct ? tools.direct.job?.progress : burn.snapshot.job?.progress
            Button {
                model.discToolTab = direct ? "video" : "iso"
                model.showBurn = true; model.showQueue = false
            } label: {
                HStack(spacing: 7) {
                    Image(systemName: "opticaldiscdrive")
                    Text(direct ? tr("動画直書き込み", "单视频直刻", "Video to Disc") : tr("ディスク書き込み", "光盘烧录", "Disc Burning"))
                    if let stage { Text(burnStage(stage, language: model.language)).foregroundStyle(.secondary) }
                    if let progress { Text(queuePercentage(progress)).monospacedDigit() }
                    else { ProgressView().controlSize(.small) }
                }.lineLimit(1)
            }.accessibilityIdentifier("backgroundDiscActivity")
        }
    }
}

struct QueueCurrentProgress: View {
    @EnvironmentObject private var model: DiscAuthorModel
    @ObservedObject var queue: DiscQueueState
    var body: some View {
        if let item = queue.displayItem {
            GroupBox {
                VStack(alignment: .leading, spacing: 14) {
                    HStack { Text(item.title).font(.headline); Spacer(); Text(queueStatus(item, language: model.language)).foregroundStyle(.secondary) }
                    QueueProgressDetails(item: item, language: model.language)
                    if let error = item.error { Text(error).foregroundStyle(.red).textSelection(.enabled) }
                    if let iso = item.iso {
                        Text(iso).font(.callout).textSelection(.enabled)
                        HStack {
                            Button(localized(model.language, "Finder で ISO を表示", "在 Finder 中显示 ISO", "Show ISO in Finder")) { model.revealOutput(item) }
                            BurnExportButton(item: item, burn: model.burn, tools: model.discTools)
                        }
                    }
                }.padding(12).frame(maxWidth: .infinity, alignment: .leading)
            }
        }
    }
}

struct QueueProgressDetails: View {
    let item: QueueItem
    let language: String
    private func tr(_ ja: String, _ zh: String, _ en: String) -> String { localized(language, ja, zh, en) }
    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack(spacing: 14) {
                ProgressView(value: min(100, max(0, item.progress)), total: 100)
                    .accessibilityLabel(tr("ISO 全体の進捗", "ISO 总进度", "Overall ISO Progress"))
                Text(queuePercentage(item.progress)).font(.system(.body, design: .monospaced)).fontWeight(.medium).frame(width: 64, alignment: .trailing)
                    .accessibilityIdentifier("queueProgress_" + item.id)
            }
            if item.isPaused { Text(tr("再開すると、この位置から制作を続けます。", "继续后会从此位置接着制作。", "Resuming continues this task from its current position.")).font(.callout).foregroundStyle(.secondary) }
            else if !item.message.isEmpty { Text(item.message).font(.callout).foregroundStyle(.secondary).textSelection(.enabled) }
            if item.isActive, let stage = item.stageProgress {
                HStack(spacing: 14) {
                    Text(tr("現在の工程", "当前步骤", "Current Stage")).font(.caption).foregroundStyle(.secondary)
                    ProgressView(value: min(100, max(0, stage)), total: 100).controlSize(.small)
                    Text(queuePercentage(stage)).font(.caption.monospacedDigit()).frame(width: 54, alignment: .trailing)
                }
            }
            if item.isRunning, let seconds = item.processedSeconds, let total = item.totalSeconds, total > 0 {
                HStack {
                    Text(tr("処理済み映像", "已处理视频", "Processed Video") + "  " + formatDuration(seconds) + " / " + formatDuration(total))
                    if let speed = item.speed { Text(tr("速度", "速度", "Speed") + "  " + String(format: "%.2fx", speed)) }
                }.font(.caption.monospacedDigit()).foregroundStyle(.secondary)
            }
        }
    }
}

struct QueueWorkspace: View {
    @EnvironmentObject private var model: DiscAuthorModel
    @ObservedObject var queue: DiscQueueState
    private func tr(_ ja: String, _ zh: String, _ en: String) -> String { localized(model.language, ja, zh, en) }
    private var stateText: String {
        if queue.active?.isPaused == true { return tr("現在のタスクを一時停止中", "当前任务已暂停", "Current Task Paused") }
        if queue.running { return tr("順番に自動で作成します", "正在按顺序自动制作", "Tasks run automatically in order") }
        if queue.active != nil { return tr("現在のタスク完了後に一時停止", "当前任务完成后暂停", "Pausing After Current Task") }
        if queue.pendingCount == 0 { return tr("待機タスクはありません", "没有等待中的任务", "No Pending Tasks") }
        return tr("一時停止中", "已暂停", "Paused")
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 5) {
                    Text(tr("書き出しキュー", "导出任务队列", "Export Queue")).font(.title2).fontWeight(.semibold)
                    Text(String(format: tr("全 %d 件・完了 %d 件・待機 %d 件", "共 %d 项・已完成 %d 项・等待 %d 项", "%d Tasks · %d Complete · %d Pending"), queue.items.count, queue.completeCount, queue.pendingCount)).foregroundStyle(.secondary)
                }
                Spacer()
                if queue.running && queue.active?.isPaused != true {
                    Button { model.pauseQueue() } label: { Label(tr("現在の完了後に一時停止", "当前项完成后暂停", "Pause After Current"), systemImage: "pause.fill") }
                        .accessibilityIdentifier("pauseQueue")
                } else {
                    Button { model.startQueue() } label: { Label(queue.active == nil || queue.active?.isPaused == true ? tr("キューを開始／再開", "开始／继续队列", "Start / Resume Queue") : tr("続けて作成", "继续后续任务", "Continue Queue"), systemImage: "play.fill") }
                        .buttonStyle(.borderedProminent).disabled(queue.pendingCount == 0 && queue.active?.isPaused != true)
                        .accessibilityIdentifier("startQueue")
                }
            }.disabled(model.queueIsBusy)
            HStack(spacing: 12) {
                Menu {
                Button(tr("現在の設定を追加", "添加当前配置", "Add Current Settings")) { model.addCurrentToQueue() }
                    .disabled(!exportShapeIsValid)
                    .accessibilityIdentifier("addCurrentToQueue")
                Button(tr("DVD と Blu-ray を追加", "同时添加 DVD 和 Blu-ray", "Add DVD and Blu-ray")) { model.addBothFormatsToQueue() }
                    .disabled(!exportShapeIsValid)
                    .accessibilityIdentifier("addBothToQueue")
                Divider()
                Button(tr("プロジェクトを一括追加…", "批量导入项目…", "Import Projects…")) { model.importQueueProjects() }
                    .accessibilityIdentifier("importQueueProjects")
                } label: { Label(tr("タスクを追加", "添加任务", "Add Tasks"), systemImage: "plus") }.frame(width: 150)
                Spacer()
                Text(stateText).font(.callout).foregroundStyle(queue.running ? Color.accentColor : Color.secondary)
            }.disabled(model.queueIsBusy)
            if let error = queue.snapshot.error, !error.isEmpty {
                VStack(alignment: .leading, spacing: 5) {
                    Label(tr("キューのエラー", "队列错误", "Queue Error"), systemImage: "exclamationmark.triangle.fill").font(.callout).fontWeight(.medium)
                    Text(error).font(.callout).textSelection(.enabled)
                }.foregroundStyle(.red).padding(12).frame(maxWidth: .infinity, alignment: .leading)
                    .background(Color.red.opacity(0.07), in: RoundedRectangle(cornerRadius: 8))
                    .accessibilityIdentifier("queueControllerError")
            }
            Text(tr("ISO の作成中も「ディスク」から完成済み ISO を書き込めます。書き込み・検証と書き出しキューは同時に動作します。", "导出期间可从“光盘工具”烧录已有 ISO。烧录及校验与导出队列可同时运行。", "While exporting, use Disc Tools to burn an existing ISO. Burning and verification run alongside the export queue.")).font(.callout).foregroundStyle(.secondary).accessibilityIdentifier("queueConcurrentBurnHint")
            Text(tr("追加時の設定をタスクごとに保存します。編集画面で設定を変えても、登録済みタスクは変わりません。待機タスクは「…」から順番を変更できます。", "每项任务保存添加时的配置；继续编辑不会改变已排任务。等待任务可在“…”菜单中调整顺序。", "Each task keeps its settings when added. Further edits do not change queued tasks. Use each task’s actions menu to reorder pending tasks.")).font(.caption).foregroundStyle(.secondary)
            Divider()
            if queue.items.isEmpty {
                VStack(spacing: 14) {
                    Image(systemName: "list.bullet.rectangle").font(.system(size: 38)).foregroundStyle(.secondary)
                    Text(tr("まだタスクはありません", "还没有任务", "No Tasks Yet")).font(.title3)
                    Text(tr("現在の設定、または複数の保存済みプロジェクトを追加してください。", "添加当前配置，或一次导入多个已保存项目。", "Add the current settings or import several saved projects.")).foregroundStyle(.secondary)
                }.frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ScrollView {
                    LazyVStack(spacing: 12) {
                        ForEach(Array(queue.items.enumerated()), id: \.element.id) { index, item in
                            QueueTaskRow(item: item, number: index + 1,
                                         canMoveUp: item.status == "pending" && queue.items.prefix(index).contains(where: { $0.status == "pending" }),
                                         canMoveDown: item.status == "pending" && queue.items.dropFirst(index + 1).contains(where: { $0.status == "pending" }))
                        }
                    }.padding(.bottom, 8)
                }
            }
            Text(tr("終了してもタスク一覧は保存されます。次回起動時は自動開始せず、中断したタスクは「再試行」で登録し直せます。", "任务列表会保存。下次打开不会自动开始；中断任务可点击“重试”重新制作。", "The task list is saved. Relaunching does not start exports automatically; use Retry for interrupted tasks.")).font(.caption).foregroundStyle(.secondary)
        }.padding(24).frame(maxWidth: .infinity, maxHeight: .infinity)
    }
    private var exportShapeIsValid: Bool {
        !model.project.videos.isEmpty && (model.project.menuEnabled || (model.project.videos.count == 1 && model.project.noMenuEndAction == .stop))
    }
}

struct BurnExportButton: View {
    @EnvironmentObject private var model: DiscAuthorModel
    let item: QueueItem
    @ObservedObject var burn: DiscBurnState
    @ObservedObject var tools: DiscToolsState
    private var busy: Bool { burn.protectsTermination || tools.protectsTermination }
    var body: some View {
        if item.completedISO != nil {
            Button { model.burnCompletedISO(item) } label: {
                Label(localized(model.language, "この ISO を書き込む", "烧录此 ISO", "Burn This ISO"), systemImage: "opticaldiscdrive")
            }
            .buttonStyle(.bordered)
            .disabled(busy)
            .help(busy
                  ? localized(model.language, "現在のディスク操作が終了するまでお待ちください。", "请等待当前光盘操作结束。", "Wait for the current disc operation to finish.")
                  : localized(model.language, "この ISO を選択して書き込み画面を開きます。", "带入此 ISO 并打开烧录界面。", "Open the burning screen with this ISO selected."))
            .accessibilityIdentifier("burnExport_" + item.id)
        }
    }
}

struct CompletedExportsMenu: View {
    @EnvironmentObject private var model: DiscAuthorModel
    @ObservedObject var queue: DiscQueueState
    @ObservedObject var burn: DiscBurnState
    @ObservedObject var tools: DiscToolsState
    private var completed: [QueueItem] { queue.items.reversed().filter { $0.completedISO != nil } }
    var body: some View {
        if !completed.isEmpty {
            Menu {
                ForEach(completed) { item in
                    Button(item.title + " · " + (item.format == "dvd" ? "DVD" : "BD")) { model.burnCompletedISO(item) }
                        .help(item.completedISO ?? "")
                }
            } label: {
                Label(localized(model.language, "書き出し済み ISO", "已导出的 ISO", "Exported ISOs"), systemImage: "checkmark.circle")
            }
            .disabled(burn.protectsTermination || tools.protectsTermination)
            .accessibilityIdentifier("completedExportsMenu")
        }
    }
}

struct QueueTaskRow: View {
    @EnvironmentObject private var model: DiscAuthorModel
    let item: QueueItem
    let number: Int
    let canMoveUp: Bool
    let canMoveDown: Bool
    @State private var expanded = false
    private func tr(_ ja: String, _ zh: String, _ en: String) -> String { localized(model.language, ja, zh, en) }
    private var statusColor: Color {
        switch item.status { case "complete": return .green; case "failed", "interrupted": return .red; case "running": return .accentColor; case "paused": return .orange; default: return .secondary }
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 14) {
                Text(String(format: "%02d", number)).font(.system(.headline, design: .monospaced)).foregroundStyle(.secondary).frame(width: 30)
                VStack(alignment: .leading, spacing: 4) {
                    Text(item.title).font(.headline).lineLimit(2).textSelection(.enabled)
                    Text((item.format == "dvd" ? "DVD-Video" : "Blu-ray") + " · " + item.capacity.uppercased() + " · " + String(format: tr("%d 番組", "%d 个节目", "%d Programs"), item.config.videos.count)).font(.caption).foregroundStyle(.secondary)
                }
                Spacer(minLength: 20)
                Text(queueStatus(item, language: model.language)).font(.callout).foregroundStyle(statusColor)
                BurnExportButton(item: item, burn: model.burn, tools: model.discTools)
                if item.isRunning {
                    Button { model.pauseQueueItem(item) } label: { Label(tr("今すぐ一時停止", "立即暂停", "Pause Now"), systemImage: "pause.fill") }
                        .accessibilityIdentifier("pauseQueueItem_" + item.id)
                }
                if item.isPaused {
                    Button { model.resumeQueueItem(item) } label: { Label(tr("再開", "继续", "Resume"), systemImage: "play.fill") }
                        .accessibilityIdentifier("resumeQueueItem_" + item.id)
                }
                if item.isActive {
                    Button(tr("中止", "取消", "Cancel"), role: .destructive) { model.cancelQueueItem(item) }
                }
                if item.canRetry { Button(tr("再試行", "重试", "Retry")) { model.retryQueueItem(item) } }
                Menu {
                    if item.status == "pending" {
                        Button(tr("前へ移動", "向前移动", "Move Earlier")) { model.moveQueueItem(item, direction: "up") }.disabled(!canMoveUp)
                        Button(tr("後へ移動", "向后移动", "Move Later")) { model.moveQueueItem(item, direction: "down") }.disabled(!canMoveDown)
                        Button(tr("タスクを中止", "取消任务", "Cancel Task"), role: .destructive) { model.cancelQueueItem(item) }
                        Divider()
                    }
                    Button(tr("ログを表示", "查看日志", "View Log")) { model.showLogs(item) }.disabled(item.folder == nil)
                    Button(tr("出力を Finder で表示", "打开输出位置", "Show Output in Finder")) { model.revealOutput(item) }.disabled(item.folder == nil && item.iso == nil)
                    Divider()
                    Button(tr("一覧から削除", "从列表移除", "Remove from List"), role: .destructive) { model.removeQueueItem(item) }.disabled(item.isActive)
                } label: { Image(systemName: "ellipsis") }.menuStyle(.borderlessButton).frame(width: 24)
            }.disabled(model.queueIsBusy)
            QueueProgressDetails(item: item, language: model.language)
            if let error = item.error, !error.isEmpty { Text(error).font(.callout).foregroundStyle(.red).textSelection(.enabled) }
            DisclosureGroup(isExpanded: $expanded) {
                VStack(alignment: .leading, spacing: 6) {
                    Text(tr("保存先", "输出位置", "Output") + ": " + (item.iso ?? item.folder ?? item.config.outputDir)).textSelection(.enabled)
                    Text(tr("保存方法", "保存方式", "Save Method") + ": " + (item.config.exportMode == "project_name" ? tr("同じフォルダにプロジェクト名で保存", "统一目录，按项目名称保存", "One Folder, Project Names") : tr("タスクごとに別フォルダ", "每个任务单独文件夹", "Separate Task Folders")))
                    Text(tr("DVD / BD の接尾辞", "DVD／BD 后缀", "DVD / BD Suffix") + ": " + (item.config.exportMode == "project_name" ? (item.config.exportFormatSuffix ? tr("あり", "启用", "On") : tr("なし", "关闭", "Off")) : tr("この保存方法では使用しません", "此保存方式不使用", "Not Used in This Mode")))
                    Text(tr("予定ファイル名", "预计文件名", "Filename Preview") + ": " + item.config.exportFilenamePreview).textSelection(.enabled)
                    Text(tr("番組", "节目", "Programs") + ": " + item.config.videos.map(\.title).joined(separator: " · ")).textSelection(.enabled)
                    Text(item.config.menuEnabled ? tr("メニューあり", "有菜单", "Menu Enabled") : tr("メニューなし", "无菜单", "No Menu"))
                    if item.config.menuEnabled { Text(tr("フォント", "字体", "Font") + ": " + item.config.font.label) }
                    Text(tr("追加日時", "添加时间", "Added") + ": " + item.createdAt)
                }.font(.caption).foregroundStyle(.secondary).frame(maxWidth: .infinity, alignment: .leading).padding(.top, 5)
            } label: { Text(tr("タスク設定", "任务配置", "Task Settings")).font(.caption) }
        }.padding(16).background(item.isActive ? Color.accentColor.opacity(0.055) : Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 10))
            .overlay(RoundedRectangle(cornerRadius: 10).stroke(item.isActive ? Color.accentColor.opacity(0.3) : Color.secondary.opacity(0.15), lineWidth: 1))
    }
}

private func burnBytes(_ bytes: Int64) -> String {
    String(format: "%.2f GB", Double(bytes) / 1_000_000_000)
}

private func burnStage(_ stage: String, language: String) -> String {
    switch stage {
    case "preparing": return localized(language, "準備中", "准备中", "Preparing")
    case "authoring": return localized(language, "動画を変換中", "正在转换视频", "Preparing Video")
    case "checking_media", "preparing_burn": return localized(language, "ディスクを確認中", "正在检查光盘", "Checking Disc")
    case "cleaning": return localized(language, "一時ファイルを整理中", "正在清理临时文件", "Cleaning Temporary Files")
    case "burning": return localized(language, "ディスクに書き込み中", "正在写入光盘", "Writing Disc")
    case "verifying": return localized(language, "書き込みを検証中", "正在校验光盘", "Verifying Disc")
    case "finishing": return localized(language, "ディスクを仕上げ中", "正在完成光盘", "Finalizing Disc")
    case "complete": return localized(language, "書き込み完了", "烧录完成", "Burn Complete")
    case "failed": return localized(language, "書き込み失敗", "烧录失败", "Burn Failed")
    case "cancelling": return localized(language, "中止処理中", "正在取消", "Cancelling")
    case "cancelled": return localized(language, "中止しました", "已取消", "Cancelled")
    default: return localized(language, "光学ドライブを待機中", "正在等待光驱", "Waiting for Drive")
    }
}

struct BurnHeaderButton: View {
    @EnvironmentObject private var model: DiscAuthorModel
    @ObservedObject var burn: DiscBurnState
    var body: some View {
        Button { model.openBurnWorkspace() } label: {
            Label(model.showBurn ? localized(model.language, "編集", "编辑器", "Editor") : localized(model.language, "ディスク", "光盘工具", "Disc Tools"), systemImage: model.showBurn ? "rectangle.and.pencil.and.ellipsis" : "opticaldiscdrive")
                .foregroundStyle(burn.protectsTermination ? Color.accentColor : Color.primary)
        }.disabled(!model.isReady).accessibilityIdentifier("burnToggle")
    }
}

private struct BurnSetupCard<Content: View>: View {
    let number: String
    let title: String
    let icon: String
    @ViewBuilder let content: () -> Content
    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack(spacing: 10) {
                Text(number).font(.system(.caption, design: .monospaced).weight(.semibold))
                    .foregroundStyle(Color.accentColor).frame(width: 28, height: 28)
                    .background(Color.accentColor.opacity(0.08), in: RoundedRectangle(cornerRadius: 8))
                Label(title, systemImage: icon).font(.headline)
            }
            content()
        }.padding(20).frame(maxWidth: .infinity, alignment: .topLeading)
            .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 14))
            .overlay(RoundedRectangle(cornerRadius: 14).stroke(Color.primary.opacity(0.07), lineWidth: 1))
    }
}

struct BurnWorkspace: View {
    @EnvironmentObject private var model: DiscAuthorModel
    @ObservedObject var burn: DiscBurnState
    private func tr(_ ja: String, _ zh: String, _ en: String) -> String { localized(model.language, ja, zh, en) }
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                VStack(alignment: .leading, spacing: 6) {
                    Text(tr("ISO をディスクに書き込む", "将 ISO 烧录到光盘", "Burn ISO to Disc")).font(.title2.weight(.semibold))
                    Text(tr("ISO を選び、空のディスクを入れて開始します。", "选择 ISO，放入空白光盘，然后开始烧录。", "Choose an ISO, insert a blank disc, and start burning.")).foregroundStyle(.secondary)
                }
                if let job = burn.snapshot.job, burn.snapshot.active {
                    BurnJobProgress(job: job, active: true)
                }
                HStack(alignment: .top, spacing: 18) {
                    imagePanel.frame(maxWidth: .infinity, alignment: .topLeading)
                    devicePanel.frame(maxWidth: .infinity, alignment: .topLeading)
                }
                if let error = burn.detected.error, !error.isEmpty { errorText(error) }
                if let error = burn.connectionError, !error.isEmpty { errorText(error) }
                if let error = burn.error, !error.isEmpty { errorText(error) }
                if let check = burn.check, !check.valid, burn.device?.media.isProbing != true {
                    VStack(alignment: .leading, spacing: 7) {
                        Label(tr("開始するには", "开始前还需要", "Before You Start"), systemImage: "exclamationmark.circle").font(.headline)
                        ForEach(Array(check.errors.enumerated()), id: \.offset) { _, error in Text(error).textSelection(.enabled) }
                    }.foregroundStyle(.red).padding(14).frame(maxWidth: .infinity, alignment: .leading)
                        .background(Color.red.opacity(0.06), in: RoundedRectangle(cornerRadius: 10))
                }
                Divider()
                HStack(alignment: .center, spacing: 24) {
                    VStack(alignment: .leading, spacing: 6) {
                        Toggle(tr("書き込み後にデータを検証", "烧录后校验数据完整性", "Verify Data After Burning"), isOn: Binding(get: { burn.verifyAfterBurn }, set: { model.selectBurnVerification($0) }))
                            .disabled(model.burnProtectsTermination).accessibilityIdentifier("burnVerifyAfterBurn")
                        Text(burn.verifyAfterBurn
                             ? tr("ディスクを読み戻し、ISO と照合します。", "回读光盘，与 ISO 核对写入数据。", "Read the disc back and compare it with the ISO.")
                             : tr("書き込み後のデータ検証を省略します。", "写入完成后跳过数据校验。", "Skip the data check after writing."))
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    Spacer(minLength: 0)
                    Button { model.startBurn() } label: { Label(tr("書き込みを開始", "开始烧录", "Start Burning"), systemImage: "opticaldiscdrive.fill").frame(minWidth: 125) }
                        .buttonStyle(.borderedProminent).controlSize(.large).disabled(!burn.canStart || model.discTools.protectsTermination).accessibilityIdentifier("startBurn")
                }
                if burn.isInspecting || burn.isChecking || burn.isStarting {
                    HStack(spacing: 8) {
                        ProgressView().controlSize(.small)
                        Text(burn.isStarting ? tr("光学ドライブを準備中…", "正在准备光驱…", "Preparing the drive…") : tr("ISO とディスクを確認中…", "正在检查 ISO 与光盘…", "Checking ISO and disc…")).foregroundStyle(.secondary)
                    }
                } else if burn.canStart {
                    Label(tr("書き込みの準備ができました", "已准备好，可以开始烧录", "Ready to Burn"), systemImage: "checkmark.circle.fill").foregroundStyle(.green)
                } else if !burn.protectsTermination && burn.check == nil {
                    Text(burn.image == nil
                         ? tr("書き込む ISO を選択してください。", "请选择要烧录的 ISO。", "Choose an ISO to burn.")
                         : tr("空のディスクを入れてください。", "请放入空白光盘。", "Insert a blank disc."))
                        .font(.callout).foregroundStyle(.secondary)
                }
                Text(tr("書き込み中も ISO の書き出しを続けられます。", "烧录期间可以继续导出 ISO。", "ISO exports can continue while burning.")).font(.caption).foregroundStyle(.secondary).accessibilityIdentifier("burnConcurrentExportHint")
                if let job = burn.snapshot.job, !burn.snapshot.active {
                    DisclosureGroup(tr("前回の書き込み", "上次烧录结果", "Previous Burn")) {
                        BurnJobProgress(job: job, active: false).padding(.top, 10)
                    }.accessibilityIdentifier("previousBurnResult")
                }
            }.padding(28).frame(maxWidth: 1060, alignment: .leading).frame(maxWidth: .infinity)
        }.onAppear { model.refreshBurnDevices() }
    }
    private var imagePanel: some View {
        BurnSetupCard(number: "01", title: tr("書き込む ISO", "待烧录 ISO", "ISO Image"), icon: "doc.zipper") {
            if let image = burn.image {
                Text(image.title).font(.title3.weight(.medium)).fixedSize(horizontal: false, vertical: true).textSelection(.enabled)
                Text((image.format == "dvd" ? "DVD-Video" : "Blu-ray Video") + " · " + burnBytes(image.sizeBytes)).foregroundStyle(.secondary)
            } else if let path = burn.requestedISOPath {
                Text(URL(fileURLWithPath: path).lastPathComponent).font(.title3).fixedSize(horizontal: false, vertical: true).textSelection(.enabled)
            } else {
                Text(tr("ISO が選択されていません", "尚未选择 ISO", "No ISO Selected")).font(.title3).foregroundStyle(.secondary)
                Text(tr("ファイル、または書き出し済みのタスクから選びます。", "从文件或已完成的导出任务中选择。", "Choose a file or a completed export.")).font(.callout).foregroundStyle(.secondary)
            }
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 10) { imageActions }
                VStack(alignment: .leading, spacing: 10) { imageActions }
            }
            if let title = burn.sourceExportTitle {
                Label(tr("書き出し済み", "已导出", "Exported") + " · " + title, systemImage: "checkmark.circle")
                    .font(.caption).foregroundStyle(.secondary).lineLimit(2).textSelection(.enabled).accessibilityIdentifier("burnExportSource")
            }
            if let image = burn.image {
                DisclosureGroup(tr("ファイルの詳細", "文件详情", "File Details")) {
                    VStack(alignment: .leading, spacing: 8) {
                        Text(image.path).textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
                        Text(tr("必要な空き容量", "所需光盘空间", "Required Disc Space") + " · " + burnBytes(image.requiredBytes))
                    }.font(.caption).foregroundStyle(.secondary).padding(.top, 8)
                }.font(.callout)
            }
        }
    }
    @ViewBuilder private var imageActions: some View {
        Button { model.chooseBurnISO() } label: {
            Label(burn.image == nil ? tr("ISO を選択…", "选择 ISO…", "Choose ISO…") : tr("変更…", "更换…", "Change…"), systemImage: "folder")
        }.controlSize(.large).disabled(model.burnProtectsTermination).accessibilityIdentifier("chooseBurnISO")
        CompletedExportsMenu(queue: model.queue, burn: burn, tools: model.discTools)
    }
    private var devicePanel: some View {
        BurnSetupCard(number: "02", title: tr("書き込み先", "目标光盘", "Target Disc"), icon: "opticaldiscdrive") {
            HStack {
                if burn.detected.devices.count == 1, let device = burn.device {
                    Text(device.name).font(.headline).fixedSize(horizontal: false, vertical: true).accessibilityIdentifier("burnDeviceName")
                } else if !burn.detected.devices.isEmpty {
                    Picker(tr("光学ドライブ", "光驱", "Optical Drive"), selection: Binding(get: { burn.selectedDeviceID }, set: { model.selectBurnDevice($0) })) {
                        ForEach(burn.detected.devices) { device in Text(device.name).tag(device.id) }
                    }.labelsHidden().disabled(burn.protectsTermination).accessibilityIdentifier("burnDevicePicker")
                } else {
                    Text(tr("光学ドライブなし", "未检测到光驱", "No Optical Drive")).foregroundStyle(.secondary)
                }
                Spacer(minLength: 8)
                if burn.isRefreshing { ProgressView().controlSize(.small) }
                Button { model.refreshBurnDevices() } label: { Image(systemName: "arrow.clockwise") }
                    .help(tr("再検出", "重新检测", "Refresh Drives"))
                    .accessibilityLabel(tr("再検出", "重新检测", "Refresh Drives"))
                    .disabled(burn.isRefreshing || model.burnProtectsTermination).accessibilityIdentifier("refreshBurnDevices")
            }
            if let device = burn.device {
                mediaDetails(device.media)
                DisclosureGroup(tr("速度とドライブ情報", "速度与光驱详情", "Speed & Drive Details")) {
                    VStack(alignment: .leading, spacing: 12) {
                        if !device.media.speeds.isEmpty {
                            Picker(tr("書き込み速度", "烧录速度", "Write Speed"), selection: Binding(get: { burn.speed }, set: { model.selectBurnSpeed($0) })) {
                                Text(tr("ドライブに合わせる", "光驱自动选择", "Drive Default")).tag(0.0)
                                ForEach(Array(Set(device.media.speeds.filter { $0 > 0 })).sorted(), id: \.self) { speed in Text(String(format: "%.1f×", speed)).tag(speed) }
                            }.disabled(model.burnProtectsTermination).accessibilityIdentifier("burnSpeedPicker")
                        }
                        Text([device.bus, device.canWriteDVD ? "DVD" : nil, device.canWriteBD ? "Blu-ray" : nil].compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: " · "))
                        if let bytes = device.media.capacityBytes { Text(tr("ディスク容量", "盘片总容量", "Disc Capacity") + " · " + burnBytes(bytes)) }
                    }.font(.caption).foregroundStyle(.secondary).padding(.top, 8)
                }.font(.callout)
            } else {
                Text(tr("USB 光学ドライブを接続してください。", "请连接 USB 光驱。", "Connect a USB optical drive.")).font(.callout).foregroundStyle(.secondary)
            }
        }
    }
    @ViewBuilder private func mediaDetails(_ media: BurnMedia) -> some View {
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
        } else if !media.present {
            Label(tr("ディスクが挿入されていません", "尚未插入光盘", "No Disc Inserted"), systemImage: "opticaldisc").foregroundStyle(.secondary)
        } else {
            LabeledContent(tr("ディスク", "盘片类型", "Disc Type"), value: media.type)
            LabeledContent(tr("状態", "状态", "State"), value: media.busy ? tr("使用中", "使用中", "Busy") : media.blank && media.writable ? tr("空のディスク・書き込み可能", "空白盘片・可写入", "Blank · Writable") : tr("この状態では書き込みできません", "当前状态不可写入", "Not Writable in Current State"))
            if let bytes = media.freeBytes { LabeledContent(tr("空き容量", "可用容量", "Free Space"), value: burnBytes(bytes)) }
        }
    }
    private func errorText(_ error: String) -> some View {
        Label { Text(error).textSelection(.enabled) } icon: { Image(systemName: "exclamationmark.triangle.fill") }.foregroundStyle(.red)
    }
}

struct BurnJobProgress: View {
    @EnvironmentObject private var model: DiscAuthorModel
    let job: BurnJob
    let active: Bool
    private func tr(_ ja: String, _ zh: String, _ en: String) -> String { localized(model.language, ja, zh, en) }
    var body: some View {
        GroupBox {
            VStack(alignment: .leading, spacing: 14) {
                HStack {
                    Label(job.verificationWasSkipped ? tr("書き込み完了・未検証", "已烧录，未校验", "Burned · Not Verified") : burnStage(job.status, language: model.language), systemImage: job.status == "complete" && job.verified ? "checkmark.circle.fill" : job.status == "failed" ? "exclamationmark.triangle.fill" : "opticaldisc")
                        .font(.headline).foregroundStyle(job.status == "complete" && job.verified ? Color.green : job.status == "failed" ? Color.red : Color.primary)
                    Spacer()
                    Button(tr("書き込みログ", "烧录日志", "Burn Log")) { model.showBurnLogs() }
                }
                Text(job.deviceName + " · " + URL(fileURLWithPath: job.iso).lastPathComponent).font(.callout).textSelection(.enabled)
                if active {
                    if let value = job.stageProgress ?? job.progress {
                        HStack(spacing: 16) {
                            Text(job.stageProgress == nil ? tr("全体の進捗", "总进度", "Overall Progress") : burnStage(job.stage, language: model.language)).font(.callout)
                            ProgressView(value: min(100, max(0, value)), total: 100)
                            Text(queuePercentage(value)).font(.system(.body, design: .monospaced)).frame(width: 64, alignment: .trailing)
                        }.accessibilityIdentifier("burnStageProgress")
                    } else {
                        HStack(spacing: 12) {
                            ProgressView().controlSize(.small)
                            Text(burnStage(job.stage, language: model.language) + " · " + tr("ドライブからの進捗通知を待っています", "等待光驱报告进度", "Waiting for drive progress"))
                        }
                        Text(tr("この工程で進捗率が提供されない場合は、完了通知を待ちます。", "若此步骤不提供百分比，会等待光驱返回完成结果。", "If this stage does not report a percentage, the app waits for the drive's completion result.")).font(.caption).foregroundStyle(.secondary)
                    }
                }
                if !job.message.isEmpty { Text(job.message).font(.callout).foregroundStyle(.secondary).textSelection(.enabled) }
                if let error = job.error, !error.isEmpty { Text(error).foregroundStyle(.red).textSelection(.enabled) }
                if job.status == "complete" {
                    if job.verificationWasSkipped {
                        Label(tr("設定によりデータ検証をスキップしました", "已按设置跳过数据校验", "Data verification was skipped as selected"), systemImage: "opticaldisc")
                            .foregroundStyle(.secondary)
                    } else {
                        Label(job.verified ? tr("書き込み後の検証に合格しました", "烧录后校验通过", "Post-burn Verification Passed") : tr("検証完了の確認がありません", "未确认校验完成", "Verification Completion Not Confirmed"), systemImage: job.verified ? "checkmark.shield.fill" : "exclamationmark.shield")
                            .foregroundStyle(job.verified ? Color.green : Color.orange)
                    }
                }
                if active {
                    Text(job.verificationRequired == false
                         ? tr("書き込みが完了するまでアプリを開いたままにしてください。", "请保持应用打开，直到烧录完成。", "Keep the app open until writing finishes.")
                         : tr("書き込みと検証が完了するまでアプリを開いたままにしてください。", "请保持应用打开，直到烧录与校验完成。", "Keep the app open until writing and verification finish."))
                        .font(.caption).foregroundStyle(.secondary)
                }
            }.padding(14).frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

struct PanelTitle: View {
    let title: String
    let icon: String
    var body: some View { Label(title, systemImage: icon).font(.system(size: 14, weight: .semibold)).padding(.bottom, 2) }
}

struct ProjectSettings: View {
    @EnvironmentObject private var model: DiscAuthorModel
    private func tr(_ ja: String, _ zh: String, _ en: String) -> String { localized(model.language, ja, zh, en) }
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            PanelTitle(title: tr("プロジェクト", "项目", "Project"), icon: "doc.text")
            TextField(tr("プロジェクト名", "项目名称", "Project Name"), text: $model.project.title).textFieldStyle(.roundedBorder)
            Text(tr("再生のスタイル", "播放方式", "Playback Style")).font(.caption).foregroundStyle(.secondary).padding(.top, 8)
            Picker(tr("再生のスタイル", "播放方式", "Playback Style"), selection: Binding(get: { model.project.menuEnabled }, set: { enabled in
                var next = model.project
                next.menuEnabled = enabled
                if !enabled { next.noMenuEndAction = .stop }
                model.project = next
            })) {
                Text(tr("メニューあり", "有菜单", "With Menu")).tag(true)
                Text(tr("動画1本・メニューなし", "单视频无菜单", "One Video · No Menu")).tag(false)
            }.labelsHidden().accessibilityIdentifier("menuEnabled")
            Text(model.project.menuEnabled ? tr("番組をリモコンで選んで再生します。", "用遥控器选择节目播放。", "Choose a program with the remote control.") : tr("動画を自動再生し、終了後に停止します。", "自动播放一个视频，结束后停止。", "Autoplay one video, then stop.")).font(.caption).foregroundStyle(.secondary)
            ExportShapeNotice()
            Divider().padding(.vertical, 4)
            Picker(tr("ディスク", "光盘", "Disc"), selection: $model.project.format) { Text("DVD-Video").tag("dvd"); Text("Blu-ray").tag("bluray") }.pickerStyle(.segmented)
                .onChange(of: model.project.format) { value in
                    model.project.capacity = value == "dvd" ? "dvd5" : "bd25"
                    if value == "dvd" && model.project.encoding.quality == "efficient" {
                        model.project.encoding.quality = "high"
                    }
                }
            Picker(tr("容量", "容量", "Capacity"), selection: $model.project.capacity) {
                if model.project.format == "dvd" { Text("DVD-5 · 4.7 GB").tag("dvd5"); Text("DVD-9 · 8.5 GB").tag("dvd9") }
                else { Text("BD-25 · 25 GB").tag("bd25"); Text("BD-50 · 50 GB").tag("bd50") }
            }
            DisclosureGroup(tr("映像方式", "视频制式", "Video Standard")) {
            if model.project.format == "dvd" {
                Picker(tr("映像方式", "视频制式", "Video Standard"), selection: $model.project.standard) { Text("NTSC · 16:9").tag("ntsc"); Text("PAL · 16:9").tag("pal") }
            } else {
                Picker(tr("映像方式", "视频制式", "Video Standard"), selection: $model.project.bdMode) {
                    Text(tr("素材に合わせて自動", "根据素材自动选择", "Automatic for Source")).tag("auto")
                    Text("1080p · 23.976").tag("1080p24")
                    Text("1080p · 24").tag("1080p24_exact")
                    Text("1080i · 59.94").tag("1080i60")
                    Text("1080i · 50").tag("1080i50")
                }
            }
            }.padding(.top, 4)
        }
    }
}

struct ExportShapeNotice: View {
    @EnvironmentObject private var model: DiscAuthorModel
    private func tr(_ ja: String, _ zh: String, _ en: String) -> String { localized(model.language, ja, zh, en) }
    var body: some View {
        if !model.project.menuEnabled && (model.project.videos.count > 1 || model.project.noMenuEndAction != .stop) {
            VStack(alignment: .leading, spacing: 10) {
                Label(tr("出力設定を確認", "请调整导出设置", "Review Export Settings"), systemImage: "exclamationmark.triangle").font(.callout.weight(.medium))
                if model.project.videos.count > 1 {
                    Text(tr("メニューなしは動画 1 本です。素材を削除せずに残しています。「メニューあり」に切り替えるか、素材一覧を 1 本にしてください。", "无菜单仅支持一个视频。已保留全部素材；请切换为“有菜单”，或在素材列表中保留一个视频。", "No Menu supports one video. All sources have been kept; choose With Menu or keep one video in the source list."))
                }
                if model.project.noMenuEndAction != .stop {
                    Text(tr("このプロジェクトには以前の繰り返し設定が残っています。出力する前に「再生後に停止」に変更してください。", "该项目保留了旧版循环设置。请改为“播放结束后停止”再导出。", "This project still has a legacy loop setting. Change it to Stop After Playback before exporting."))
                    Button(tr("再生後に停止へ変更", "改为播放结束后停止", "Change to Stop After Playback")) { model.project.noMenuEndAction = .stop }
                }
            }.font(.caption).foregroundStyle(.orange).padding(12).frame(maxWidth: .infinity, alignment: .leading)
                .background(Color.orange.opacity(0.075), in: RoundedRectangle(cornerRadius: 9)).accessibilityIdentifier("exportShapeWarning")
        }
    }
}

struct QualitySettings: View {
    @EnvironmentObject private var model: DiscAuthorModel
    private func tr(_ ja: String, _ zh: String, _ en: String) -> String { localized(model.language, ja, zh, en) }
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Picker(tr("画質", "画质", "Picture Quality"), selection: $model.project.encoding.quality) {
                Text(tr("高画質（推奨）", "高画质（推荐）", "High Quality (Recommended)")).tag("high")
                Text(tr("バランス", "均衡", "Balanced")).tag("balanced")
                if model.project.format == "bluray" {
                    Text(tr("高効率（1パス・CPU）", "高效单遍（CPU）", "Efficient (Single Pass · CPU)")).tag("efficient")
                }
            }.accessibilityIdentifier("encodingQuality")
            if model.project.encoding.quality == "efficient" {
                Text(tr("CPUによる1パスのソフトウェアエンコードです。ハードウェアエンコードは使用せず、処理速度と画質のバランスを重視します。", "使用 CPU 单遍软件编码，不使用硬件编码；在速度与画质之间取舍。", "Uses single-pass CPU software encoding, without hardware encoding, balancing speed and picture quality.")).font(.caption).foregroundStyle(.secondary)
            } else {
                Text(tr("高画質は変換に時間をかけて仕上げます。", "高画质会使用更长的编码时间。", "High Quality spends more time encoding the video.")).font(.caption).foregroundStyle(.secondary)
            }
            if model.mode == "professional" {
                DisclosureGroup(tr("画質の詳細設定", "画质高级设置", "Advanced Picture Settings")) {
                    VStack(alignment: .leading, spacing: 12) {
                        Picker(tr("元映像の走査方式", "源视频扫描方式", "Source Scan Type"), selection: $model.project.encoding.sourceScan) {
                            Text(tr("自動（ファイル情報）", "自动（文件信息）", "Automatic (File Metadata)")).tag("auto")
                            Text(tr("プログレッシブ", "逐行", "Progressive")).tag("progressive")
                            Text(tr("インターレース・上フィールド先行", "交错・上场优先", "Interlaced · Top Field First")).tag("tff")
                            Text(tr("インターレース・下フィールド先行", "交错・下场优先", "Interlaced · Bottom Field First")).tag("bff")
                        }.accessibilityIdentifier("sourceScanOverride")
                        Text(tr("ファイルの走査方式が誤っている場合のみ変更してください。", "仅在文件的扫描方式标记错误时修改。", "Override only when the file's scan metadata is incorrect.")).font(.caption).foregroundStyle(.secondary)
                        Toggle(tr("軽いノイズ低減", "轻度降噪", "Light Noise Reduction"), isOn: filterBinding(antiFlicker: false)).accessibilityIdentifier("encodingDenoise")
                        Toggle(tr("細線のちらつきを軽減", "细线防闪烁", "Reduce Fine-Line Flicker"), isOn: filterBinding(antiFlicker: true)).accessibilityIdentifier("encodingAntiFlicker")
                        Text(tr("どちらも初期設定はオフです。軽い処理でも細かな質感が柔らかくなる場合があります。", "两项默认关闭，轻度处理也可能使细微纹理变柔和。", "Both are off by default. Even light processing can soften fine textures.")).font(.caption).foregroundStyle(.secondary)
                    }.padding(.top, 10)
                }
            }
        }
    }
    private func filterBinding(antiFlicker: Bool) -> Binding<Bool> {
        Binding(get: { (antiFlicker ? model.project.encoding.antiFlicker : model.project.encoding.denoise) == "light" }, set: { enabled in
            if antiFlicker { model.project.encoding.antiFlicker = enabled ? "light" : "off" }
            else { model.project.encoding.denoise = enabled ? "light" : "off" }
        })
    }
}

struct NoMenuPlaybackSettings: View {
    @EnvironmentObject private var model: DiscAuthorModel
    private func tr(_ ja: String, _ zh: String, _ en: String) -> String { localized(model.language, ja, zh, en) }
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            PanelTitle(title: tr("動画1本・メニューなし", "单视频无菜单", "One Video · No Menu"), icon: "play.rectangle")
            Text(tr("ディスクを入れると自動再生し、動画の終了後に停止します。", "插入光盘后自动播放，视频结束后停止。", "Playback starts when the disc is inserted and stops at the end.")).font(.callout).foregroundStyle(.secondary).accessibilityIdentifier("noMenuEndAction")
            ExportShapeNotice()
        }
    }
}

struct MenuSettings: View {
    @EnvironmentObject private var model: DiscAuthorModel
    @State private var layoutExpanded = true
    @State private var textExpanded = false
    @State private var feedbackExpanded = false
    private func tr(_ ja: String, _ zh: String, _ en: String) -> String { localized(model.language, ja, zh, en) }
    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            PanelTitle(title: tr("メニューデザイン", "菜单设计", "Menu Design"), icon: "rectangle.3.group")
            Text(tr("自動配置、または自分の背景画像でメニューを作成できます。", "使用自动布局或自己的背景图片制作菜单。", "Create a menu with automatic layout or your own background image.")).font(.caption).foregroundStyle(.secondary)
            DisclosureGroup(isExpanded: $layoutExpanded) {
                if layoutExpanded { layoutControls.padding(.top, 14) }
            } label: { Label(tr("背景と配置", "背景与布局", "Background & Layout"), systemImage: "rectangle.3.group") }
            Divider()
            DisclosureGroup(isExpanded: $textExpanded) {
                if textExpanded { textControls.padding(.top, 14) }
            } label: { Label(tr("文字と縁取り", "文字与描边", "Text & Outline"), systemImage: "textformat") }
            Text(model.project.font.label + " · \(model.project.fontSize) pt").font(.caption).foregroundStyle(.secondary).lineLimit(2)
            Divider()
            DisclosureGroup(isExpanded: $feedbackExpanded) {
                if feedbackExpanded { feedbackControls.padding(.top, 14) }
            } label: { Label(tr("選択・決定の見た目", "选中与按下效果", "Selection & Press Effects"), systemImage: "cursorarrow.click") }
            Text(tr("上のプレビューで通常・選択・決定を切り替えて確認できます。", "在预览顶部切换普通、选中、按下，检查实际效果。", "Use Normal, Selected, and Activated above the preview to check each state.")).font(.caption).foregroundStyle(.secondary)
        }
    }
    private var layoutControls: some View {
        VStack(alignment: .leading, spacing: 13) {
            Picker(tr("メニューの案内言語", "菜单导航语言", "Menu Navigation Language"), selection: $model.project.menuLanguage) {
                Text("日本語").tag("ja"); Text("中文").tag("zh"); Text("English").tag("en")
            }.accessibilityIdentifier("menuNavigationLanguage")
            Text(tr("画面の表示言語を変えても、メニューの文字とフォントは変わりません。", "切换界面语言不会改变菜单文字或字体。", "Interface language does not change menu text or fonts.")).font(.caption).foregroundStyle(.secondary)
            if model.project.menuTemplate == nil {
                Stepper(value: $model.project.perPage, in: 1...12) { Text(tr("1 ページ", "每页", "Per Page") + "  \(model.project.perPage)") }
                Picker(tr("配置", "布局", "Layout"), selection: $model.project.layout) { Text(tr("自動配置", "自动布局", "Automatic")).tag("auto"); Text(tr("カスタム", "自定义", "Custom")).tag("custom") }
                    .onChange(of: model.project.layout) { value in if value == "custom" && model.project.buttonRects.isEmpty { model.project.buttonRects = model.preview?.rects ?? [] } }
                HStack { Button { model.chooseBackground() } label: { Label(tr("背景画像…", "背景图片…", "Background…"), systemImage: "photo") }; Spacer(); if !model.project.background.isEmpty { Button(tr("解除", "清除", "Clear")) { model.project.background = "" } } }
                if !model.project.background.isEmpty { Text(URL(fileURLWithPath: model.project.background).lastPathComponent).font(.caption).foregroundStyle(.secondary).lineLimit(1).truncationMode(.middle) }
            }
        }
    }
    private var textControls: some View {
        VStack(alignment: .leading, spacing: 13) {
            HStack { Text(tr("番組名のフォント", "节目名称字体", "Program Font")).font(.subheadline).fontWeight(.medium); Spacer(); Button { model.rescanFonts() } label: { Image(systemName: "arrow.clockwise") }.help(tr("フォントを再検索", "重新扫描字体", "Rescan Fonts")) }
            TextField(tr("本機のフォントを検索", "搜索本机字体", "Search Installed Fonts"), text: $model.fontSearch).textFieldStyle(.roundedBorder)
            Picker(tr("フォント", "字体", "Font"), selection: Binding(get: { model.project.font.id }, set: { model.selectFont(id: $0) })) {
                if !model.filteredFonts.contains(where: { $0.id == model.project.font.id }) { Text(model.project.font.label).tag(model.project.font.id) }
                ForEach(model.filteredFonts) { font in Text(font.label).tag(font.id) }
            }.labelsHidden()
            Stepper(value: $model.project.fontSize, in: 16...100) { Text(tr("文字サイズ", "字号", "Font Size") + "  \(model.project.fontSize)") }
            ColorPicker(tr("通常時の文字色", "普通文字颜色", "Normal Text Color"), selection: textStyleColourBinding(), supportsOpacity: false)
                .accessibilityIdentifier("normalTextColor")
            Toggle(tr("文字に縁を付ける", "文字描边", "Text Outline"), isOn: $model.project.textStyle.outlineEnabled)
                .accessibilityIdentifier("textOutlineEnabled")
            if model.project.textStyle.outlineEnabled { VStack(alignment: .leading, spacing: 10) {
                ColorPicker(tr("縁の色", "描边颜色", "Outline Color"), selection: textStyleColourBinding(outline: true), supportsOpacity: false)
                Stepper(value: $model.project.textStyle.outlineWidth, in: 1...12) { Text(tr("縁の太さ", "描边宽度", "Outline Width") + "  \(model.project.textStyle.outlineWidth) px") }
                    .help(tr("1920 × 1080 のメニュー画像での太さです。", "以 1920 × 1080 菜单画面中的像素为单位。", "Measured in pixels on the 1920 × 1080 menu image."))
            } }
            Button(tr("青い文字・白い縁", "蓝字白边", "Blue Text, White Outline")) { model.applyBlueWhiteTextPreset() }
                .accessibilityIdentifier("blueWhiteTextPreset")
            Text(tr("プリセット：通常は青、選択時は黒、決定時は赤。白い縁はそのまま残ります。", "预设：普通为蓝字，选中变黑，按下变红，白色描边保持不变。", "Preset: blue normally, black when selected, red when activated. The white outline stays visible.")).font(.caption).foregroundStyle(.secondary)
        }
    }
    private var feedbackControls: some View {
        VStack(alignment: .leading, spacing: 13) {
            Text(tr("ボタンのハイライト", "按钮反馈", "Button Feedback")).font(.subheadline).fontWeight(.medium)
            Toggle(tr("領域・枠線の色を変更", "区域／边框变色", "Recolor Button Area / Frame"), isOn: $model.project.buttonFeedback.regionEnabled)
                .accessibilityIdentifier("regionFeedbackEnabled")
            if model.project.buttonFeedback.regionEnabled { VStack(alignment: .leading, spacing: 10) {
                Picker(tr("領域の効果", "区域效果", "Area Effect"), selection: $model.project.buttonFeedback.style) { Text(tr("全体を着色", "区域叠色", "Fill")).tag("fill"); Text(tr("枠線", "边框", "Frame")).tag("frame") }
                HStack {
                    ColorPicker(tr("領域：選択時", "区域选中", "Area Selected"), selection: colourBinding(selected: true), supportsOpacity: false)
                    ColorPicker(tr("領域：決定時", "区域按下", "Area Activated"), selection: colourBinding(selected: false), supportsOpacity: false)
                }
                HStack { Text(tr("領域の不透明度", "区域不透明度", "Area Opacity")); Slider(value: $model.project.buttonFeedback.opacity, in: 0.1...1); Text("\(Int(model.project.buttonFeedback.opacity * 100))%").monospacedDigit().frame(width: 35) }.font(.caption)
            } }
            Toggle(tr("番組名の文字色を変更", "节目文字变色", "Recolor Program Text"), isOn: $model.project.buttonFeedback.textEnabled)
                .accessibilityIdentifier("textFeedbackEnabled")
            if model.project.buttonFeedback.textEnabled { VStack(alignment: .leading, spacing: 10) {
                ColorPicker(tr("文字：選択時", "文字选中", "Text Selected"), selection: colourBinding(selected: true, text: true), supportsOpacity: false)
                ColorPicker(tr("文字：決定時", "文字按下", "Text Activated"), selection: colourBinding(selected: false, text: true), supportsOpacity: false)
            } }
            Text(tr("選択と決定の表示は、DVD／Blu-ray に同じ画像として書き込まれます。", "选中和按下图层与实际 DVD／Blu-ray 共用。", "The same selected and activated layers are written to the DVD or Blu-ray.")).font(.caption).foregroundStyle(.secondary)
        }
    }
    private func textStyleColourBinding(outline: Bool = false) -> Binding<Color> {
        Binding(get: { Color(hexRGB: outline ? model.project.textStyle.outlineColor : model.project.textStyle.color) }, set: { colour in
            if outline { model.project.textStyle.outlineColor = colour.hexRGB } else { model.project.textStyle.color = colour.hexRGB }
        })
    }
    private func colourBinding(selected: Bool, text: Bool = false) -> Binding<Color> {
        Binding(get: {
            let feedback = model.project.buttonFeedback
            return Color(hexRGB: text ? (selected ? feedback.selectedTextColor : feedback.activatedTextColor) : (selected ? feedback.selectedColor : feedback.activatedColor))
        }, set: { colour in
            if text {
                if selected { model.project.buttonFeedback.selectedTextColor = colour.hexRGB } else { model.project.buttonFeedback.activatedTextColor = colour.hexRGB }
            } else {
                if selected { model.project.buttonFeedback.selectedColor = colour.hexRGB } else { model.project.buttonFeedback.activatedColor = colour.hexRGB }
            }
        })
    }
}

struct PlaybackSettings: View {
    @EnvironmentObject private var model: DiscAuthorModel
    private func tr(_ ja: String, _ zh: String, _ en: String) -> String { localized(model.language, ja, zh, en) }
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            PanelTitle(title: tr("再生とリモコン", "播放与遥控", "Playback & Remote"), icon: "dpad")
            Picker(tr("最初の選択", "初始选中", "Initial Selection"), selection: $model.project.firstButton) {
                ForEach(1...max(1, model.project.perPage), id: \.self) { index in Text(tr("番組", "节目", "Program") + " \(index)").tag(index) }
            }
            Text(tr("末尾のページに番組がない番号は、番組 1 に戻ります。", "末页不存在对应节目时，初选节目 1。", "If that program slot is absent on the last page, selection starts at program 1.")).font(.caption).foregroundStyle(.secondary)
            Picker(tr("再生終了後", "播放结束后", "After Playback"), selection: $model.project.afterPlay) {
                Text(tr("メニューへ戻る", "返回菜单", "Return to Menu")).tag("menu")
                Text(tr("次の番組へ", "播放下一个", "Play Next")).tag("next")
                Text(tr("全番組を繰り返す", "循环所有节目", "Loop All")).tag("loop")
            }
            Toggle(tr("戻った時に選択を記憶", "返回时记住所选节目", "Remember Selection on Return"), isOn: $model.project.rememberSelection)
        }
    }
}

struct OutputSettings: View {
    @EnvironmentObject private var model: DiscAuthorModel
    private func tr(_ ja: String, _ zh: String, _ en: String) -> String { localized(model.language, ja, zh, en) }
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            PanelTitle(title: tr("出力先", "输出位置", "Output Folder"), icon: "externaldrive")
            TextField(tr("フォルダのパス", "文件夹路径", "Folder Path"), text: $model.project.outputDir).textFieldStyle(.roundedBorder)
            Button { model.chooseOutput() } label: { Label(tr("フォルダを選択…", "选择文件夹…", "Choose Folder…"), systemImage: "folder") }
            DisclosureGroup(tr("ファイル名と保存方法", "命名与保存方式", "Naming & Save Method")) {
            VStack(alignment: .leading, spacing: 12) {
            Picker(tr("保存方法", "保存方式", "Save Method"), selection: $model.project.exportMode) {
                Text(tr("同じフォルダにプロジェクト名で保存", "统一目录，按项目名称保存", "One Folder, Project Names")).tag("project_name")
                Text(tr("タスクごとに別フォルダ", "每个任务单独文件夹", "Separate Task Folders")).tag("task_folder")
            }.accessibilityIdentifier("exportMode")
            Toggle(tr("ファイル名に DVD / BD を付ける", "文件名附加 DVD／BD", "Append DVD / BD to Filename"), isOn: $model.project.exportFormatSuffix)
                .disabled(model.project.exportMode != "project_name").accessibilityIdentifier("exportFormatSuffix")
            }.padding(.top, 10)
            }
            VStack(alignment: .leading, spacing: 5) {
                Text(tr("ISO ファイル名", "ISO 文件名", "ISO Filename")).font(.caption).foregroundStyle(.secondary)
                Text(model.project.exportFilenamePreview).font(.callout).textSelection(.enabled).lineLimit(3)
                    .accessibilityIdentifier("exportFilenamePreview")
            }
            if model.project.exportMode == "project_name" {
                Text(tr("ISO は選択したフォルダにまとめて保存します。ファイル名には上のプロジェクト名を使います。", "ISO 统一保存到所选文件夹，文件名使用上方的项目名称。", "ISOs are saved together in the selected folder, using the project name above.")).font(.caption).foregroundStyle(.secondary)
                Text(tr("同名のファイルがある場合は番号を付け、上書きしません。使用できない文字は保存時に置き換えます。", "同名文件会自动添加编号，不会覆盖；不可用字符会在保存时替换。", "Existing files are kept; duplicate names receive a number. Unsupported filename characters are replaced when saving.")).font(.caption).foregroundStyle(.secondary)
            } else {
                Text(tr("タスクごとにフォルダを作り、ISO と制作記録を保存します。", "每项任务建立独立文件夹，保存 ISO 与制作记录。", "Each task gets its own folder for the ISO and build records.")).font(.caption).foregroundStyle(.secondary)
            }
        }
    }
}

struct VideoListView: View {
    @EnvironmentObject private var model: DiscAuthorModel
    let compact: Bool
    @State private var selectedRow: Int?
    private func tr(_ ja: String, _ zh: String, _ en: String) -> String { localized(model.language, ja, zh, en) }
    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Label(tr("番組", "节目", "Programs") + "  \(model.project.videos.count)", systemImage: "film.stack").font(.headline)
                Spacer()
                Text(formatDuration(model.totalDuration)).font(.caption).foregroundStyle(.secondary)
                Button { model.chooseVideos() } label: { Label(tr("素材を追加…", "添加素材…", "Add Videos…"), systemImage: "plus") }.buttonStyle(.borderedProminent)
            }.padding(compact ? 16 : 22)
            if model.project.videos.isEmpty {
                VStack(spacing: 16) {
                    Image(systemName: "film.stack").font(.system(size: compact ? 35 : 64, weight: .ultraLight)).foregroundStyle(.secondary)
                    Text(tr("映像を追加してプロジェクトを始めます", "添加视频开始制作", "Add Videos to Begin")).font(compact ? .headline : .title2)
                    if !compact { Text(tr("番組名と順番は後から変更できます。", "节目名称和顺序可以随时调整。", "Program names and order can be changed at any time.")).foregroundStyle(.secondary); Button(tr("映像ファイルを選択…", "选择视频文件…", "Choose Video Files…")) { model.chooseVideos() } }
                }.frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                List(selection: $selectedRow) {
                    ForEach(Array(model.project.videos.enumerated()), id: \.offset) { index, video in
                        HStack(spacing: 14) {
                            Text(String(format: "%02d", index + 1)).font(.system(.title3, design: .rounded).weight(.medium)).foregroundStyle(.secondary).frame(width: 33)
                            VStack(alignment: .leading, spacing: 6) {
                                TextField(tr("番組名", "节目名称", "Program Name"), text: Binding(get: { index < model.project.videos.count ? model.project.videos[index].title : "" }, set: { if index < model.project.videos.count { model.project.videos[index].title = $0 } })).textFieldStyle(.plain).font(.system(size: 14, weight: .medium))
                                Text(video.path).font(.caption).foregroundStyle(.secondary).lineLimit(1).truncationMode(.middle)
                                if let info = video.info {
                                    Text("\(info.width) × \(info.height)  ·  \(sourceFrameRate(info))  ·  \(sourceScan(info))  ·  \(formatDuration(info.duration))").font(.caption2).foregroundStyle(.secondary)
                                    if model.mode == "professional" {
                                        DisclosureGroup(tr("素材情報", "素材信息", "Source Details")) {
                                            VStack(alignment: .leading, spacing: 4) {
                                                Text(tr("コーデック", "编码", "Codec") + ": " + metadata(info.codec))
                                                Text(tr("ピクセル形式", "像素格式", "Pixel Format") + ": " + metadata(info.pixFmt))
                                                Text(tr("色変換行列", "色彩矩阵", "Color Matrix") + ": " + metadata(info.colorSpace))
                                                Text(tr("色の伝達特性", "色彩传递函数", "Color Transfer") + ": " + metadata(info.colorTransfer))
                                                Text(tr("色域", "色域", "Color Primaries") + ": " + metadata(info.colorPrimaries))
                                                Text(tr("信号レンジ", "信号范围", "Color Range") + ": " + metadata(info.colorRange))
                                                Text(tr("ピクセル縦横比", "像素宽高比", "Pixel Aspect Ratio") + ": " + metadata(info.sampleAspectRatio))
                                            }.font(.caption2).foregroundStyle(.secondary).padding(.top, 5)
                                        }.font(.caption)
                                    }
                                }
                            }
                            Spacer(minLength: 4)
                            Button { model.setStep("simulation"); model.playVideo(at: index) } label: { Image(systemName: "play.circle") }.help(tr("素材を再生", "播放素材", "Play Source"))
                            Menu {
                                Button(tr("上へ移動", "上移", "Move Up")) { model.moveVideo(from: index, to: index - 1) }.disabled(index == 0)
                                Button(tr("下へ移動", "下移", "Move Down")) { model.moveVideo(from: index, to: index + 1) }.disabled(index + 1 == model.project.videos.count)
                                Divider()
                                Button(tr("素材を削除", "移除素材", "Remove Source"), role: .destructive) { model.removeVideo(at: index) }
                            } label: { Image(systemName: "ellipsis") }.menuStyle(.borderlessButton).frame(width: 25).help(tr("素材の操作", "素材操作", "Source Actions"))
                        }.padding(.vertical, 9).tag(index)
                    }
                }.listStyle(.inset)
            }
        }
    }
    private func metadata(_ value: String?) -> String {
        guard let value, !["", "unknown", "unspecified", "N/A"].contains(value) else { return tr("未記載", "未标记", "Unmarked") }
        return value
    }
    private func sourceFrameRate(_ info: VideoInfo) -> String {
        for raw in [info.fps, info.rFrameRate].compactMap({ $0 }) {
            let rawParts = raw.split(separator: "/", omittingEmptySubsequences: false)
            let parts = rawParts.compactMap { Double($0) }
            guard parts.count == rawParts.count else { continue }
            let value: Double
            if parts.count == 2 && parts[1] != 0 { value = parts[0] / parts[1] }
            else if parts.count == 1 { value = parts[0] }
            else { continue }
            if value.isFinite && value > 0 {
                let number = String(format: "%.3f", value).replacingOccurrences(of: "0+$", with: "", options: .regularExpression).replacingOccurrences(of: "\\.$", with: "", options: .regularExpression)
                return number + " fps"
            }
        }
        return tr("フレームレート未記載", "帧率未标记", "Frame Rate Unmarked")
    }
    private func sourceScan(_ info: VideoInfo) -> String {
        switch info.fieldOrder {
        case "progressive": return tr("プログレッシブ", "逐行", "Progressive")
        case "tt", "tff": return tr("上フィールド先行", "上场优先", "Top Field First")
        case "bb", "bff": return tr("下フィールド先行", "下场优先", "Bottom Field First")
        case "tb", "bt": return tr("インターレース", "交错", "Interlaced") + " (" + (info.fieldOrder ?? "") + ")"
        default: return tr("走査方式未記載", "扫描方式未标记", "Scan Type Unmarked")
        }
    }
}

private func formatDuration(_ seconds: Double) -> String {
    let value = max(0, Int(seconds))
    return value >= 3600 ? String(format: "%d:%02d:%02d", value / 3600, value / 60 % 60, value % 60) : String(format: "%d:%02d", value / 60, value % 60)
}

private extension Color {
    init(hexRGB: String) {
        let value = UInt64(hexRGB.trimmingCharacters(in: CharacterSet(charactersIn: "#")), radix: 16) ?? 0
        self.init(red: Double(value >> 16 & 255) / 255, green: Double(value >> 8 & 255) / 255, blue: Double(value & 255) / 255)
    }
    var hexRGB: String {
        let c = NSColor(self).usingColorSpace(.deviceRGB) ?? .black
        return String(format: "#%02x%02x%02x", Int(c.redComponent * 255), Int(c.greenComponent * 255), Int(c.blueComponent * 255))
    }
}

struct NoMenuWorkspace: View {
    @EnvironmentObject private var model: DiscAuthorModel
    private func tr(_ ja: String, _ zh: String, _ en: String) -> String { localized(model.language, ja, zh, en) }
    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Label(tr("動画再生シミュレーション", "视频播放模拟", "Video Playback Simulation"), systemImage: "play.rectangle").font(.headline)
                Spacer()
                if model.project.videos.isEmpty {
                    Button(tr("動画を追加…", "导入一段视频…", "Add One Video…")) { model.chooseVideos() }
                } else {
                    Button(tr("動画を再生", "播放视频", "Play Video")) { model.startNoMenuSimulation() }
                        .disabled(model.project.videos.count != 1 || model.project.noMenuEndAction != .stop)
                        .accessibilityIdentifier("startNoMenuSimulation")
                }
                Button(tr("停止", "停止", "Stop")) { model.stopSimulation() }
                    .disabled(model.playingIndex == nil).accessibilityIdentifier("stopNoMenuSimulation")
            }.padding(18)
            Divider()
            if let player = model.player, let index = model.playingIndex {
                NativeVideoPlayer(player: player).background(.black)
                HStack {
                    if model.project.videos.count > 1 { Text("\(index+1) / \(model.project.videos.count)").monospacedDigit().foregroundStyle(.secondary) }
                    Text(model.project.videos.indices.contains(index) ? model.project.videos[index].title : "").font(.headline).lineLimit(1)
                    Spacer()
                }.padding(18)
            } else {
                VStack(spacing: 16) {
                    Image(systemName: model.simulationStopped ? "stop.circle" : "play.circle").font(.system(size: 44)).foregroundStyle(.secondary)
                    Text(model.simulationStopped ? tr("再生は停止しました", "播放已停止", "Playback Stopped") : tr("メニューを表示せずに再生します", "直接播放，不显示菜单", "Playback Starts without a Menu")).font(.title3)
                    Text(model.project.videos.isEmpty ? tr("動画を 1 本追加し、DVD-Video または Blu-ray を選んでください。", "导入一段视频，选择 DVD-Video 或 Blu-ray。", "Add one video and choose DVD-Video or Blu-ray.") : model.project.videos.count == 1 ? tr("ディスクを入れると、この動画を自動で再生します。", "插入光盘后自动播放这段视频。", "This video plays automatically when the disc is inserted.") : tr("メニューなしで出力するには、素材を 1 本にしてください。", "无菜单导出需要保留一个视频。", "Keep one video to export without a menu.")).font(.callout).foregroundStyle(.secondary).multilineTextAlignment(.center).frame(maxWidth: 460)
                }.padding(30).frame(maxWidth: .infinity, maxHeight: .infinity).background(Color(nsColor: .controlBackgroundColor))
            }
            HStack {
                Image(systemName: "stop.fill")
                Text(tr("自動再生・終了後に停止", "自动播放，结束后停止", "Autoplay · Stop at the End"))
                Spacer()
            }.font(.caption).foregroundStyle(.secondary).padding(18)
        }
    }
}

struct MenuWorkspace: View {
    @EnvironmentObject private var model: DiscAuthorModel
    let simulation: Bool
    let compact: Bool
    @State private var editingTitle: Int?
    @State private var buttonDetailsExpanded = false
    private func tr(_ ja: String, _ zh: String, _ en: String) -> String { localized(model.language, ja, zh, en) }
    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 12) {
                Label(simulation ? tr("再生シミュレーション", "播放模拟", "Playback Simulation") : tr("メニュー編集", "菜单编辑", "Menu Editor"), systemImage: simulation ? "play.rectangle" : "cursorarrow.and.square.on.square.dashed").font(.headline)
                Spacer()
                if model.playingIndex == nil {
                    Button { model.setPage(max(0, model.currentPage - 1)) } label: { Image(systemName: "chevron.left") }.disabled(model.currentPage == 0)
                    Text("\(model.currentPage + 1) / \(model.preview?.pages ?? 1)").monospacedDigit().font(.callout)
                    Button { model.setPage(min((model.preview?.pages ?? 1) - 1, model.currentPage + 1)) } label: { Image(systemName: "chevron.right") }.disabled(model.currentPage + 1 >= (model.preview?.pages ?? 1))
                    Button { model.refreshPreview() } label: { Image(systemName: "arrow.clockwise") }.help(tr("プレビューを更新", "更新预览", "Refresh Preview"))
                }
            }.padding(.horizontal, 20).padding(.vertical, 14)
            if !simulation {
                HStack {
                    Text(tr("ボタンの表示", "按钮显示状态", "Button State")).font(.caption).foregroundStyle(.secondary)
                    Picker(tr("ボタンの表示", "按钮显示状态", "Button State"), selection: $model.previewState) {
                        Text(tr("通常", "普通", "Normal")).tag("normal")
                        Text(tr("選択", "选中", "Selected")).tag("selected")
                        Text(tr("決定", "按下", "Activated")).tag("activated")
                    }.pickerStyle(.segmented).labelsHidden().frame(width: 260).accessibilityIdentifier("buttonFeedbackState")
                    Spacer()
                }.padding(.horizontal, 20).padding(.bottom, 12)
            }
            Divider()
            if simulation, let player = model.player, let index = model.playingIndex {
                VStack(spacing: 12) {
                    NativeVideoPlayer(player: player).background(.black)
                    HStack {
                        Text(index < model.project.videos.count ? model.project.videos[index].title : "").font(.headline).lineLimit(1)
                        Spacer()
                        Button { model.returnToMenu() } label: { Label(tr("メニューへ戻る", "返回菜单", "Return to Menu"), systemImage: "rectangle.grid.2x2") }
                    }.padding(.horizontal, 18).padding(.bottom, 14)
                }
            } else {
                NativeMenuCanvas(simulation: simulation, editTitle: { editingTitle = $0 })
                    .padding(compact ? 12 : 24)
                    .background(Color(nsColor: .controlBackgroundColor))
                if compact {
                    HStack {
                        Text(tr("クリックで選択、ドラッグで移動、ダブルクリックで番組名を編集", "单击选中，拖动位置，双击编辑节目名称", "Click to select · Drag to move · Double-click to rename")).font(.caption).foregroundStyle(.secondary)
                        Spacer()
                        Button(tr("再生を確認", "模拟播放", "Simulate")) { model.setStep("simulation") }
                    }.padding(.horizontal, 20).padding(.vertical, 10)
                } else if !simulation {
                    Divider()
                    DisclosureGroup(isExpanded: $buttonDetailsExpanded) {
                        ButtonInspector(editTitle: { editingTitle = $0 }).padding(.top, 14)
                    } label: { Label(tr("選択ボタンの位置とリモコン", "选中按钮的位置与遥控", "Selected Button: Position & Remote"), systemImage: "slider.horizontal.3") }.padding(18)
                } else {
                    HStack { Image(systemName: "keyboard"); Text(tr("方向キーで選択、Return で決定", "方向键选择，Return 确认", "Arrow keys select · Return activates")); Spacer(); Text(model.selectedButton?.label ?? "").fontWeight(.medium) }.font(.callout).padding(18)
                    RemoteKeyboardShortcuts()
                }
            }
        }
        .sheet(isPresented: Binding(get: { editingTitle != nil }, set: { if !$0 { editingTitle = nil } })) {
            VStack(alignment: .leading, spacing: 18) {
                Text(tr("番組名を編集", "编辑节目名称", "Edit Program Name")).font(.title2)
                if let index = editingTitle, index < model.project.videos.count {
                    TextField(tr("番組名", "节目名称", "Program Name"), text: Binding(get: { model.project.videos[index].title }, set: { model.project.videos[index].title = $0 })).textFieldStyle(.roundedBorder).frame(width: 420)
                }
                HStack { Spacer(); Button(tr("完了", "完成", "Done")) { editingTitle = nil }.keyboardShortcut(.defaultAction) }
            }.padding(26)
        }
    }
}

/// Use the AppKit player directly, avoiding the AVKit SwiftUI overlay runtime.
struct NativeVideoPlayer: NSViewRepresentable {
    let player: AVPlayer
    func makeNSView(context: Context) -> AVPlayerView {
        let view = AVPlayerView()
        view.player = player
        view.controlsStyle = .inline
        view.videoGravity = .resizeAspect
        view.showsFullScreenToggleButton = true
        view.showsSharingServiceButton = false
        return view
    }
    func updateNSView(_ view: AVPlayerView, context: Context) {
        if view.player !== player { view.player = player }
    }
    static func dismantleNSView(_ view: AVPlayerView, coordinator: ()) {
        view.player = nil
    }
}

struct NativeMenuCanvas: View {
    @EnvironmentObject private var model: DiscAuthorModel
    let simulation: Bool
    let editTitle: (Int) -> Void
    var body: some View {
        GeometryReader { geometry in
            let width = min(geometry.size.width, geometry.size.height * 16 / 9)
            let height = width * 9 / 16
            let scale = width / 1920
            ZStack(alignment: .topLeading) {
                if let image = model.baseImage {
                    Image(nsImage: image).resizable().interpolation(.high).frame(width: width, height: height)
                    if let preview = model.preview {
                        ForEach(Array(preview.buttons.enumerated()), id: \.offset) { index, button in
                            CanvasButton(index: index, button: button, scale: scale, simulation: simulation, editTitle: editTitle)
                        }
                    }
                } else {
                    RoundedRectangle(cornerRadius: 10).fill(Color(nsColor: .windowBackgroundColor))
                    ProgressView().position(x: width / 2, y: height / 2)
                }
            }
            .frame(width: width, height: height)
            .clipped()
            .shadow(color: .black.opacity(0.13), radius: 14, y: 5)
            .position(x: geometry.size.width / 2, y: geometry.size.height / 2)
        }
    }
}

private struct CanvasButton: View {
    @EnvironmentObject private var model: DiscAuthorModel
    let index: Int
    let button: MenuButton
    let scale: CGFloat
    let simulation: Bool
    let editTitle: (Int) -> Void
    @State private var drag: CGSize = .zero
    private var rect: [Int] {
        if model.project.menuTemplate == nil, button.title != nil, model.project.layout == "custom", index < model.project.buttonRects.count, model.project.buttonRects[index].count == 4 { return model.project.buttonRects[index] }
        return button.rect
    }
    private var selected: Bool { model.selectedIndex == index }
    private var overlayImage: NSImage? {
        guard selected, let images = model.buttonImages[index] else { return nil }
        if simulation { return model.isActivated ? images.activated : images.selected }
        switch model.previewState {
        case "normal": return nil
        case "activated": return images.activated
        default: return images.selected
        }
    }
    var body: some View {
        let box = rect
        ZStack {
            if let image = overlayImage {
                Image(nsImage: image).resizable().interpolation(.none)
            }
            if !simulation {
                Rectangle().strokeBorder(selected ? Color.accentColor : Color.white.opacity(0.25), style: StrokeStyle(lineWidth: selected ? 2 : 1, dash: selected ? [] : [4, 3]))
                if selected {
                    VStack { HStack { Text("\(index + 1)").font(.system(size: 10, weight: .semibold)).padding(.horizontal, 5).padding(.vertical, 2).background(Color.accentColor).foregroundStyle(.white); Spacer() }; Spacer() }
                }
            }
            Color.clear.contentShape(Rectangle())
        }
        .frame(width: CGFloat(box[2]) * scale, height: CGFloat(box[3]) * scale)
        .offset(x: CGFloat(box[0]) * scale + drag.width, y: CGFloat(box[1]) * scale + drag.height)
        .onTapGesture(count: 2) {
            if !simulation, let title = button.title, title > 0, title <= model.project.videos.count { model.selectButton(index); editTitle(title - 1) }
        }
        .onTapGesture {
            model.selectButton(index)
            if simulation { model.activateSelected() }
        }
        .gesture(DragGesture(minimumDistance: 3).onChanged { value in
            guard !simulation, model.project.menuTemplate == nil, button.title != nil else { return }
            model.selectButton(index)
            drag = value.translation
        }.onEnded { value in
            guard !simulation, model.project.menuTemplate == nil, button.title != nil, scale > 0 else { return }
            let x = max(0, min(1920 - box[2], box[0] + Int((value.translation.width / scale).rounded())))
            let y = max(0, min(1080 - box[3], box[1] + Int((value.translation.height / scale).rounded())))
            model.updateButtonRect(index: index, rect: [x, y, box[2], box[3]])
            drag = .zero
        })
        .accessibilityLabel(button.label)
        .accessibilityIdentifier("menu_button_\(index + 1)")
    }
}

struct ButtonInspector: View {
    @EnvironmentObject private var model: DiscAuthorModel
    let editTitle: (Int) -> Void
    private func tr(_ ja: String, _ zh: String, _ en: String) -> String { localized(model.language, ja, zh, en) }
    var body: some View {
        if let button = model.selectedButton {
            VStack(alignment: .leading, spacing: 12) {
                HStack {
                    Text(tr("選択中", "当前选中", "Selected") + "  \(model.selectedIndex + 1) · \(button.label)").font(.headline).lineLimit(1)
                    Spacer()
                    Button(tr("移動先を自動設定", "重置方向目标", "Reset Navigation")) { model.resetNavigation() }
                    if let title = button.title, title <= model.project.videos.count { Button(tr("番組名を編集", "编辑名称", "Rename")) { editTitle(title - 1) } }
                }
                HStack(alignment: .top, spacing: 24) {
                    if button.title != nil && model.project.menuTemplate == nil {
                        VStack(alignment: .leading, spacing: 8) {
                            Text(tr("位置とサイズ", "位置和尺寸", "Position & Size")).font(.caption).foregroundStyle(.secondary)
                            HStack(spacing: 9) { coordinate("X", 0, button); coordinate("Y", 1, button); coordinate("W", 2, button); coordinate("H", 3, button) }
                        }
                    }
                    VStack(alignment: .leading, spacing: 8) {
                        Text(tr("方向キーの移動先", "方向键目标", "Directional Targets")).font(.caption).foregroundStyle(.secondary)
                        HStack(spacing: 10) { direction("↑", 0, button); direction("↓", 1, button); direction("←", 2, button); direction("→", 3, button) }
                    }
                    Spacer(minLength: 0)
                }
                Text(tr("ドラッグで移動できます。リモコン順序は設定したボタンへ循環します。", "可拖动调整位置。遥控器按设置的目标循环。", "Drag to reposition. Remote navigation follows the configured button targets.")).font(.caption).foregroundStyle(.secondary)
            }
        } else { Text(tr("ボタンを選択して編集します", "选择按钮进行编辑", "Select a Button to Edit")).foregroundStyle(.secondary) }
    }
    private func inspectorRect(_ button: MenuButton) -> [Int] {
        if model.project.menuTemplate == nil, model.project.layout == "custom", model.selectedIndex < model.project.buttonRects.count { return model.project.buttonRects[model.selectedIndex] }
        return button.rect
    }
    private func coordinate(_ label: String, _ component: Int, _ button: MenuButton) -> some View {
        VStack(spacing: 3) {
            Text(label).font(.caption2).foregroundStyle(.secondary)
            TextField(label, text: Binding(get: { String(inspectorRect(button)[component]) }, set: { value in
                guard let number = Int(value) else { return }
                var rect = inspectorRect(button)
                rect[component] = number
                rect[2] = max(80, min(1920, rect[2])); rect[3] = max(40, min(1080, rect[3]))
                rect[0] = max(0, min(1920 - rect[2], rect[0])); rect[1] = max(0, min(1080 - rect[3], rect[1]))
                model.updateButtonRect(index: model.selectedIndex, rect: rect)
            })).textFieldStyle(.roundedBorder).frame(width: 53)
        }
    }
    private func direction(_ icon: String, _ direction: Int, _ button: MenuButton) -> some View {
        Picker(icon, selection: Binding(get: { (model.project.navigation[String(model.selectedIndex + 1)] ?? button.nav)[direction] }, set: { model.setDirection(index: model.selectedIndex, direction: direction, target: $0) })) {
            ForEach(1...max(1, model.preview?.buttons.count ?? 1), id: \.self) { value in Text("\(value)").tag(value) }
        }.frame(width: 68)
    }
}

struct RemoteControl: View {
    @EnvironmentObject private var model: DiscAuthorModel
    private func tr(_ ja: String, _ zh: String, _ en: String) -> String { localized(model.language, ja, zh, en) }
    var body: some View {
        VStack(spacing: 16) {
            PanelTitle(title: tr("リモコン", "遥控器", "Remote Control"), icon: "dpad.fill")
            VStack(spacing: 8) {
                remoteButton("chevron.up", 0)
                HStack(spacing: 8) {
                    remoteButton("chevron.left", 2)
                    Button { model.activateSelected() } label: { Text("OK").fontWeight(.semibold).frame(width: 56, height: 40) }.buttonStyle(.borderedProminent).disabled(model.playingIndex != nil)
                    remoteButton("chevron.right", 3)
                }
                remoteButton("chevron.down", 1)
            }
            HStack { Button("TOP MENU") { model.topMenu() }; Button(tr("戻る", "返回", "Return")) { model.returnToMenu() } }
            Text(model.selectedButton?.label ?? "").font(.callout).lineLimit(2).multilineTextAlignment(.center)
        }.frame(maxWidth: .infinity)
    }
    private func remoteButton(_ icon: String, _ direction: Int) -> some View {
        Button { model.navigate(direction: direction) } label: { Image(systemName: icon).frame(width: 45, height: 34) }.disabled(model.playingIndex != nil)
    }
}

private struct RemoteKeyboardShortcuts: View {
    @EnvironmentObject private var model: DiscAuthorModel
    var body: some View {
        HStack(spacing: 0) {
            Button("") { model.navigate(direction: 0) }.keyboardShortcut(.upArrow, modifiers: [])
            Button("") { model.navigate(direction: 1) }.keyboardShortcut(.downArrow, modifiers: [])
            Button("") { model.navigate(direction: 2) }.keyboardShortcut(.leftArrow, modifiers: [])
            Button("") { model.navigate(direction: 3) }.keyboardShortcut(.rightArrow, modifiers: [])
            Button("") { model.activateSelected() }.keyboardShortcut(.return, modifiers: [])
        }.frame(width: 0, height: 0).clipped().opacity(0).accessibilityHidden(true)
    }
}

struct EncodingPreflightNotes: View {
    @EnvironmentObject private var model: DiscAuthorModel
    let check: PreflightResult
    private func tr(_ ja: String, _ zh: String, _ en: String) -> String { localized(model.language, ja, zh, en) }
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if let mode = check.outputMode, !mode.isEmpty {
                Label(tr("実際の出力方式", "实际输出制式", "Resolved Output Format") + ": " + modeName(mode), systemImage: "video").font(.callout)
            }
            ForEach(Array((check.encodingWarnings ?? []).enumerated()), id: \.offset) { _, warning in
                HStack(alignment: .top, spacing: 9) {
                    Image(systemName: "info.circle").foregroundStyle(.orange)
                    VStack(alignment: .leading, spacing: 5) {
                        if let index = warning.videoIndex, model.project.videos.indices.contains(index) {
                            Text(model.project.videos[index].title).font(.caption).fontWeight(.semibold)
                        }
                        Text(warningText(warning.code)).font(.callout)
                        if let source = warning.source, let target = warning.target, !source.isEmpty || !target.isEmpty {
                            Text(source + " → " + target).font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
                        } else if let target = warning.target, !target.isEmpty {
                            Text(target).font(.caption).foregroundStyle(.secondary)
                        }
                    }
                }
            }
        }.frame(maxWidth: .infinity, alignment: .leading)
    }
    private func modeName(_ value: String) -> String {
        switch value {
        case "1080p24": return "1080p · 23.976"
        case "1080p24_exact": return "1080p · 24"
        case "1080i60": return "1080i · 59.94"
        case "1080i50": return "1080i · 50"
        case "ntsc": return "NTSC"
        case "pal": return "PAL"
        default: return value
        }
    }
    private func warningText(_ code: String) -> String {
        switch code {
        case "unknown_scan":
            return tr("素材に走査方式の記載がありません。元映像の走査設定を確認してください。", "素材未标记扫描方式，请核对源视频扫描设置。", "The source has no scan-type tag. Check the source scan setting.")
        case "assumed_color":
            return tr("素材の色情報が不足しているため、表示された色設定を適用します。", "素材色彩信息不完整，将采用下方显示的色彩设置。", "The source color tags are incomplete; the color settings shown below will be used.")
        case "cadence_change":
            return tr("元映像と出力のフレームレートが異なります。", "源视频与输出的帧率不同。", "The source and output frame rates differ.")
        case "motion_to_24p":
            return tr("24p への変換で動きの滑らかさが変わる場合があります。", "转换为 24p 可能降低运动画面的流畅度。", "Converting to 24p may reduce motion smoothness.")
        case "filters_soften":
            return tr("選択した処理で細かな質感が柔らかくなる場合があります。", "所选处理可能使细微纹理变柔和。", "The selected processing may soften fine textures.")
        default:
            return tr("映像設定に関するお知らせ", "视频设置提示", "Video Settings Notice")
        }
    }
}

struct OutputSummary: View {
    @EnvironmentObject private var model: DiscAuthorModel
    private func tr(_ ja: String, _ zh: String, _ en: String) -> String { localized(model.language, ja, zh, en) }
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                Text(tr("出力の確認", "输出确认", "Review Output")).font(.title)
                HStack(spacing: 30) {
                    metric(tr("番組", "节目", "Programs"), "\(model.project.videos.count)", "film.stack")
                    metric(tr("総再生時間", "总时长", "Duration"), formatDuration(model.totalDuration), "clock")
                    metric(tr("形式", "格式", "Format"), model.project.format == "dvd" ? "DVD-Video" : "Blu-ray", "opticaldisc")
                }
                if !model.project.menuEnabled {
                    Label(tr("動画1本・メニューなし", "单视频无菜单", "One Video · No Menu"), systemImage: "play.rectangle").font(.headline)
                    Text(tr("ディスクを入れると自動再生し、終了後に停止します。", "插入光盘后自动播放，结束后停止。", "Autoplay when the disc is inserted, then stop at the end."))
                    ExportShapeNotice()
                }
                if let check = model.preflight {
                    GroupBox {
                        VStack(alignment: .leading, spacing: 13) {
                            Label(tr("出力前チェック完了", "制作前检查通过", "Preflight Passed"), systemImage: "checkmark.circle.fill").foregroundStyle(.green).font(.headline)
                            Text(tr("メニューページ", "菜单页数", "Menu Pages") + ": \(check.pages)")
                            Text(tr("映像ビットレート", "视频码率", "Video Bitrate") + ": \(check.videoBitrateKbps) kbps")
                            Text(String(format: tr("必要容量 %.1f GB ／ 空き容量 %.1f GB", "需要 %.1f GB ／ 可用 %.1f GB", "Required %.1f GB / Available %.1f GB"), check.requiredGB, check.availableGB))
                            EncodingPreflightNotes(check: check)
                        }.frame(maxWidth: .infinity, alignment: .leading).padding(12)
                    }
                } else {
                    Label(model.project.menuEnabled ? tr("出力前チェックで素材・容量・リモコン経路を確認します。", "制作前检查会验证素材、容量和遥控器路径。", "Preflight checks media, capacity, and remote navigation.") : tr("出力前チェックで素材と容量を確認します。", "制作前检查会验证素材和容量。", "Preflight checks media and capacity."), systemImage: "checkmark.shield").foregroundStyle(.secondary)
                }
                QueueCurrentProgress(queue: model.queue)
                Text(tr("作成後は使用するプレーヤーで再生確認を行ってください。", "制作后请在实际使用的播放器上验盘。", "Verify the completed disc on the intended player.")).font(.caption).foregroundStyle(.secondary)
            }.padding(32).frame(maxWidth: .infinity, alignment: .leading)
        }
    }
    private func metric(_ title: String, _ value: String, _ icon: String) -> some View {
        VStack(alignment: .leading, spacing: 10) { Label(title, systemImage: icon).font(.caption).foregroundStyle(.secondary); Text(value).font(.title2).fontWeight(.medium) }.frame(maxWidth: .infinity, alignment: .leading)
    }
}
