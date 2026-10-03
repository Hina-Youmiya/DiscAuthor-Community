import AppKit
import SwiftUI
import Darwin

/// Navigation is sent to the disc's actual DVD/HDMV interpreter, not the editor preview.
enum DiscNavigation: UInt32 {
    case activate = 0, up, down, left, right, popup
}

private enum DiscPlayerFailure: Error { case runtimeMissing, invalidRuntime, initialization, open }

/// Only the stable, public libVLC 3 ABI is used. No Homebrew or VLC installation is required.
private final class DiscVLCAPI {
    typealias Ptr = UnsafeMutableRawPointer
    typealias Create = @convention(c) (Int32, UnsafePointer<UnsafePointer<CChar>?>?) -> Ptr?
    typealias Release = @convention(c) (Ptr?) -> Void
    typealias NewMedia = @convention(c) (Ptr?, UnsafePointer<CChar>?) -> Ptr?
    typealias NewPlayer = @convention(c) (Ptr?) -> Ptr?
    typealias SetObject = @convention(c) (Ptr?, Ptr?) -> Void
    typealias AddOption = @convention(c) (Ptr?, UnsafePointer<CChar>?) -> Void
    typealias GetInt = @convention(c) (Ptr?) -> Int32
    typealias SetInt = @convention(c) (Ptr?, Int32) -> Void
    typealias GetTime = @convention(c) (Ptr?) -> Int64
    typealias SetTime = @convention(c) (Ptr?, Int64) -> Void
    typealias Navigate = @convention(c) (Ptr?, UInt32) -> Void
    typealias AudioVolume = @convention(c) (Ptr?, Int32) -> Int32
    let handle: Ptr
    let newInstance: Create, releaseInstance: Release
    let newMedia: NewMedia, releaseMedia: Release, addOption: AddOption
    let newPlayer: NewPlayer, releasePlayer: Release, setNSObject: SetObject
    let play: GetInt, pause: Release, stop: Release, getState: GetInt
    let getTime: GetTime, getLength: GetTime, setTime: SetTime
    let navigate: Navigate, setTitle: SetInt, getTitle: GetInt, getTitleCount: GetInt
    let getChapter: GetInt, getChapterCount: GetInt, nextChapter: Release, previousChapter: Release
    let setVolume: AudioVolume, setMouseInput: SetInt, setKeyInput: SetInt
    let runtimeRoot: URL
    init() throws {
        var roots: [URL] = []
        if let custom = ProcessInfo.processInfo.environment["DISCAUTHOR_VLC_RUNTIME"] {
            roots.append(URL(fileURLWithPath: custom, isDirectory: true))
        }
        if let resources = Bundle.main.resourceURL { roots.append(resources.appendingPathComponent("VLC")) }
        guard let root = roots.first(where: { FileManager.default.fileExists(atPath: $0.appendingPathComponent("lib/libvlc.dylib").path) }) else { throw DiscPlayerFailure.runtimeMissing }
        runtimeRoot = root
        // libvlccore needs its plugins and data relative to the embedded runtime.
        setenv("VLC_PLUGIN_PATH", root.appendingPathComponent("plugins").path, 1)
        setenv("VLC_DATA_PATH", root.appendingPathComponent("share").path, 1)
        guard let loaded = dlopen(root.appendingPathComponent("lib/libvlc.dylib").path, RTLD_NOW | RTLD_LOCAL) else { throw DiscPlayerFailure.invalidRuntime }
        handle = loaded
        func symbol<T>(_ name: String, _: T.Type) throws -> T {
            guard let address = dlsym(loaded, name) else { throw DiscPlayerFailure.invalidRuntime }
            return unsafeBitCast(address, to: T.self)
        }
        newInstance = try symbol("libvlc_new", Create.self)
        releaseInstance = try symbol("libvlc_release", Release.self)
        newMedia = try symbol("libvlc_media_new_location", NewMedia.self)
        releaseMedia = try symbol("libvlc_media_release", Release.self)
        addOption = try symbol("libvlc_media_add_option", AddOption.self)
        newPlayer = try symbol("libvlc_media_player_new_from_media", NewPlayer.self)
        releasePlayer = try symbol("libvlc_media_player_release", Release.self)
        setNSObject = try symbol("libvlc_media_player_set_nsobject", SetObject.self)
        play = try symbol("libvlc_media_player_play", GetInt.self)
        pause = try symbol("libvlc_media_player_pause", Release.self)
        stop = try symbol("libvlc_media_player_stop", Release.self)
        getState = try symbol("libvlc_media_player_get_state", GetInt.self)
        getTime = try symbol("libvlc_media_player_get_time", GetTime.self)
        getLength = try symbol("libvlc_media_player_get_length", GetTime.self)
        setTime = try symbol("libvlc_media_player_set_time", SetTime.self)
        navigate = try symbol("libvlc_media_player_navigate", Navigate.self)
        setTitle = try symbol("libvlc_media_player_set_title", SetInt.self)
        getTitle = try symbol("libvlc_media_player_get_title", GetInt.self)
        getTitleCount = try symbol("libvlc_media_player_get_title_count", GetInt.self)
        getChapter = try symbol("libvlc_media_player_get_chapter", GetInt.self)
        getChapterCount = try symbol("libvlc_media_player_get_chapter_count", GetInt.self)
        nextChapter = try symbol("libvlc_media_player_next_chapter", Release.self)
        previousChapter = try symbol("libvlc_media_player_previous_chapter", Release.self)
        setVolume = try symbol("libvlc_audio_set_volume", AudioVolume.self)
        setMouseInput = try symbol("libvlc_video_set_mouse_input", SetInt.self)
        setKeyInput = try symbol("libvlc_video_set_key_input", SetInt.self)
    }
    // VLC plugins register Objective-C classes. Unloading/reloading their dylibs is unsafe.
    // Keep the dlopen handle until process exit; players and instances are still released.
}

private struct DiscPlayerSnapshot {
    var state: String = "idle"
    var time: Double = 0, length: Double = 0
    var title = -1, titles = 0, chapter = -1, chapters = 0
}

/// Blocking libVLC calls (especially stop) must never run on the main AppKit thread.
private final class DiscPlayerSession: @unchecked Sendable {
    let queue = DispatchQueue(label: "jp.discauthor.disc-player", qos: .userInitiated)
    var api: DiscVLCAPI?
    var instance: UnsafeMutableRawPointer?
    var player: UnsafeMutableRawPointer?
    var retainedSurface: NSView?
    private func close(releaseInstance: Bool = false) {
        if let p = player, let a = api {
            a.stop(p)
            a.setNSObject(p, nil)
            a.releasePlayer(p)
        }
        player = nil
        if releaseInstance {
            if let i = instance { api?.releaseInstance(i) }
            instance = nil
        }
        let oldSurface = retainedSurface
        retainedSurface = nil
        // Release the NSView on the main thread after macosx vout has detached.
        DispatchQueue.main.async { _ = oldSurface }
    }
    func shutdown(releaseInstance: Bool = false) { queue.async { self.close(releaseInstance: releaseInstance) } }
    func load(path: String, format: String, surface: NSView?, volume: Int, completion: @escaping (String?) -> Void) {
        queue.async {
            self.close()
            func trace(_ text: String) { if ProcessInfo.processInfo.environment["DISCAUTHOR_PLAYER_DEBUG"] == "1" { NSLog("DiscPlayer: %@", text) } }
            trace("loading runtime")
            do {
                if self.api == nil { self.api = try DiscVLCAPI() }
                guard let a = self.api else { throw DiscPlayerFailure.runtimeMissing }
                if self.instance == nil {
                trace("creating libVLC instance")
                let arguments = ["--no-video-title-show", "--no-osd", "--no-snapshot-preview", "--no-stats", "--vout=macosx", "--aout=auhal", "--intf=dummy", "--no-media-library", "--ignore-config", "--no-plugins-cache"] + (ProcessInfo.processInfo.environment["DISCAUTHOR_PLAYER_DEBUG"] == "1" ? ["--verbose=2"] : [])
                let owned = arguments.map { strdup($0) }
                defer { owned.forEach { free($0) } }
                let pointers = owned.map { $0.map { UnsafePointer<CChar>($0) } }
                self.instance = pointers.withUnsafeBufferPointer { a.newInstance(Int32($0.count), $0.baseAddress) }
                }
                guard let instance = self.instance else { throw DiscPlayerFailure.initialization }
                trace("creating disc media")
                // VLC demux decodes URI paths. Use Foundation escaping so percent signs,
                // hash characters and Japanese names cannot alter the selected source.
                let fileURL = URL(fileURLWithPath: path).absoluteString
                let mrl = (format == "bluray" ? "bluray" : "dvdnav") + fileURL.dropFirst(4)
                guard let media = mrl.withCString({ a.newMedia(instance, $0) }) else { throw DiscPlayerFailure.open }
                defer { a.releaseMedia(media) }
                for option in [":bluray-menu", ":disc-caching=800", ":file-caching=800"] {
                    option.withCString { a.addOption(media, $0) }
                }
                self.player = a.newPlayer(media)
                guard let p = self.player else { throw DiscPlayerFailure.open }
                self.retainedSurface = surface
                if let surface { a.setNSObject(p, Unmanaged.passUnretained(surface).toOpaque()) }
                a.setMouseInput(p, 1)
                // Native responder/remote controls own keyboard commands, preventing double navigation.
                a.setKeyInput(p, 0)
                _ = a.setVolume(p, Int32(volume))
                trace("starting playback")
                guard a.play(p) == 0 else { throw DiscPlayerFailure.open }
                trace("playback started")
                DispatchQueue.main.async { completion(nil) }
            } catch {
                self.close()
                let key: String
                switch error {
                case DiscPlayerFailure.runtimeMissing: key = "player_runtime_missing"
                case DiscPlayerFailure.invalidRuntime: key = "player_runtime_invalid"
                default: key = "player_open_failed"
                }
                DispatchQueue.main.async { completion(key) }
            }
        }
    }
    func attach(_ surface: NSView) {
        queue.async {
            self.retainedSurface = surface
            if let p = self.player { self.api?.setNSObject(p, Unmanaged.passUnretained(surface).toOpaque()) }
        }
    }
    func action(_ body: @escaping (DiscVLCAPI, UnsafeMutableRawPointer) -> Void) {
        queue.async { if let a = self.api, let p = self.player { body(a, p) } }
    }
    func snapshot(_ completion: @escaping (DiscPlayerSnapshot) -> Void) {
        queue.async {
            var s = DiscPlayerSnapshot()
            if let a = self.api, let p = self.player {
                let states = ["idle", "opening", "opening", "playing", "paused", "stopped", "ended", "error"]
                let value = Int(a.getState(p))
                s.state = states.indices.contains(value) ? states[value] : "error"
                s.time = max(0, Double(a.getTime(p)) / 1000)
                s.length = max(0, Double(a.getLength(p)) / 1000)
                s.title = Int(a.getTitle(p)); s.titles = max(0, Int(a.getTitleCount(p)))
                s.chapter = Int(a.getChapter(p)); s.chapters = max(0, Int(a.getChapterCount(p)))
            }
            DispatchQueue.main.async { completion(s) }
        }
    }
}

/// libVLC can discard title/length and report a stale clock on its final sample.
/// Remember timing only within the same title, never across a menu/title transition.
struct DiscPlayerTimeline {
    private var title = -1
    private var lastDuration: Double = 0
    private var lastTime: Double = 0

    mutating func update(state: String, title newTitle: Int, time: Double, length: Double) -> (time: Double, duration: Double) {
        if newTitle >= 0 && newTitle != title {
            title = newTitle; lastDuration = 0; lastTime = 0
        }
        let validLength = length.isFinite && length > 0 ? length : 0
        let validTime = time.isFinite ? max(0, time) : 0
        if validLength > 0 { lastDuration = validLength }
        if state == "ended" {
            // Ended means the program finished; a later stale clock must not
            // make the displayed position exceed the last valid duration.
            lastTime = lastDuration > 0 ? lastDuration : lastTime
        } else {
            lastTime = lastDuration > 0 ? min(validTime, lastDuration) : validTime
        }
        return (lastTime, lastDuration)
    }
}

@MainActor final class DiscPlayerEngine: ObservableObject {
    @Published private(set) var state = "idle"
    @Published private(set) var error: String?
    @Published private(set) var currentTime: Double = 0
    @Published private(set) var duration: Double = 0
    @Published private(set) var currentTitle = -1
    @Published private(set) var titleCount = 0
    @Published private(set) var currentChapter = -1
    @Published private(set) var chapterCount = 0
    @Published private(set) var volume = 100
    @Published private(set) var sourcePath = ""
    @Published private(set) var format = "dvd"
    private let session = DiscPlayerSession()
    private var timer: Timer?
    private weak var surface: NSView?
    private var generation = 0
    private var pollPending = false
    private var timeline = DiscPlayerTimeline()

    func open(path: String, format: String) {
        guard ["dvd", "bluray"].contains(format), !path.isEmpty, path.hasPrefix("/") else {
            stop(); error = "player_source_invalid"; state = "error"; return
        }
        generation += 1
        let current = generation
        sourcePath = path; self.format = format; error = nil; state = "opening"
        currentTime = 0; duration = 0; currentTitle = -1; titleCount = 0; currentChapter = -1; chapterCount = 0
        timeline = DiscPlayerTimeline()
        timer?.invalidate(); timer = nil
        session.load(path: path, format: format, surface: surface, volume: volume) { [weak self] failure in
            guard let self, self.generation == current else { return }
            if let failure { self.error = failure; self.state = "error"; return }
            self.timer = Timer.scheduledTimer(withTimeInterval: 0.35, repeats: true) { [weak self] _ in
                Task { @MainActor in self?.refresh() }
            }
            self.refresh()
        }
    }
    func stop() {
        generation += 1; timer?.invalidate(); timer = nil
        state = "stopped"; currentTime = 0
        session.shutdown()
    }
    /// Wait for libVLC to close the disc/device before operating the drive tray.
    /// The serial worker does the blocking stop; AppKit remains free to detach video views.
    func stopAndWait() async {
        stop()
        await withCheckedContinuation { (continuation: CheckedContinuation<Void, Never>) in
            session.queue.async { continuation.resume() }
        }
    }
    func togglePause() {
        if state == "stopped" || state == "ended" { open(path: sourcePath, format: format) }
        else { session.action { $0.pause($1) } }
    }
    func seek(seconds: Double) {
        guard seconds.isFinite else { return }
        let ms = Int64(max(0, min(seconds, duration > 0 ? duration : seconds)) * 1000)
        session.action { $0.setTime($1, ms) }
    }
    func navigate(_ direction: DiscNavigation) { session.action { $0.navigate($1, direction.rawValue) } }
    /// Both VLC dvdnav and bluray access modules designate title zero as the disc menu.
    func topMenu() { session.action { $0.setTitle($1, 0) } }
    func setTitle(_ title: Int) {
        guard title >= 0, title < titleCount else { return }
        session.action { $0.setTitle($1, Int32(title)) }
    }
    func nextChapter() { session.action { $0.nextChapter($1) } }
    func previousChapter() { session.action { $0.previousChapter($1) } }
    func setVolume(_ value: Int) {
        volume = max(0, min(100, value))
        let chosen = Int32(volume)
        session.action { a, p in _ = a.setVolume(p, chosen) }
    }
    fileprivate func attach(_ view: NSView) { surface = view; session.attach(view) }
    fileprivate func detach(_ view: NSView) {
        guard surface === view else { return }
        surface = nil
        // A removed player surface must not keep a disc/device open in the background.
        stop()
    }
    private func refresh() {
        guard !pollPending, state != "ended", state != "error" else { return }
        pollPending = true
        let current = generation
        session.snapshot { [weak self] s in
            guard let self else { return }
            self.pollPending = false
            guard current == self.generation else { return }
            let timing = self.timeline.update(state: s.state, title: s.title, time: s.time, length: s.length)
            self.state = s.state; self.currentTime = timing.time; self.duration = timing.duration
            self.currentTitle = s.title; self.titleCount = s.titles
            self.currentChapter = s.chapter; self.chapterCount = s.chapters
            if s.state == "ended" { self.timer?.invalidate(); self.timer = nil }
            if s.state == "error" {
                self.error = "player_disc_unreadable"
                self.timer?.invalidate(); self.timer = nil
            }
        }
    }
    deinit { timer?.invalidate(); session.shutdown(releaseInstance: true) }
}

final class DiscPlayerHostView: NSView {
    weak var engine: DiscPlayerEngine?
    private var eventMonitor: Any?
    private var pressedVideoView: NSView?
    override var acceptsFirstResponder: Bool { true }
    override init(frame frameRect: NSRect) {
        super.init(frame: frameRect)
        wantsLayer = true; layer?.backgroundColor = NSColor.black.cgColor
    }
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }
    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        if let monitor = eventMonitor { NSEvent.removeMonitor(monitor); eventMonitor = nil }
        guard window != nil else { return }
        window?.acceptsMouseMovedEvents = true
        eventMonitor = NSEvent.addLocalMonitorForEvents(matching: [.keyDown, .leftMouseDown, .leftMouseUp, .mouseMoved]) { [weak self] event in
            guard let self, event.window === self.window else { return event }
            if event.type == .leftMouseUp, let video = self.pressedVideoView {
                self.pressedVideoView = nil
                video.mouseUp(with: event)
                return nil
            }
            if event.type == .leftMouseDown || event.type == .mouseMoved {
                if self.bounds.contains(self.convert(event.locationInWindow, from: nil)), let video = self.subviews.first {
                    // SwiftUI hosting does not route all pointer events to VLC's
                    // dynamically attached video subview. Forward the real events,
                    // updating coordinates before clicks, and consume them once.
                    video.mouseMoved(with: event)
                    if event.type == .leftMouseDown {
                        self.window?.makeFirstResponder(self)
                        self.pressedVideoView = video
                        video.mouseDown(with: event)
                    }
                    return nil
                }
                return event
            }
            guard let responder = self.window?.firstResponder as? NSView,
                  responder === self || responder.isDescendant(of: self),
                  event.modifierFlags.intersection([.command, .control, .option]).isEmpty else { return event }
            switch event.keyCode {
            case 123: self.engine?.navigate(.left)
            case 124: self.engine?.navigate(.right)
            case 125: self.engine?.navigate(.down)
            case 126: self.engine?.navigate(.up)
            case 36, 76: self.engine?.navigate(.activate)
            case 53: self.engine?.topMenu()
            case 49: self.engine?.togglePause()
            default: return event
            }
            return nil
        }
    }
    deinit { if let monitor = eventMonitor { NSEvent.removeMonitor(monitor) } }
}

struct DiscPlayerSurface: NSViewRepresentable {
    let engine: DiscPlayerEngine
    func makeNSView(context: Context) -> DiscPlayerHostView {
        let view = DiscPlayerHostView(frame: .zero)
        view.engine = engine; engine.attach(view)
        return view
    }
    func updateNSView(_ view: DiscPlayerHostView, context: Context) { view.engine = engine }
    static func dismantleNSView(_ view: DiscPlayerHostView, coordinator: ()) { view.engine?.detach(view) }
}
