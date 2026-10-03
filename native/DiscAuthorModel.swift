import SwiftUI
import AppKit
import AVFoundation
import UniformTypeIdentifiers
import Darwin

struct LocalFont: Codable, Hashable, Identifiable {
    var path: String = ""
    var index: Int = 0
    var label: String = ""
    var id: String { path + "#" + String(index) }
    init(path: String = "", index: Int = 0, label: String = "") {
        self.path = path; self.index = index; self.label = label
    }
    enum CodingKeys: String, CodingKey { case path, index, label }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        path = try c.decode(String.self, forKey: .path)
        index = try c.decodeIfPresent(Int.self, forKey: .index) ?? 0
        label = try c.decodeIfPresent(String.self, forKey: .label) ?? URL(fileURLWithPath: path).lastPathComponent
    }
}

struct VideoInfo: Codable, Equatable {
    var duration: Double
    var width: Int
    var height: Int
    var fps: String?
    var fieldOrder: String?
    var audio: Bool
    var codec: String?
    var pixFmt: String?
    var colorSpace: String?
    var colorTransfer: String?
    var colorPrimaries: String?
    var colorRange: String?
    var sampleAspectRatio: String?
    var rFrameRate: String?
    enum CodingKeys: String, CodingKey {
        case duration, width, height, fps, audio, codec
        case fieldOrder = "field_order", pixFmt = "pix_fmt", colorSpace = "color_space"
        case colorTransfer = "color_transfer", colorPrimaries = "color_primaries", colorRange = "color_range"
        case sampleAspectRatio = "sample_aspect_ratio", rFrameRate = "r_frame_rate"
    }
}

struct DiscVideo: Codable, Identifiable, Equatable {
    var path: String
    var title: String
    var info: VideoInfo?
    var previewURL: String?
    var id: String { path }
    enum CodingKeys: String, CodingKey { case path, title, info; case previewURL = "preview_url" }
}

struct ButtonFeedback: Codable, Equatable {
    var regionEnabled = true
    var textEnabled = false
    var style = "fill"
    var selectedColor = "#ffff00"
    var activatedColor = "#ff0000"
    var selectedTextColor = "#ffff00"
    var activatedTextColor = "#ff0000"
    var opacity = 0.5
    enum CodingKeys: String, CodingKey {
        case style, opacity
        case regionEnabled = "region_enabled", textEnabled = "text_enabled"
        case selectedColor = "selected_color", activatedColor = "activated_color"
        case selectedTextColor = "selected_text_color", activatedTextColor = "activated_text_color"
    }
    init() {}
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        regionEnabled = try c.decodeIfPresent(Bool.self, forKey: .regionEnabled) ?? true
        textEnabled = try c.decodeIfPresent(Bool.self, forKey: .textEnabled) ?? false
        style = try c.decodeIfPresent(String.self, forKey: .style) ?? "fill"
        selectedColor = try c.decodeIfPresent(String.self, forKey: .selectedColor) ?? "#ffff00"
        activatedColor = try c.decodeIfPresent(String.self, forKey: .activatedColor) ?? "#ff0000"
        selectedTextColor = try c.decodeIfPresent(String.self, forKey: .selectedTextColor) ?? "#ffff00"
        activatedTextColor = try c.decodeIfPresent(String.self, forKey: .activatedTextColor) ?? "#ff0000"
        opacity = try c.decodeIfPresent(Double.self, forKey: .opacity) ?? 0.5
    }
}

struct TextStyle: Codable, Equatable {
    var color = "#ffffff"
    var outlineEnabled = false
    var outlineColor = "#ffffff"
    var outlineWidth = 4
    enum CodingKeys: String, CodingKey {
        case color
        case outlineEnabled = "outline_enabled", outlineColor = "outline_color", outlineWidth = "outline_width"
    }
    init(color: String = "#ffffff", outlineEnabled: Bool = false, outlineColor: String = "#ffffff", outlineWidth: Int = 4) {
        self.color = color; self.outlineEnabled = outlineEnabled
        self.outlineColor = outlineColor; self.outlineWidth = outlineWidth
    }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        color = try c.decodeIfPresent(String.self, forKey: .color) ?? "#ffffff"
        outlineEnabled = try c.decodeIfPresent(Bool.self, forKey: .outlineEnabled) ?? false
        outlineColor = try c.decodeIfPresent(String.self, forKey: .outlineColor) ?? "#ffffff"
        outlineWidth = try c.decodeIfPresent(Int.self, forKey: .outlineWidth) ?? 4
    }
}

enum NoMenuEndAction: String, Codable, CaseIterable {
    case loop, stop
}

struct EncodingSettings: Codable, Equatable {
    var quality = "high"
    var denoise = "off"
    var antiFlicker = "off"
    var sourceScan = "auto"
    enum CodingKeys: String, CodingKey {
        case quality, denoise
        case antiFlicker = "anti_flicker", sourceScan = "source_scan"
    }
    init() {}
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        quality = try c.decodeIfPresent(String.self, forKey: .quality) ?? "high"
        denoise = try c.decodeIfPresent(String.self, forKey: .denoise) ?? "off"
        antiFlicker = try c.decodeIfPresent(String.self, forKey: .antiFlicker) ?? "off"
        sourceScan = try c.decodeIfPresent(String.self, forKey: .sourceScan) ?? "auto"
    }
}

// Non-null template data is deliberately rejected in the public edition.
// Keeping this Codable boundary prevents a private project from losing its
// design silently when opened here.
struct MenuTemplateSettings: Codable, Equatable {
    var id = ""
    init(from decoder: Decoder) throws {
        throw NativeError(message: "公开版不包含私有菜单模板。请新建通用菜单或使用自己的背景图片。")
    }
}

struct DiscProject: Codable, Equatable {
    var title = "メインメニュー"
    var format = "dvd"
    var capacity = "dvd5"
    var standard = "ntsc"
    var bdMode = "auto"
    var encoding = EncodingSettings()
    var perPage = 4
    var layout = "auto"
    var background = ""
    var font = LocalFont()
    var fontSize = 42
    var textStyle = TextStyle()
    var firstButton = 1
    var afterPlay = "menu"
    var rememberSelection = true
    var menuEnabled = true
    var menuTemplate: MenuTemplateSettings?
    var noMenuEndAction: NoMenuEndAction = .stop
    var buttonFeedback = ButtonFeedback()
    var navigation: [String: [Int]] = [:]
    var buttonRects: [[Int]] = []
    var outputDir = ""
    var exportMode = "project_name"
    var exportFormatSuffix = true
    var cleanupWorkOnSuccess = false
    var preventSleep = false
    var menuLanguage = "ja"
    var videos: [DiscVideo] = []
    init() {}
    enum CodingKeys: String, CodingKey {
        case title, format, capacity, standard, layout, background, font, navigation, videos, encoding
        case bdMode = "bd_mode", perPage = "per_page", fontSize = "font_size"
        case textStyle = "text_style"
        case firstButton = "first_button", afterPlay = "after_play", rememberSelection = "remember_selection"
        case menuEnabled = "menu_enabled", noMenuEndAction = "no_menu_end_action"
        case menuTemplate = "menu_template"
        case buttonFeedback = "button_feedback", buttonRects = "button_rects"
        case outputDir = "output_dir", menuLanguage = "menu_language"
        case exportMode = "export_mode", exportFormatSuffix = "export_format_suffix"
        case cleanupWorkOnSuccess = "cleanup_work_on_success", preventSleep = "prevent_sleep"
    }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        title = try c.decodeIfPresent(String.self, forKey: .title) ?? title
        format = try c.decodeIfPresent(String.self, forKey: .format) ?? format
        capacity = try c.decodeIfPresent(String.self, forKey: .capacity) ?? (format == "dvd" ? "dvd5" : "bd25")
        standard = try c.decodeIfPresent(String.self, forKey: .standard) ?? standard
        bdMode = try c.decodeIfPresent(String.self, forKey: .bdMode) ?? bdMode
        encoding = try c.decodeIfPresent(EncodingSettings.self, forKey: .encoding) ?? encoding
        perPage = try c.decodeIfPresent(Int.self, forKey: .perPage) ?? perPage
        layout = try c.decodeIfPresent(String.self, forKey: .layout) ?? layout
        background = try c.decodeIfPresent(String.self, forKey: .background) ?? background
        font = try c.decodeIfPresent(LocalFont.self, forKey: .font) ?? font
        fontSize = try c.decodeIfPresent(Int.self, forKey: .fontSize) ?? fontSize
        textStyle = try c.decodeIfPresent(TextStyle.self, forKey: .textStyle) ?? textStyle
        firstButton = try c.decodeIfPresent(Int.self, forKey: .firstButton) ?? firstButton
        afterPlay = try c.decodeIfPresent(String.self, forKey: .afterPlay) ?? afterPlay
        rememberSelection = try c.decodeIfPresent(Bool.self, forKey: .rememberSelection) ?? rememberSelection
        menuEnabled = try c.decodeIfPresent(Bool.self, forKey: .menuEnabled) ?? true
        menuTemplate = try c.decodeIfPresent(MenuTemplateSettings.self, forKey: .menuTemplate)
        noMenuEndAction = try c.decodeIfPresent(NoMenuEndAction.self, forKey: .noMenuEndAction) ?? .stop
        buttonFeedback = try c.decodeIfPresent(ButtonFeedback.self, forKey: .buttonFeedback) ?? buttonFeedback
        navigation = try c.decodeIfPresent([String: [Int]].self, forKey: .navigation) ?? navigation
        buttonRects = try c.decodeIfPresent([[Int]].self, forKey: .buttonRects) ?? buttonRects
        outputDir = try c.decodeIfPresent(String.self, forKey: .outputDir) ?? outputDir
        // Old saved projects retain their existing per-task folder behavior.
        exportMode = try c.decodeIfPresent(String.self, forKey: .exportMode) ?? "task_folder"
        exportFormatSuffix = try c.decodeIfPresent(Bool.self, forKey: .exportFormatSuffix) ?? true
        cleanupWorkOnSuccess = try c.decodeIfPresent(Bool.self, forKey: .cleanupWorkOnSuccess) ?? false
        preventSleep = try c.decodeIfPresent(Bool.self, forKey: .preventSleep) ?? false
        menuLanguage = try c.decodeIfPresent(String.self, forKey: .menuLanguage) ?? menuLanguage
        videos = try c.decodeIfPresent([DiscVideo].self, forKey: .videos) ?? videos
        if !["auto", "custom"].contains(layout) {
            throw NativeError(message: "公开版只支持自动或自定义菜单布局。请新建通用菜单。")
        }
    }
    // Match iso_export.iso_basename; only collision numbering is decided at export.
    var exportFilenamePreview: String {
        if exportMode != "project_name" { return format == "dvd" ? "DVD_VIDEO.iso" : "BLURAY_VIDEO.iso" }
        let invalid = CharacterSet(charactersIn: "/\\:*?\"<>|")
        let normalized = title.precomposedStringWithCanonicalMapping
        var name = normalized.unicodeScalars.map { scalar -> String in
            let category = scalar.properties.generalCategory
            return invalid.contains(scalar) || category == .control || category == .format ? "_" : String(scalar)
        }.joined().trimmingCharacters(in: CharacterSet(charactersIn: " ."))
        if name.lowercased().hasSuffix(".iso") { name.removeLast(4) }
        while name.hasSuffix(" ") || name.hasSuffix(".") { name.removeLast() }
        var limited = ""
        var bytes = 0
        for scalar in name.unicodeScalars {
            let count = String(scalar).utf8.count
            if bytes + count > 180 { break }
            limited.unicodeScalars.append(scalar)
            bytes += count
        }
        while limited.hasSuffix(" ") || limited.hasSuffix(".") { limited.removeLast() }
        if limited.isEmpty { limited = "Untitled" }
        if let reserved = limited.range(of: "^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])$", options: [.regularExpression, .caseInsensitive]),
           reserved == limited.startIndex..<limited.endIndex {
            limited = "_" + limited
        }
        return limited + (exportFormatSuffix ? (format == "dvd" ? "_DVD" : "_BD") : "") + ".iso"
    }
}

struct MenuButton: Codable, Equatable {
    var label: String
    var rect: [Int]
    var nav: [Int]
    var title: Int?
    var page: Int?
    var feedback: [String: String]
}

struct MenuPreview: Codable {
    var image: String
    var buttons: [MenuButton]
    var page: Int
    var pages: Int
    var rects: [[Int]]
    var feedback: ButtonFeedback?
}

struct ButtonImages {
    var selected: NSImage?
    var activated: NSImage?
}

struct JobStatus: Codable, Equatable {
    var status: String
    var message: String
    var progress: Double
    var error: String?
    var iso: String?
    var folder: String?
    var stageProgress: Double?
    var processedSeconds: Double?
    var totalSeconds: Double?
    var speed: Double?
    enum CodingKeys: String, CodingKey {
        case status, message, progress, error, iso, folder, speed
        case stageProgress = "stage_progress", processedSeconds = "processed_seconds", totalSeconds = "total_seconds"
    }
}

struct QueueItem: Codable, Equatable, Identifiable {
    var id: String
    var title: String
    var format: String
    var capacity: String
    var status: String
    var progress: Double
    var message: String
    var error: String?
    var iso: String?
    var folder: String?
    var createdAt: String
    var config: DiscProject
    var stageProgress: Double?
    var processedSeconds: Double?
    var totalSeconds: Double?
    var speed: Double?
    enum CodingKeys: String, CodingKey {
        case id, title, format, capacity, status, progress, message, error, iso, folder, config, speed
        case createdAt = "created_at", stageProgress = "stage_progress", processedSeconds = "processed_seconds", totalSeconds = "total_seconds"
    }
    var canRetry: Bool { ["failed", "cancelled", "interrupted"].contains(status) }
    var isRunning: Bool { status == "running" }
    var isPaused: Bool { status == "paused" }
    var isActive: Bool { isRunning || isPaused }
    var completedISO: String? {
        guard status == "complete", let iso, !iso.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return nil }
        return iso
    }
    var jobStatus: JobStatus {
        JobStatus(status: status, message: message, progress: progress, error: error, iso: iso, folder: folder,
                  stageProgress: stageProgress, processedSeconds: processedSeconds, totalSeconds: totalSeconds, speed: speed)
    }
}

struct QueueSnapshot: Codable, Equatable {
    var running = false
    var items: [QueueItem] = []
    var error: String?
}

// Encoding ticks update only queue/progress views, not the editor or its font picker.
@MainActor final class DiscQueueState: ObservableObject {
    @Published private(set) var snapshot = QueueSnapshot()
    var items: [QueueItem] { snapshot.items }
    var running: Bool { snapshot.running }
    var active: QueueItem? { items.first(where: \.isActive) }
    var pendingCount: Int { items.filter { $0.status == "pending" }.count }
    var completeCount: Int { items.filter { $0.status == "complete" }.count }
    var displayItem: QueueItem? { active ?? items.last(where: { $0.status != "pending" }) ?? items.first }
    func apply(_ snapshot: QueueSnapshot) {
        guard snapshot != self.snapshot else { return }
        self.snapshot = snapshot
    }
}

struct BurnMedia: Codable, Equatable {
    var present: Bool
    var busy: Bool
    var type: String
    var format: String
    var blank: Bool
    var writable: Bool
    var freeBytes: Int64?
    var capacityBytes: Int64?
    var speeds: [Double]
    var state: String? = nil
    var isProbing: Bool { state == "probing" || (state == nil && !present && busy) }
    var isDetecting: Bool { isProbing || state == "unknown" }
    var isReady: Bool { present && !busy && (state == nil || state == "ready") }
    enum CodingKeys: String, CodingKey {
        case present, busy, type, format, blank, writable, speeds, state
        case freeBytes = "free_bytes", capacityBytes = "capacity_bytes"
    }
}

struct BurnDevice: Codable, Equatable, Identifiable {
    var id: String
    var name: String
    var vendor: String
    var product: String
    var bus: String
    var location: String
    var canWriteDVD: Bool
    var canWriteBD: Bool
    var media: BurnMedia
    enum CodingKeys: String, CodingKey {
        case id, name, vendor, product, bus, location, media
        case canWriteDVD = "can_write_dvd", canWriteBD = "can_write_bd"
    }
}

struct BurnDevices: Codable, Equatable {
    var devices: [BurnDevice] = []
    var error: String?
}

struct BurnImage: Codable, Equatable {
    var path: String
    var title: String
    var format: String
    var sizeBytes: Int64
    var requiredBytes: Int64
    enum CodingKeys: String, CodingKey {
        case path, title, format
        case sizeBytes = "size_bytes", requiredBytes = "required_bytes"
    }
}

struct BurnCheck: Codable, Equatable {
    var valid: Bool
    var errors: [String]
    var image: BurnImage?
    var device: BurnDevice?
}

struct BurnJob: Codable, Equatable, Identifiable {
    var id: String
    var status: String
    var stage: String
    var progress: Double?
    var stageProgress: Double?
    var message: String
    var error: String?
    var iso: String
    var deviceID: String
    var deviceName: String
    var format: String
    var verified: Bool
    var verificationRequired: Bool?
    var verificationSkipped: Bool?
    var logPath: String
    var startedAt: String
    enum CodingKeys: String, CodingKey {
        case id, status, stage, progress, message, error, iso, format, verified
        case stageProgress = "stage_progress", deviceID = "device_id", deviceName = "device_name", logPath = "log_path", startedAt = "started_at"
        case verificationRequired = "verify_after_burn", verificationSkipped = "verification_skipped"
    }
    var verificationWasSkipped: Bool {
        status == "complete" && !verified && (verificationSkipped == true || verificationRequired == false)
    }
}

struct BurnSnapshot: Codable, Equatable {
    var active = false
    var job: BurnJob?
}

@MainActor final class DiscBurnState: ObservableObject {
    @Published private(set) var snapshot = BurnSnapshot()
    @Published private(set) var detected = BurnDevices()
    @Published var selectedDeviceID = ""
    @Published var image: BurnImage?
    @Published private(set) var requestedISOPath: String?
    @Published private(set) var sourceExportTitle: String?
    @Published var check: BurnCheck?
    @Published var speed: Double = 0
    @Published var verifyAfterBurn = true
    @Published var error: String?
    @Published var connectionError: String?
    @Published var isInspecting = false
    @Published var isRefreshing = false
    @Published var isChecking = false
    @Published var isStarting = false
    @Published var awaitingStartStatus = false
    var device: BurnDevice? { detected.devices.first(where: { $0.id == selectedDeviceID }) }
    var protectsTermination: Bool { snapshot.active || isStarting || awaitingStartStatus }
    var canStart: Bool {
        guard !protectsTermination && !isInspecting && !isChecking && !isRefreshing,
              let image, let device, let check, check.valid,
              check.image?.path == image.path, check.device?.id == device.id,
              check.device?.media == device.media else { return false }
        let media = device.media
        let formatSupported = image.format == "dvd" ? device.canWriteDVD : image.format == "bluray" && device.canWriteBD
        return formatSupported && media.isReady && media.blank && media.writable &&
            media.format == image.format && (media.freeBytes ?? 0) >= image.requiredBytes
    }
    func apply(_ snapshot: BurnSnapshot) {
        if self.snapshot != snapshot { self.snapshot = snapshot }
    }
    func selectImage(_ path: String, exportTitle: String?) {
        requestedISOPath = path
        sourceExportTitle = exportTitle
    }
    @discardableResult func applyDevices(_ devices: BurnDevices) -> Bool {
        guard devices != detected else { return false }
        detected = devices
        if !devices.devices.contains(where: { $0.id == selectedDeviceID }) {
            selectedDeviceID = devices.devices.first?.id ?? ""
            speed = 0
        }
        return true
    }
    func applyCheckedDevice(_ device: BurnDevice?) {
        guard let device, device.id == selectedDeviceID,
              let index = detected.devices.firstIndex(where: { $0.id == device.id }),
              detected.devices[index] != device else { return }
        var updated = detected
        updated.devices[index] = device
        detected = updated
    }
}

private struct BurnRequest: Encodable {
    var iso: String
    var deviceID: String
    var speed: Double?
    var verifyAfterBurn: Bool
    enum CodingKeys: String, CodingKey {
        case iso, speed
        case deviceID = "device_id", verifyAfterBurn = "verify_after_burn"
    }
}

struct EncodingWarning: Codable {
    var code: String
    var videoIndex: Int?
    var source: String?
    var target: String?
    enum CodingKeys: String, CodingKey { case code, source, target; case videoIndex = "video_index" }
}

struct PreflightResult: Codable {
    var valid: Bool
    var videos: Int
    var pages: Int
    var duration: Double
    var videoBitrateKbps: Int
    var requiredGB: Double
    var availableGB: Double
    var encodingWarnings: [EncodingWarning]?
    var outputMode: String?
    enum CodingKeys: String, CodingKey {
        case valid, videos, pages, duration
        case videoBitrateKbps = "video_bitrate_kbps", requiredGB = "required_gb", availableGB = "available_gb"
        case encodingWarnings = "encoding_warnings", outputMode = "output_mode"
    }
}

private struct FontsReply: Decodable { var fonts: [LocalFont] }
private struct VideosReply: Decodable { var videos: [DiscVideo] }
private struct ProjectsReply: Decodable { var files: [String] }
private struct SaveReply: Decodable { var path: String; var name: String }
private struct QueueSubmission: Encodable { var projects: [DiscProject] }
private struct LogReply: Decodable { var text: String }
private struct HealthReply: Decodable { var application: String; var version: String }
private struct APIErrorReply: Decodable { var error: String }
private struct EmptyReply: Decodable { var ok: Bool? }
private struct NativeError: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}

@MainActor
final class DiscAuthorModel: ObservableObject {
    @Published var project = DiscProject() { didSet { projectChanged(previous: oldValue) } }
    @Published var language: String { didSet {
        preferences.set(language, forKey: "DiscAuthor.language")
        if isReady {
            Task { try? await pollOnce(); try? await pollBurnStatus() }
            if showBurn { refreshBurnDevices(); checkBurnSelection() }
        }
    } }
    @Published var mode: String { didSet {
        preferences.set(mode, forKey: "DiscAuthor.mode")
        if mode == "quick" && oldValue != mode { stopPlayback(); cancelActivation() }
    } }
    @Published var step = "source"
    @Published var fonts: [LocalFont] = []
    @Published var fontSearch = ""
    @Published var preview: MenuPreview?
    @Published var baseImage: NSImage?
    @Published var buttonImages: [Int: ButtonImages] = [:]
    @Published var selectedIndex = 0
    @Published var currentPage = 0
    @Published var previewState = "selected"
    @Published var isActivated = false
    @Published var player: AVPlayer?
    @Published var playingIndex: Int?
    @Published var simulationStopped = false
    let queue = DiscQueueState()
    let burn = DiscBurnState()
    let discTools = DiscToolsState()
    @Published var discToolTab = "iso"
    var job: JobStatus? { queue.displayItem?.jobStatus }
    @Published var showQueue = false
    @Published var showBurn = false
    @Published private(set) var queueIsBusy = false
    @Published var preflight: PreflightResult?
    @Published var message = ""
    @Published var errorMessage: String?
    @Published var isBusy = false
    @Published var isReady = false
    @Published var projects: [String] = []
    @Published var logs = ""
    @Published var showLogWindow = false
    @Published var hasUnsavedChanges = false

    private let preferences: UserDefaults
    private var translations: [String: [String: String]] = [:]
    private var baseURL: URL?
    private let token = UUID().uuidString + UUID().uuidString
    private var backend: Process?
    private var backendLog: FileHandle?
    private var starting = false
    private var stopped = false
    private var applyingProject = false
    private var operationCount = 0
    private var previewGeneration = 0
    private var interactionGeneration = 0
    private var previewTask: Task<Void, Never>?
    private var activationTask: Task<Void, Never>?
    private var pollTask: Task<Void, Never>?
    private var burnPollTask: Task<Void, Never>?
    private var burnCheckTask: Task<Void, Never>?
    private var burnGeneration = 0
    private var burnImageGeneration = 0
    private var burnLastCheckAttempt: Date?
    private var playerObserver: NSObjectProtocol?
    private var playerStatusObserver: NSKeyValueObservation?
    private var remembered: [Int: Int] = [:]
    private let session = URLSession(configuration: .ephemeral)

    init() {
        let preferenceSuite = ProcessInfo.processInfo.environment["DISCAUTHOR_PREFERENCES_SUITE"] ?? "jp.discauthor.community"
        // Foundation returns nil when suiteName is this app's own bundle ID.
        // The standard domain is already isolated by the Community bundle ID.
        let preferences: UserDefaults
        if preferenceSuite == Bundle.main.bundleIdentifier {
            preferences = .standard
        } else {
            preferences = UserDefaults(suiteName: preferenceSuite)
                ?? UserDefaults(suiteName: "jp.discauthor.community.preferences")
                ?? .standard
        }
        self.preferences = preferences
        let savedLanguage = preferences.string(forKey: "DiscAuthor.language") ?? "ja"
        language = ["ja", "zh", "en"].contains(savedLanguage) ? savedLanguage : "ja"
        let savedMode = preferences.string(forKey: "DiscAuthor.mode") ?? "quick"
        mode = savedMode == "professional" ? "professional" : "quick"
        let candidates = [Bundle.main.resourceURL?.appendingPathComponent("web/translations.json"),
                          Bundle.main.resourceURL?.appendingPathComponent("translations.json"),
                          ProcessInfo.processInfo.environment["DISCAUTHOR_TRANSLATIONS"].map { URL(fileURLWithPath: $0) }]
        for candidate in candidates.compactMap({ $0 }) {
            if let data = try? Data(contentsOf: candidate),
               let catalog = try? JSONDecoder().decode([String: [String: String]].self, from: data) {
                translations = catalog; break
            }
        }
    }

    var buildIsRunning: Bool { queue.active != nil }
    var burnProtectsTermination: Bool { burn.protectsTermination || discTools.protectsTermination }
    var totalDuration: Double { project.videos.reduce(0) { $0 + ($1.info?.duration ?? 0) } }
    var filteredFonts: [LocalFont] { fontSearch.isEmpty ? fonts : fonts.filter { $0.label.localizedCaseInsensitiveContains(fontSearch) } }
    var selectedButton: MenuButton? {
        guard let buttons = preview?.buttons, buttons.indices.contains(selectedIndex) else { return nil }
        return buttons[selectedIndex]
    }
    var fontID: String {
        get { project.font.id }
        set { selectFont(id: newValue) }
    }

    func t(_ key: String, _ params: [String: String] = [:]) -> String {
        var text = translations[key]?[language] ?? translations[key]?["ja"] ?? key
        for (name, value) in params { text = text.replacingOccurrences(of: "{" + name + "}", with: value) }
        return text
    }

    private func nativeText(_ ja: String, _ zh: String, _ en: String) -> String {
        language == "zh" ? zh : language == "en" ? en : ja
    }

    func start() async {
        guard !starting && !isReady else { return }
        starting = true; stopped = false; beginOperation()
        defer { starting = false; endOperation() }
        do {
            try launchBackend()
            try await waitForBackend()
            async let fontReply: FontsReply = request("/api/fonts")
            async let preset: DiscProject = request("/api/preset")
            fonts = try await fontReply.fonts
            var initial = try await preset
            if let match = fonts.first(where: { $0.id == initial.font.id }) { initial.font = match }
            else if initial.font.path.isEmpty, let fallback = fonts.first { initial.font = fallback }
            applyingProject = true; project = initial; applyingProject = false
            isReady = true
            try await reloadProjects()
            try await pollOnce()
            startPolling()
            startBurnPolling()
            discTools.attach(to: self)
            try await refreshNow(resetSelection: true)
            message = nativeText("制作準備ができました", "制作环境已就绪", "Ready to author")
        } catch { report(error) }
    }

    private func launchBackend() throws {
        let environment = ProcessInfo.processInfo.environment
        let port = try availablePort()
        baseURL = URL(string: "http://127.0.0.1:\(port)")
        let process = Process()
        var env = environment
        env["DISCAUTHOR_NATIVE_TOKEN"] = token
        env["DISCAUTHOR_PORT"] = String(port)
        env["DISCAUTHOR_NO_BROWSER"] = "1"
        env["NO_BROWSER"] = "1"
        env["DISCAUTHOR_PARENT_PID"] = String(ProcessInfo.processInfo.processIdentifier)
        let data = environment["DISCAUTHOR_DATA_DIR"].map { URL(fileURLWithPath: $0) }
            ?? FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/DiscAuthor Community")
        env["DISCAUTHOR_DATA_DIR"] = data.path
        if let script = environment["DISCAUTHOR_BACKEND_SCRIPT"] {
            let scriptURL = URL(fileURLWithPath: script)
            let python = environment["DISCAUTHOR_PYTHON"] ?? scriptURL.deletingLastPathComponent().appendingPathComponent(".venv/bin/python").path
            process.executableURL = URL(fileURLWithPath: python)
            process.arguments = [script]
            if scriptURL.lastPathComponent == "server.py" { process.arguments! += ["--port", String(port)] }
            process.currentDirectoryURL = scriptURL.deletingLastPathComponent()
        } else if let helper = Bundle.main.url(forAuxiliaryExecutable: "DiscAuthorBackend") {
            process.executableURL = helper
        } else {
            throw NativeError(message: nativeText("制作エンジンが見つかりません。アプリを再インストールしてください。", "找不到制作引擎，请重新安装应用。", "Authoring engine is missing. Reinstall the app."))
        }
        try FileManager.default.createDirectory(at: data, withIntermediateDirectories: true)
        let logURL = data.appendingPathComponent("native-backend.log")
        if !FileManager.default.fileExists(atPath: logURL.path) { FileManager.default.createFile(atPath: logURL.path, contents: nil) }
        let handle = try FileHandle(forWritingTo: logURL)
        try handle.seekToEnd()
        backendLog = handle
        process.standardOutput = handle; process.standardError = handle; process.environment = env
        try process.run()
        backend = process
    }

    private func availablePort() throws -> UInt16 {
        let descriptor = socket(AF_INET, SOCK_STREAM, 0)
        guard descriptor >= 0 else { throw NativeError(message: "Local socket creation failed (\(errno))") }
        defer { Darwin.close(descriptor) }
        var address = sockaddr_in()
        address.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
        address.sin_family = sa_family_t(AF_INET)
        address.sin_port = 0
        address.sin_addr = in_addr(s_addr: inet_addr("127.0.0.1"))
        let bound = withUnsafePointer(to: &address) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { Darwin.bind(descriptor, $0, socklen_t(MemoryLayout<sockaddr_in>.size)) }
        }
        guard bound == 0 else { throw NativeError(message: "Local socket binding failed (\(errno))") }
        var size = socklen_t(MemoryLayout<sockaddr_in>.size)
        let found = withUnsafeMutablePointer(to: &address) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { getsockname(descriptor, $0, &size) }
        }
        guard found == 0 else { throw NativeError(message: "Local port lookup failed (\(errno))") }
        return UInt16(bigEndian: address.sin_port)
    }

    private func waitForBackend() async throws {
        for _ in 0..<200 {
            try Task.checkCancellation()
            guard backend?.isRunning == true else {
                throw NativeError(message: nativeText("制作エンジンを起動できませんでした。native-backend.log を確認してください。", "制作引擎启动失败，请查看 native-backend.log。", "The authoring engine failed to start. Check native-backend.log."))
            }
            if let reply: HealthReply = try? await request("/health", timeout: 1), reply.application == "DiscAuthor Community" {
                return
            }
            try await Task.sleep(nanoseconds: 200_000_000)
        }
        throw NativeError(message: nativeText("制作エンジンの起動がタイムアウトしました。", "制作引擎启动超时。", "Authoring engine startup timed out."))
    }

    private func request<T: Decodable>(_ path: String, body: Data? = nil, method: String? = nil, timeout: TimeInterval = 180) async throws -> T {
        guard let baseURL, let url = URL(string: path, relativeTo: baseURL) else {
            throw NativeError(message: nativeText("制作エンジンの準備中です", "制作引擎尚未就绪", "Authoring engine is not ready"))
        }
        var req = URLRequest(url: url, timeoutInterval: timeout)
        req.httpMethod = method ?? (body == nil ? "GET" : "POST")
        req.httpBody = body
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.setValue(token, forHTTPHeaderField: "X-DiscAuthor-Token")
        req.setValue(language, forHTTPHeaderField: "X-DiscAuthor-Language")
        let (data, response) = try await session.data(for: req)
        guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            let detail = (try? JSONDecoder().decode(APIErrorReply.self, from: data))?.error
            throw NativeError(message: detail ?? nativeText("制作エンジンとの通信に失敗しました", "与制作引擎通信失败", "Authoring engine request failed"))
        }
        return try JSONDecoder().decode(T.self, from: data)
    }

    private func projectData(page: Int? = nil) throws -> Data {
        let encoded = try JSONEncoder().encode(project)
        guard let page else { return encoded }
        var object = try JSONSerialization.jsonObject(with: encoded) as! [String: Any]
        object["page"] = page
        return try JSONSerialization.data(withJSONObject: object)
    }

    private func beginOperation() { operationCount += 1; isBusy = true; errorMessage = nil }
    private func endOperation() { operationCount = max(0, operationCount-1); isBusy = operationCount > 0 }
    private func report(_ error: Error) {
        if error is CancellationError || stopped { return }
        if let network = error as? URLError, network.code == .cancelled { return }
        errorMessage = t(error.localizedDescription)
    }
    private func perform(_ action: @escaping @MainActor () async throws -> Void) {
        beginOperation()
        Task {
            defer { endOperation() }
            do { try await action() } catch { report(error) }
        }
    }

    private func projectChanged(previous: DiscProject) {
        guard isReady && !applyingProject && project != previous else { return }
        hasUnsavedChanges = true; preflight = nil
        // Encoding and output settings do not change the rendered menu.
        var unchangedMenuProject = project
        unchangedMenuProject.encoding = previous.encoding
        unchangedMenuProject.bdMode = previous.bdMode
        unchangedMenuProject.outputDir = previous.outputDir
        unchangedMenuProject.exportMode = previous.exportMode
        unchangedMenuProject.exportFormatSuffix = previous.exportFormatSuffix
        unchangedMenuProject.cleanupWorkOnSuccess = previous.cleanupWorkOnSuccess
        unchangedMenuProject.preventSleep = previous.preventSleep
        if unchangedMenuProject == previous { return }
        var corrected = project
        if project.format != previous.format { corrected.capacity = project.format == "dvd" ? "dvd5" : "bd25" }
        if project.perPage != previous.perPage {
            corrected.perPage = min(12, max(1, project.perPage))
            corrected.layout = "auto"; corrected.navigation = [:]; corrected.buttonRects = []
            corrected.firstButton = min(max(1, corrected.firstButton), corrected.perPage)
            currentPage = 0; remembered = [:]
        }
        if corrected != project { applyingProject = true; project = corrected; applyingProject = false }
        if project.menuEnabled != previous.menuEnabled {
            stopPlayback(); cancelActivation(); simulationStopped = false
            previewTask?.cancel(); previewGeneration += 1
            preview = nil; baseImage = nil; buttonImages = [:]
            currentPage = 0; selectedIndex = 0
            if !project.menuEnabled && step == "menu" { setStep("simulation") }
        }
        let count = max(1, Int(ceil(Double(max(1, project.videos.count)) / Double(max(1, project.perPage)))))
        if currentPage >= count { currentPage = count-1 }
        if project.menuEnabled { schedulePreview() }
    }

    private func schedulePreview() {
        previewTask?.cancel()
        previewTask = Task {
            do {
                try await Task.sleep(nanoseconds: 280_000_000)
                try await refreshNow(resetSelection: false)
            } catch { report(error) }
        }
    }

    func refreshPreview() {
        guard isReady else { return }
        previewTask?.cancel()
        previewTask = Task {
            do { try await refreshNow(resetSelection: false) } catch { report(error) }
        }
    }

    private func image(at path: String) async throws -> NSImage {
        guard let baseURL, let url = URL(string: path, relativeTo: baseURL) else { throw URLError(.badURL) }
        let (data, response) = try await session.data(from: url)
        guard let http = response as? HTTPURLResponse, http.statusCode == 200, let image = NSImage(data: data) else {
            throw NativeError(message: nativeText("メニュー画像を読み込めません", "无法读取菜单图像", "Cannot load the menu image"))
        }
        return image
    }

    private func initialSelection(in next: MenuPreview) -> Int {
        let programmes = next.buttons.filter { $0.title != nil }.count
        return project.firstButton <= programmes ? max(0, project.firstButton-1) : 0
    }

    private func refreshNow(resetSelection: Bool, forcedSelection: Int? = nil) async throws {
        guard isReady else { return }
        previewGeneration += 1; let generation = previewGeneration
        guard project.menuEnabled else {
            preview = nil; baseImage = nil; buttonImages = [:]; selectedIndex = 0; currentPage = 0
            return
        }
        let next: MenuPreview = try await request("/api/preview", body: projectData(page: currentPage))
        let background = try await image(at: next.image)
        var layers: [Int: ButtonImages] = [:]
        for (i, button) in next.buttons.enumerated() {
            try Task.checkCancellation()
            var layer = ButtonImages()
            if let path = button.feedback["selected"] { layer.selected = try await image(at: path) }
            if let path = button.feedback["activated"] { layer.activated = try await image(at: path) }
            layers[i] = layer
        }
        guard generation == previewGeneration && !Task.isCancelled else { return }
        preview = next; baseImage = background; buttonImages = layers
        if let forcedSelection { selectedIndex = min(max(0, forcedSelection), max(0, next.buttons.count-1)) }
        else if resetSelection || !next.buttons.indices.contains(selectedIndex) { selectedIndex = initialSelection(in: next) }
    }

    func setMode(_ value: String) { mode = value == "professional" ? "professional" : "quick" }
    func setStep(_ value: String) {
        guard ["source", "menu", "simulation", "output"].contains(value) else { return }
        let destination = value == "menu" && !project.menuEnabled ? "simulation" : value
        if destination != "simulation" { stopPlayback() }
        step = destination
        if destination == "simulation" {
            previewState = "selected"
            if !project.menuEnabled && playingIndex == nil { startNoMenuSimulation() }
        }
    }
    func setPage(_ page: Int) {
        guard project.menuEnabled else { return }
        cancelActivation(); stopPlayback()
        previewTask?.cancel()
        currentPage = max(0, min(page, max(0, (preview?.pages ?? 1)-1)))
        perform { try await self.refreshNow(resetSelection: true) }
    }
    func selectFont(id: String) {
        guard project.font.id != id else { return }
        if let choice = fonts.first(where: { $0.id == id }) { project.font = choice }
    }
    func applyBlueWhiteTextPreset() {
        var next = project
        next.textStyle = TextStyle(color: "#2c95cc", outlineEnabled: true, outlineColor: "#ffffff", outlineWidth: 4)
        next.buttonFeedback.regionEnabled = false
        next.buttonFeedback.textEnabled = true
        next.buttonFeedback.selectedTextColor = "#000000"
        next.buttonFeedback.activatedTextColor = "#e24b35"
        project = next
    }
    func rescanFonts() {
        perform { let reply: FontsReply = try await self.request("/api/fonts?refresh=1"); self.fonts = reply.fonts }
    }
    func chooseVideos() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = true; panel.canChooseDirectories = false; panel.allowsMultipleSelection = true
        panel.allowedContentTypes = ["mp4","mov","mkv","m4v","avi","mts","m2ts","mpg","mpeg","vob","webm","mxf"].compactMap { UTType(filenameExtension: $0) }
        panel.message = nativeText("作品にする動画を選択してください", "选择要制作的节目视频", "Choose programme videos")
        guard panel.runModal() == .OK else { return }
        addVideos(paths: panel.urls.map(\.path))
    }
    func addVideos(paths: [String]) {
        let existing = Set(project.videos.map(\.path))
        let additions = paths.filter { !existing.contains($0) }
        guard !additions.isEmpty else { return }
        guard additions.count + project.videos.count <= 30 else { errorMessage = t("最多添加 30 个文件"); return }
        perform {
            let data = try JSONSerialization.data(withJSONObject: ["paths": additions])
            let reply: VideosReply = try await self.request("/api/probe", body: data)
            self.project.videos += reply.videos
            self.currentPage = 0
            try await self.refreshNow(resetSelection: true)
        }
    }
    func chooseBackground() {
        let panel = NSOpenPanel(); panel.canChooseFiles = true; panel.canChooseDirectories = false
        panel.allowedContentTypes = [.png, .jpeg, .tiff, .webP]
        guard panel.runModal() == .OK, let url = panel.url else { return }
        project.background = url.path
    }
    func clearBackground() { project.background = "" }
    func chooseOutput() {
        let panel = NSOpenPanel(); panel.canChooseFiles = false; panel.canChooseDirectories = true; panel.canCreateDirectories = true
        if !project.outputDir.isEmpty { panel.directoryURL = URL(fileURLWithPath: project.outputDir) }
        guard panel.runModal() == .OK, let url = panel.url else { return }
        project.outputDir = url.path
    }
    func removeVideo(at index: Int) {
        guard project.videos.indices.contains(index) else { return }
        stopPlayback(); project.videos.remove(at: index); remembered = [:]
    }
    func moveVideo(from source: Int, to destination: Int) {
        guard project.videos.indices.contains(source), project.videos.indices.contains(destination), source != destination else { return }
        stopPlayback()
        let item = project.videos.remove(at: source); project.videos.insert(item, at: destination); remembered = [:]
    }
    func updateButtonRect(index: Int, rect: [Int]) {
        guard project.menuTemplate == nil, index >= 0 && index < project.perPage && rect.count == 4 else { return }
        var rectangles = project.layout == "custom" ? project.buttonRects : (preview?.rects ?? [])
        guard rectangles.indices.contains(index) else { return }
        let width = min(1920, max(80, rect[2])), height = min(1080, max(40, rect[3]))
        rectangles[index] = [min(max(0, rect[0]), 1920-width), min(max(0, rect[1]), 1080-height), width, height]
        var next = project; next.buttonRects = rectangles; next.layout = "custom"; project = next
    }
    func setDirection(index: Int, direction: Int, target: Int) {
        guard let preview, preview.buttons.indices.contains(index), (0..<4).contains(direction), (1...preview.buttons.count).contains(target) else { return }
        var targets = project.navigation[String(index+1)] ?? preview.buttons[index].nav
        guard targets.count == 4 else { return }
        targets[direction] = target; project.navigation[String(index+1)] = targets
    }
    func resetNavigation() { project.navigation = [:] }
    func selectButton(_ index: Int) {
        guard project.menuEnabled, !isActivated, preview?.buttons.indices.contains(index) == true else { return }
        selectedIndex = index
    }
    func navigate(direction: Int) {
        guard project.menuEnabled, !isActivated, playingIndex == nil, (0..<4).contains(direction), let button = selectedButton,
              button.nav.indices.contains(direction), let preview else { return }
        let next = button.nav[direction]-1
        if preview.buttons.indices.contains(next) { selectedIndex = next; previewState = "selected" }
    }
    func activateSelected() {
        guard project.menuEnabled, !isActivated, playingIndex == nil, let button = selectedButton else { return }
        isActivated = true; interactionGeneration += 1; let generation = interactionGeneration
        activationTask = Task {
            do {
                try await Task.sleep(nanoseconds: 450_000_000)
                guard generation == interactionGeneration else { return }
                isActivated = false
                if let title = button.title { playVideo(at: title-1) }
                else if let page = button.page {
                    previewTask?.cancel()
                    currentPage = page; try await refreshNow(resetSelection: true)
                }
            } catch { if generation == interactionGeneration { isActivated = false }; report(error) }
        }
    }
    private func cancelActivation() { interactionGeneration += 1; activationTask?.cancel(); isActivated = false }
    func playVideo(at index: Int) {
        guard project.videos.indices.contains(index) else { return }
        stopPlayback(); cancelActivation()
        let video = project.videos[index]
        guard FileManager.default.fileExists(atPath: video.path) else { errorMessage = t("找不到视频：{path}", ["path":video.path]); return }
        let item = AVPlayerItem(url: URL(fileURLWithPath: video.path))
        let playback = AVPlayer(playerItem: item)
        player = playback; playingIndex = index; simulationStopped = false
        remembered[index / max(1, project.perPage)] = index % max(1, project.perPage)
        playerObserver = NotificationCenter.default.addObserver(forName: .AVPlayerItemDidPlayToEndTime, object: item, queue: .main) { [weak self] _ in
            Task { @MainActor in
                guard let self, self.player?.currentItem === item else { return }
                self.videoFinished()
            }
        }
        playerStatusObserver = item.observe(\.status, options: [.new]) { [weak self] item, _ in
            guard item.status == .failed else { return }
            let detail = item.error?.localizedDescription ?? ""
            Task { @MainActor in
                guard let self, self.player?.currentItem === item else { return }
                self.errorMessage = self.nativeText("この動画は macOS のプレーヤーで再生できません。ISO 制作は別途チェックできます。", "macOS 原生播放器无法试播此视频，可继续检查是否能制作 ISO。", "The macOS player cannot preview this video. You can still check whether it can be authored to ISO.") + " " + detail
                self.returnToMenu()
            }
        }
        playback.play()
    }
    private func videoFinished() {
        guard let index = playingIndex else { return }
        if !project.menuEnabled {
            stopSimulation()
            return
        }
        if project.afterPlay == "loop" && !project.videos.isEmpty { playVideo(at: (index+1) % project.videos.count) }
        else if project.afterPlay == "next" && project.videos.indices.contains(index+1) { playVideo(at: index+1) }
        else { returnToMenu() }
    }
    private func stopPlayback() {
        player?.pause(); player?.replaceCurrentItem(with: nil)
        player = nil; playingIndex = nil
        if let observer = playerObserver { NotificationCenter.default.removeObserver(observer) }
        playerObserver = nil; playerStatusObserver = nil
    }
    func startNoMenuSimulation() {
        guard !project.menuEnabled else { return }
        guard !project.videos.isEmpty else { simulationStopped = false; return }
        guard project.videos.count == 1, project.noMenuEndAction == .stop else {
            errorMessage = t(project.videos.count != 1 ? "无菜单模式只支持一个视频，请改为有菜单或保留一个视频" : "无菜单模式统一播放结束后停止，请修改旧工程的循环设置")
            return
        }
        playVideo(at: 0)
    }
    func stopSimulation() {
        stopPlayback(); cancelActivation(); simulationStopped = true
    }
    func returnToMenu() {
        guard project.menuEnabled else { stopSimulation(); return }
        let last = playingIndex
        stopPlayback(); cancelActivation()
        previewTask?.cancel()
        if let last { currentPage = last / max(1, project.perPage) }
        let selection = project.rememberSelection ? remembered[currentPage] : nil
        if let selection { selectedIndex = selection }
        perform { try await self.refreshNow(resetSelection: selection == nil, forcedSelection: selection) }
    }
    func topMenu() {
        guard project.menuEnabled else { return }
        stopPlayback(); cancelActivation(); currentPage = 0
        previewTask?.cancel()
        let selection = project.rememberSelection ? remembered[0] : nil
        perform { try await self.refreshNow(resetSelection: selection == nil, forcedSelection: selection) }
    }

    func newProject() {
        if hasUnsavedChanges {
            let alert = NSAlert()
            alert.messageText = nativeText("現在のプロジェクトを保存しますか？", "保存当前项目吗？", "Save the current project?")
            alert.addButton(withTitle: nativeText("保存して新規作成", "保存并新建", "Save and create"))
            alert.addButton(withTitle: nativeText("保存せずに新規作成", "不保存并新建", "Create without saving"))
            alert.addButton(withTitle: nativeText("キャンセル", "取消", "Cancel"))
            let response = alert.runModal()
            if response == .alertThirdButtonReturn { return }
            perform {
                if response == .alertFirstButtonReturn { try await self.saveNow() }
                let fresh: DiscProject = try await self.request("/api/preset")
                try await self.apply(fresh); self.step = "source"
            }
        } else {
            perform { let fresh: DiscProject = try await self.request("/api/preset"); try await self.apply(fresh); self.step = "source" }
        }
    }
    private func apply(_ incoming: DiscProject) async throws {
        var incoming = incoming
        if !incoming.videos.isEmpty {
            let data = try JSONSerialization.data(withJSONObject: ["paths": incoming.videos.map(\.path)])
            let reply: VideosReply = try await request("/api/probe", body: data)
            incoming.videos = zip(reply.videos, incoming.videos).map { scanned, saved in
                var item = scanned; item.title = saved.title; return item
            }
        }
        if let match = fonts.first(where: { $0.id == incoming.font.id }) { incoming.font = match }
        else if incoming.font.path.isEmpty, let fallback = fonts.first { incoming.font = fallback }
        stopPlayback(); cancelActivation(); remembered = [:]; currentPage = 0; simulationStopped = false
        applyingProject = true; project = incoming; applyingProject = false
        if !incoming.menuEnabled && step == "menu" { step = "source" }
        preflight = nil; hasUnsavedChanges = false
        try await refreshNow(resetSelection: true)
    }
    func saveProject() { perform { try await self.saveNow() } }
    func saveForTermination() async -> Bool {
        beginOperation(); defer { endOperation() }
        do { try await saveNow(); return !hasUnsavedChanges }
        catch { report(error); return false }
    }
    private func saveNow() async throws {
        let savedProject = project
        let reply: SaveReply = try await request("/api/save", body: projectData())
        hasUnsavedChanges = project != savedProject
        message = t("项目已保存：") + reply.path
        try await reloadProjects()
    }
    private func reloadProjects() async throws { let reply: ProjectsReply = try await request("/api/projects"); projects = reply.files }
    func loadProject(_ name: String) {
        perform {
            let encoded = name.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? name
            let incoming: DiscProject = try await self.request("/api/projects/" + encoded)
            try await self.apply(incoming)
        }
    }
    func loadProject(name: String) { loadProject(name) }
    func chooseProject() {
        let panel = NSOpenPanel(); panel.canChooseFiles = true; panel.canChooseDirectories = false; panel.allowedContentTypes = [.json]
        panel.directoryURL = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/DiscAuthor Community/projects")
        guard panel.runModal() == .OK, let url = panel.url else { return }
        perform {
            let data = try Data(contentsOf: url)
            let incoming = try JSONDecoder().decode(DiscProject.self, from: data)
            try await self.apply(incoming)
        }
    }
    func checkProject() {
        perform {
            self.preflight = nil
            let result: PreflightResult = try await self.request("/api/preflight", body: self.projectData())
            self.preflight = result
            self.message = self.t("检查通过：{n} 个视频，预计需要 {gb} GB 可用空间。", ["n":String(result.videos), "gb":String(result.requiredGB)])
        }
    }
    func startBuild() { enqueueProjects([project], start: true) }

    func addCurrentToQueue() { enqueueProjects([project]) }

    func addBothFormatsToQueue() {
        var dvd = project
        dvd.format = "dvd"; dvd.capacity = project.format == "dvd" ? project.capacity : "dvd5"
        var bd = project
        bd.format = "bluray"; bd.capacity = project.format == "bluray" ? project.capacity : "bd25"
        enqueueProjects([dvd, bd])
    }

    func importQueueProjects() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = true; panel.canChooseDirectories = false
        panel.allowsMultipleSelection = true; panel.allowedContentTypes = [.json]
        panel.message = nativeText("保存済みのプロジェクトをまとめてキューに追加します。", "选择多个已保存的项目，一次加入队列。", "Select saved projects to add them to the queue together.")
        panel.prompt = nativeText("キューに追加", "加入队列", "Add to Queue")
        guard panel.runModal() == .OK else { return }
        do {
            let incoming = try panel.urls.sorted { $0.lastPathComponent.localizedStandardCompare($1.lastPathComponent) == .orderedAscending }.map { url in
                try JSONDecoder().decode(DiscProject.self, from: Data(contentsOf: url))
            }
            enqueueProjects(incoming)
        } catch { report(error) }
    }

    // DiscProject is a value type: each task keeps the exact settings at submission.
    func enqueueProjects(_ projects: [DiscProject], start: Bool = false) {
        guard !queueIsBusy && !projects.isEmpty else { return }
        for candidate in projects where !candidate.menuEnabled {
            guard candidate.videos.count == 1 else {
                message = t("无菜单模式只支持一个视频，请改为有菜单或保留一个视频")
                return
            }
            guard candidate.noMenuEndAction == .stop else {
                message = t("无菜单模式统一播放结束后停止，请修改旧工程的循环设置")
                return
            }
        }
        let snapshots = projects
        queueOperation {
            let body = try JSONEncoder().encode(QueueSubmission(projects: snapshots))
            let reply: QueueSnapshot = try await self.request("/api/queue", body: body)
            self.queue.apply(reply)
            self.showQueue = true; self.showBurn = false
            if start {
                let started: QueueSnapshot = try await self.request("/api/queue/start", body: Data("{}".utf8))
                self.queue.apply(started)
            }
            self.message = String(format: self.nativeText("%d 件をキューに追加しました", "已加入 %d 个任务", "Added %d tasks to the queue"), snapshots.count)
        }
    }

    private func queueOperation(_ action: @escaping @MainActor () async throws -> Void) {
        guard !queueIsBusy else { return }
        queueIsBusy = true
        perform {
            defer { self.queueIsBusy = false }
            try await action()
        }
    }

    func startQueue() { queueCommand("/api/queue/start") }
    func pauseQueue() { queueCommand("/api/queue/pause") }
    func pauseQueueItem(_ item: QueueItem) { queueCommand("/api/queue/\(item.id)/pause") }
    func resumeQueueItem(_ item: QueueItem) { queueCommand("/api/queue/\(item.id)/resume") }
    func cancelQueueItem(_ item: QueueItem) { queueCommand("/api/queue/\(item.id)/cancel") }
    func retryQueueItem(_ item: QueueItem) { queueCommand("/api/queue/\(item.id)/retry") }
    func removeQueueItem(_ item: QueueItem) { queueCommand("/api/queue/\(item.id)", method: "DELETE") }
    func moveQueueItem(_ item: QueueItem, direction: String) {
        guard ["up", "down"].contains(direction), item.status == "pending" else { return }
        queueCommand("/api/queue/\(item.id)/move", body: try? JSONSerialization.data(withJSONObject: ["direction": direction]))
    }
    private func queueCommand(_ path: String, body: Data? = Data("{}".utf8), method: String = "POST") {
        queueOperation {
            let reply: QueueSnapshot = try await self.request(path, body: method == "DELETE" ? nil : body, method: method)
            self.queue.apply(reply)
        }
    }

    private func pollOnce() async throws {
        let reply: QueueSnapshot = try await request("/api/queue")
        queue.apply(reply)
    }
    private func startPolling() {
        pollTask?.cancel()
        pollTask = Task {
            while !Task.isCancelled && !stopped {
                do {
                    try await Task.sleep(nanoseconds: 1_000_000_000)
                    try await pollOnce()
                } catch {
                    if Task.isCancelled || stopped { break }
                    report(error)
                    // A transient request failure must not stop tracking a long queue.
                    try? await Task.sleep(nanoseconds: 4_000_000_000)
                }
            }
        }
    }
    func cancelBuild() {
        guard let item = queue.active else { return }
        cancelQueueItem(item)
    }
    func revealOutput(_ item: QueueItem? = nil) {
        if let path = (item ?? queue.displayItem)?.iso ?? (item ?? queue.displayItem)?.folder {
            NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: path)])
        } else if !project.outputDir.isEmpty { NSWorkspace.shared.open(URL(fileURLWithPath: project.outputDir)) }
    }
    func showLogs(_ item: QueueItem? = nil) {
        guard let item = item ?? queue.displayItem else { return }
        perform {
            let reply: LogReply = try await self.request("/api/queue/\(item.id)/log")
            self.logs = reply.text
            self.showLogWindow = true
        }
    }
    func openBurnWorkspace() {
        showBurn.toggle(); showQueue = false
        if showBurn { refreshBurnDevices() }
    }

    func discToolRequest<T: Decodable>(_ path: String, body: Data? = nil, timeout: TimeInterval = 30) async throws -> T {
        try await request(path, body: body, timeout: timeout)
    }
    func stopEditorPlaybackForDisc() { stopPlayback(); cancelActivation() }

    func chooseBurnISO() {
        guard !burnProtectsTermination else { return }
        let panel = NSOpenPanel()
        panel.canChooseFiles = true; panel.canChooseDirectories = false
        panel.allowedContentTypes = [UTType(filenameExtension: "iso") ?? .data]
        panel.message = nativeText("書き込む DVD-Video / Blu-ray の ISO を選択してください。", "选择要写入光盘的 DVD-Video 或 Blu-ray ISO。", "Choose a DVD-Video or Blu-ray ISO to burn.")
        guard panel.runModal() == .OK, let url = panel.url else { return }
        selectBurnISO(url.path)
    }

    func burnCompletedISO(_ item: QueueItem) {
        // Recheck the live queue: a displayed row may have changed since it was drawn.
        guard !burnProtectsTermination,
              let latest = queue.items.first(where: { $0.id == item.id }),
              let path = latest.completedISO else { return }
        selectBurnISO(path, exportTitle: latest.title)
    }

    func selectBurnISO(_ path: String, exportTitle: String? = nil) {
        guard !burnProtectsTermination else { return }
        showBurn = true; showQueue = false
        discToolTab = "iso"
        burn.selectImage(path, exportTitle: exportTitle)
        burnImageGeneration += 1
        let generation = burnImageGeneration
        burnCheckTask?.cancel(); burnGeneration += 1
        burn.image = nil; burn.check = nil; burn.error = nil; burn.isInspecting = true; burn.isChecking = false
        Task {
            defer { if generation == burnImageGeneration { burn.isInspecting = false } }
            do {
                let body = try JSONSerialization.data(withJSONObject: ["iso": path])
                let image: BurnImage = try await request("/api/burn/inspect", body: body)
                guard generation == burnImageGeneration else { return }
                burn.image = image
                burn.isInspecting = false
                await loadBurnDevices()
                checkBurnSelection()
            } catch {
                if generation == burnImageGeneration { burn.error = error.localizedDescription }
            }
        }
    }

    func selectBurnDevice(_ id: String) {
        guard !burnProtectsTermination && id != burn.selectedDeviceID else { return }
        burn.selectedDeviceID = id; burn.speed = 0
        checkBurnSelection()
    }

    func selectBurnSpeed(_ speed: Double) {
        guard !burnProtectsTermination && speed != burn.speed else { return }
        burn.speed = speed
        checkBurnSelection()
    }

    func selectBurnVerification(_ enabled: Bool) {
        guard !burnProtectsTermination && enabled != burn.verifyAfterBurn else { return }
        burn.verifyAfterBurn = enabled
        checkBurnSelection()
    }

    func refreshBurnDevices() { Task { await loadBurnDevices(forceCheck: true) } }

    private func loadBurnDevices(forceCheck: Bool = false) async {
        guard !burn.isRefreshing && !burnProtectsTermination else { return }
        burn.isRefreshing = true
        defer { burn.isRefreshing = false }
        do {
            let devices: BurnDevices = try await request("/api/burn/devices", timeout: 30)
            let changed = burn.applyDevices(devices)
            // A preflight may have sampled the drive while it was still reading a
            // newly inserted disc. Unchanged device data must not pin that failure.
            let retryDue = Date().timeIntervalSince(burnLastCheckAttempt ?? .distantPast) >= 15
            let needsRetry = !burn.isChecking && retryDue &&
                (burn.check == nil || burn.error != nil || (burn.check?.valid == false && burn.device?.media.isReady == true))
            if changed || forceCheck || needsRetry { checkBurnSelection() }
        } catch { burn.error = error.localizedDescription }
    }

    func checkBurnSelection() {
        burnCheckTask?.cancel(); burnGeneration += 1
        let generation = burnGeneration
        burn.check = nil; burn.isChecking = false
        guard !burnProtectsTermination && !burn.isInspecting,
              let image = burn.image, !burn.selectedDeviceID.isEmpty else { return }
        burnLastCheckAttempt = Date()
        let choice = BurnRequest(iso: image.path, deviceID: burn.selectedDeviceID, speed: burn.speed > 0 ? burn.speed : nil, verifyAfterBurn: burn.verifyAfterBurn)
        burn.error = nil; burn.isChecking = true
        burnCheckTask = Task {
            defer { if generation == burnGeneration { burn.isChecking = false } }
            do {
                let result: BurnCheck = try await request("/api/burn/check", body: JSONEncoder().encode(choice), timeout: 45)
                guard !Task.isCancelled && generation == burnGeneration else { return }
                burn.applyCheckedDevice(result.device)
                burn.check = result
            } catch {
                if !Task.isCancelled && generation == burnGeneration { burn.error = error.localizedDescription }
            }
        }
    }

    func startBurn() {
        guard burn.canStart, !discTools.protectsTermination, let image = burn.image else { return }
        discTools.prepareForBurn()
        let choice = BurnRequest(iso: image.path, deviceID: burn.selectedDeviceID, speed: burn.speed > 0 ? burn.speed : nil, verifyAfterBurn: burn.verifyAfterBurn)
        burn.isStarting = true; burn.awaitingStartStatus = true; burn.error = nil
        Task {
            defer { burn.isStarting = false }
            do {
                // The backend checks the live drive and media again before writing.
                let result: BurnSnapshot = try await request("/api/burn/start", body: JSONEncoder().encode(choice), timeout: 60)
                burn.apply(result)
                burn.awaitingStartStatus = false
            } catch {
                burn.error = error.localizedDescription
                burn.awaitingStartStatus = true
                // A lost start reply must not let Quit kill a writer that did start.
                try? await pollBurnStatus()
            }
        }
    }

    func showBurnLogs() {
        Task {
            do {
                let reply: LogReply = try await request("/api/burn/log")
                logs = reply.text; showLogWindow = true
            } catch { burn.error = error.localizedDescription }
        }
    }

    private func pollBurnStatus() async throws {
        let result: BurnSnapshot = try await request("/api/burn/status", timeout: 10)
        let justFinished = burn.snapshot.active && !result.active
        burn.apply(result)
        if burn.awaitingStartStatus { burn.awaitingStartStatus = false }
        if burn.connectionError != nil { burn.connectionError = nil }
        if justFinished && showBurn { await loadBurnDevices() }
    }

    private func startBurnPolling() {
        burnPollTask?.cancel()
        burnPollTask = Task {
            var tick = 0
            while !Task.isCancelled && !stopped {
                do {
                    try await pollBurnStatus()
                    if showBurn && tick % 5 == 0 { await loadBurnDevices() }
                } catch {
                    if Task.isCancelled || stopped { break }
                    if showBurn || burnProtectsTermination {
                        let text = error.localizedDescription
                        if burn.connectionError != text { burn.connectionError = text }
                    }
                }
                tick += 1
                do { try await Task.sleep(nanoseconds: 1_000_000_000) } catch { break }
            }
        }
    }

    func shutdown() {
        guard !stopped && !burnProtectsTermination else { return }
        discTools.shutdown()
        stopped = true; isReady = false
        previewTask?.cancel(); activationTask?.cancel(); pollTask?.cancel(); burnPollTask?.cancel(); burnCheckTask?.cancel(); stopPlayback()
        session.invalidateAndCancel()
        if backend?.isRunning == true { backend?.terminate() }
        backend = nil
        try? backendLog?.close(); backendLog = nil
    }
}
