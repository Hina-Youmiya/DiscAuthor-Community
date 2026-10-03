import SwiftUI
import AppKit
import UniformTypeIdentifiers

struct DirectBurnJob: Decodable, Equatable {
    var id: String
    var status: String
    var stage: String
    var progress: Double?
    var stageProgress: Double?
    var message: String
    var error: String?
    var video: String
    var deviceID: String
    var deviceName: String
    var format: String
    var capacity: String
    var verified: Bool
    var folder: String
    var iso: String?
    var canRetry: Bool
    var cleanupError: String?
    var burnID: String?
    var verificationSkipped: Bool?
    enum CodingKeys: String, CodingKey {
        case id, status, stage, progress, message, error, video, format, capacity, verified, folder, iso
        case stageProgress = "stage_progress", deviceID = "device_id", deviceName = "device_name"
        case canRetry = "can_retry", cleanupError = "cleanup_error"
        case burnID = "burn_id"
        case verificationSkipped = "verification_skipped"
    }
}
struct DirectBurnSnapshot: Decodable, Equatable {
    var active = false
    var job: DirectBurnJob?
}
struct DirectBurnCheck: Decodable {
    var valid: Bool
    var errors: [String]
    var device: BurnDevice?
    var format: String?
    var capacity: String?
    var project: PreflightResult?
}
struct ReadableDisc: Decodable, Identifiable {
    var id: String
    var name: String
    var format: String
    var label: String
    var present: Bool
    var blank: Bool
    var busy: Bool
    var mounted: Bool
    var readable: Bool
    var playable: Bool
    var path: String?
    var reason: String?
}
struct ReadableDiscs: Decodable {
    var devices: [ReadableDisc] = []
    var error: String?
}
struct DiscPlaybackSource: Decodable {
    var path: String
    var kind: String
    var format: String
    var label: String
    var readable: Bool
    var playable: Bool
}
struct DiscTrayResult: Decodable {
    var ok: Bool
    var connected: Bool
    var confirmed: Bool
    var trayOpen: Bool?
    var osStatus: Int
    enum CodingKeys: String, CodingKey { case ok, connected, confirmed; case trayOpen = "tray_open", osStatus = "os_status" }
}

@MainActor final class DiscToolsState: ObservableObject {
    weak var owner: DiscAuthorModel?
    let player = DiscPlayerEngine()
    @Published var video = "" { didSet { invalidate() } }
    @Published var workingDirectory = "" { didSet { invalidate() } }
    @Published var deviceID = "" { didSet { invalidateForDiscChange() } }
    @Published var quality = "high" { didSet { invalidate() } }
    @Published var standard = "ntsc" { didSet { invalidate() } }
    @Published var endAction = "stop" { didSet { invalidate() } }
    @Published var speed = 0.0 { didSet { invalidate() } }
    @Published var verifyAfterBurn = true { didSet { invalidate() } }
    @Published private(set) var drives = BurnDevices() { didSet { invalidateForDiscChange() } }
    @Published private(set) var direct = DirectBurnSnapshot()
    @Published private(set) var check: DirectBurnCheck?
    @Published private(set) var isChecking = false
    @Published private(set) var isStarting = false
    @Published private(set) var awaitingStatus = false
    @Published private(set) var refreshing = false
    @Published var error: String?
    @Published var connectionError: String?
    @Published private(set) var discs = ReadableDiscs()
    @Published var readDeviceID = ""
    @Published private(set) var readSource: DiscPlaybackSource?
    @Published private(set) var loadingDisc = false
    @Published private(set) var refreshingDiscs = false
    @Published var readError: String?
    @Published private(set) var trayBusy = false
    @Published private(set) var trayMessage: String?
    private var generation = 0
    private var readGeneration = 0
    private var polling: Task<Void, Never>?
    var protectsTermination: Bool { direct.active || isStarting || awaitingStatus || trayBusy }
    var device: BurnDevice? { drives.devices.first { $0.id == deviceID } }
    var format: String { device?.media.format ?? "" }
    var readDevice: ReadableDisc? { discs.devices.first { $0.id == readDeviceID } }
    var canStart: Bool {
        !protectsTermination && !isChecking && !refreshing && !video.isEmpty && !workingDirectory.isEmpty &&
        check?.valid == true && check?.device?.id == deviceID && device?.media.isReady == true && owner?.burn.protectsTermination != true &&
        owner?.queue.running != true && owner?.queue.active == nil
    }
    var canRetry: Bool {
        !protectsTermination && direct.job?.canRetry == true && !deviceID.isEmpty &&
        owner?.burn.protectsTermination != true
    }
    private func tr(_ ja: String, _ zh: String, _ en: String) -> String {
        owner?.language == "zh" ? zh : owner?.language == "en" ? en : ja
    }
    func attach(to model: DiscAuthorModel) {
        guard owner == nil else { return }
        owner = model
        workingDirectory = model.project.outputDir
        polling = Task { [weak self] in
            var ticks = 0
            while !Task.isCancelled {
                guard let self else { return }
                await self.poll()
                if self.owner?.showBurn == true && ticks % 6 == 0 && !self.protectsTermination {
                    if self.owner?.discToolTab == "video" { await self.loadDrives() }
                    if self.owner?.discToolTab == "player" { await self.loadDiscs() }
                }
                ticks += 1
                try? await Task.sleep(nanoseconds: 1_000_000_000)
            }
        }
    }
    func shutdown() { polling?.cancel(); polling = nil; player.stop() }
    func prepareForBurn() { readGeneration += 1; loadingDisc = false; player.stop() }
    var trayDeviceID: String {
        guard let owner else { return "" }
        if owner.discToolTab == "iso" { return owner.burn.selectedDeviceID }
        if owner.discToolTab == "video" { return deviceID }
        return readDeviceID
    }
    func controlTray(_ action: String) {
        guard ["eject", "close"].contains(action), !trayDeviceID.isEmpty,
              owner?.burnProtectsTermination != true else { return }
        let selected = trayDeviceID
        prepareForBurn(); trayBusy = true; trayMessage = nil; error = nil
        Task {
            defer { trayBusy = false; refreshDrives(); refreshDiscs(); owner?.refreshBurnDevices() }
            do {
                await player.stopAndWait()
                let result: DiscTrayResult = try await request("/api/burn/tray", ["device_id": selected, "action": action], timeout: 30)
                if !result.ok {
                    trayMessage = !result.connected ? tr("光学ドライブが切断されました。", "光驱已断开。", "The optical drive was disconnected.") : tr("トレイの操作に失敗しました。", "托盘操作失败。", "Tray operation failed.") + " (\(result.osStatus))"
                } else if result.confirmed {
                    trayMessage = action == "eject" ? tr("トレイを開きました。", "托盘已弹出。", "Tray opened.") : tr("トレイを閉じました。", "托盘已收起。", "Tray closed.")
                } else {
                    trayMessage = tr("操作を送信しました。トレイの状態を確認してください。", "指令已发送，请确认托盘的实际状态。", "Command sent. Check the tray position.")
                }
            } catch { trayMessage = error.localizedDescription }
        }
    }
    private func request<T: Decodable>(_ path: String, _ payload: [String: Any]? = nil, timeout: TimeInterval = 30) async throws -> T {
        guard let owner else { throw NSError(domain: "DiscAuthor", code: 1, userInfo: [NSLocalizedDescriptionKey: "Authoring service unavailable"]) }
        let data = try payload.map { try JSONSerialization.data(withJSONObject: $0) }
        return try await owner.discToolRequest(path, body: data, timeout: timeout)
    }
    private func invalidate() { generation += 1; check = nil }
    private func invalidateForDiscChange() {
        if format == "dvd" && quality == "efficient" {
            quality = "high"
        } else {
            invalidate()
        }
    }
    private var payload: [String: Any] {
        var value: [String: Any] = ["video": video, "device_id": deviceID, "working_dir": workingDirectory,
            "verify_after_burn": verifyAfterBurn,
            "standard": standard, "bd_mode": "auto", "no_menu_end_action": "stop",
            "encoding": ["quality": quality, "denoise": "off", "anti_flicker": "off", "source_scan": "auto"]]
        if speed > 0 { value["speed"] = speed }
        return value
    }
    func chooseVideo() {
        guard !protectsTermination else { return }
        let panel = NSOpenPanel(); panel.canChooseDirectories = false; panel.allowsMultipleSelection = false
        panel.allowedContentTypes = [.movie, .mpeg, .mpeg4Movie, .quickTimeMovie, .data]
        panel.message = tr("ディスクで自動再生する動画を1本選択します。", "选择插入光盘后自动播放的单个视频。", "Choose one video to play automatically when the disc is inserted.")
        if panel.runModal() == .OK, let url = panel.url { video = url.path; error = nil }
    }
    func chooseWorkingDirectory() {
        guard !protectsTermination else { return }
        let panel = NSOpenPanel(); panel.canChooseFiles = false; panel.canChooseDirectories = true; panel.canCreateDirectories = true
        if !workingDirectory.isEmpty { panel.directoryURL = URL(fileURLWithPath: workingDirectory) }
        if panel.runModal() == .OK, let url = panel.url { workingDirectory = url.path }
    }
    func refreshDrives() { Task { await loadDrives() } }
    private func loadDrives() async {
        guard !refreshing && !protectsTermination else { return }
        refreshing = true; defer { refreshing = false }
        do {
            let found: BurnDevices = try await request("/api/burn/devices")
            if found != drives { drives = found }
            if !found.devices.contains(where: { $0.id == deviceID }) { deviceID = found.devices.first?.id ?? "" }
        } catch { self.error = error.localizedDescription }
    }
    func preflight() {
        guard !protectsTermination && !isChecking else { return }
        let input = payload, revision = generation
        isChecking = true; error = nil; check = nil
        Task {
            defer { isChecking = false }
            do {
                let result: DirectBurnCheck = try await request("/api/direct-burn/check", input, timeout: 180)
                guard revision == generation else { return }
                check = result
            } catch { if revision == generation { self.error = error.localizedDescription } }
        }
    }
    func start() { guard canStart else { return }; submit("/api/direct-burn/start", payload) }
    func retry() {
        guard canRetry, let job = direct.job else { return }
        var input: [String: Any] = ["job_id": job.id, "device_id": deviceID, "verify_after_burn": verifyAfterBurn]
        if speed > 0 { input["speed"] = speed }
        submit("/api/direct-burn/retry", input)
    }
    private func submit(_ endpoint: String, _ input: [String: Any]) {
        prepareForBurn(); isStarting = true; awaitingStatus = true; error = nil
        Task {
            defer { isStarting = false }
            do {
                let result: DirectBurnSnapshot = try await request(endpoint, input, timeout: 180)
                direct = result; awaitingStatus = false; check = nil
            } catch { self.error = error.localizedDescription; await poll() }
        }
    }
    func cancel() {
        guard direct.active, direct.job?.burnID == nil, !["burning", "verifying", "finishing", "cleaning"].contains(direct.job?.stage ?? "") else { return }
        Task {
            do { direct = try await request("/api/direct-burn/cancel", [:]) }
            catch { self.error = error.localizedDescription }
        }
    }
    private func poll() async {
        do {
            let result: DirectBurnSnapshot = try await request("/api/direct-burn/status", timeout: 10)
            if direct != result { direct = result }
            if !isStarting { awaitingStatus = false }
            connectionError = nil
        } catch {
            if protectsTermination || owner?.discToolTab == "video" { connectionError = error.localizedDescription }
        }
    }
    func showLog() {
        Task {
            do {
                struct Reply: Decodable { var text: String }
                let reply: Reply = try await request("/api/direct-burn/log")
                owner?.logs = reply.text; owner?.showLogWindow = true
            } catch { self.error = error.localizedDescription }
        }
    }
    func refreshDiscs() { Task { await loadDiscs() } }
    private func loadDiscs() async {
        guard !loadingDisc && !refreshingDiscs else { return }
        refreshingDiscs = true
        defer { refreshingDiscs = false }
        do {
            discs = try await request("/api/discs")
            if !discs.devices.contains(where: { $0.id == readDeviceID }) { readDeviceID = discs.devices.first?.id ?? "" }
            readError = nil
        } catch { readError = error.localizedDescription }
    }
    func openInsertedDisc() { guard !readDeviceID.isEmpty else { return }; openSource(endpoint: "/api/discs/open", payload: ["device_id": readDeviceID]) }
    func chooseDiscSource() {
        let panel = NSOpenPanel(); panel.canChooseFiles = true; panel.canChooseDirectories = true; panel.allowsMultipleSelection = false
        panel.message = tr("ISO、VIDEO_TS、BDMV、またはディスクのルートフォルダを選択します。", "选择 ISO、VIDEO_TS、BDMV 或光盘根文件夹。", "Choose an ISO, VIDEO_TS, BDMV, or the disc root folder.")
        if panel.runModal() == .OK, let url = panel.url { openSource(endpoint: "/api/discs/inspect", payload: ["path": url.path]) }
    }
    private func openSource(endpoint: String, payload: [String: Any]) {
        guard !loadingDisc else { return }
        if owner?.burnProtectsTermination == true {
            readError = tr("書き込みと検証の完了を待ってください。", "请等待烧录和校验完成。", "Wait until burning and verification finish."); return
        }
        readGeneration += 1; let revision = readGeneration
        loadingDisc = true; readError = nil
        Task {
            defer { if revision == readGeneration { loadingDisc = false } }
            do {
                let source: DiscPlaybackSource = try await request(endpoint, payload)
                guard revision == readGeneration, owner?.burnProtectsTermination != true else { return }
                readSource = source; owner?.stopEditorPlaybackForDisc(); player.open(path: source.path, format: source.format)
            } catch { if revision == readGeneration { readError = error.localizedDescription } }
        }
    }
}
