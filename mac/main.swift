import Cocoa

// MARK: - Data model

struct WorknowSnapshot: Decodable {
    let schema_version: Int
    let generated_at: String
    let host: String
    let active_tasks_count: Int
    let repos: [Repo]
    let processes: [Proc]
    let sessions_text: String

    struct Repo: Decodable {
        let name: String
        let path: String
        let branch: String
        let dirty: Bool
        let changes: String
        let last_commit: String
        let recent_commits: [String]
        let has_active_agent: Bool
        let is_active: Bool
    }

    struct Proc: Decodable {
        let pid: String
        let command: String
        let cwd: String?
    }
}

// MARK: - Path discovery

enum SnapshotSource {
    /// Mirrors the CLI's default output location. If a user has overridden
    /// `output` in `~/.config/worknow/config.toml`, we walk the same path
    /// substitution rules so the UI follows.
    static func resolvedPath() -> URL {
        let cfg = readConfigOutput() ?? "~/.openclaw/workspace/current-work.md"
        let mdPath = (cfg as NSString).expandingTildeInPath
        let mdURL = URL(fileURLWithPath: mdPath)
        return mdURL.deletingPathExtension().appendingPathExtension("json")
    }

    /// Minimal TOML probe — only looks for `output = "..."` at the top level
    /// to avoid pulling in a TOML parser. Anything more elaborate falls back
    /// to the default and the user gets the standard location.
    private static func readConfigOutput() -> String? {
        let cfgURL = URL(fileURLWithPath: ("~/.config/worknow/config.toml" as NSString).expandingTildeInPath)
        guard let text = try? String(contentsOf: cfgURL, encoding: .utf8) else { return nil }
        for raw in text.split(whereSeparator: { $0.isNewline }) {
            let line = raw.trimmingCharacters(in: .whitespaces)
            if line.hasPrefix("output") && line.contains("=") {
                let after = line.split(separator: "=", maxSplits: 1).last?
                    .trimmingCharacters(in: .whitespaces) ?? ""
                if after.hasPrefix("\"") && after.hasSuffix("\"") && after.count >= 2 {
                    return String(after.dropFirst().dropLast())
                }
            }
        }
        return nil
    }
}

// MARK: - Draggable floating window

final class FloatingPanel: NSPanel {
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { false }

    init() {
        super.init(
            contentRect: NSRect(x: 0, y: 0, width: 380, height: 460),
            styleMask: [.borderless, .nonactivatingPanel, .resizable],
            backing: .buffered,
            defer: false
        )
        self.level = .floating
        self.isOpaque = false
        self.backgroundColor = .clear
        self.hasShadow = true
        self.isMovableByWindowBackground = true
        self.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        self.hidesOnDeactivate = false
        self.minSize = NSSize(width: 320, height: 240)
    }
}

// MARK: - Content view

final class WorknowContentView: NSView {
    private let titleLabel = NSTextField(labelWithString: "worknow")
    private let countLabel = NSTextField(labelWithString: "0 tasks")
    private let updatedLabel = NSTextField(labelWithString: "")
    private let stack = NSStackView()
    private let scroll = NSScrollView()
    private let closeButton = NSButton()

    var onClose: (() -> Void)?

    override init(frame frameRect: NSRect) {
        super.init(frame: frameRect)
        wantsLayer = true
        // Translucent rounded background using NSVisualEffectView.
        let effect = NSVisualEffectView(frame: bounds)
        effect.autoresizingMask = [.width, .height]
        effect.material = .hudWindow
        effect.state = .active
        effect.blendingMode = .behindWindow
        effect.wantsLayer = true
        effect.layer?.cornerRadius = 14
        effect.layer?.masksToBounds = true
        addSubview(effect)

        layer?.cornerRadius = 14
        layer?.masksToBounds = true

        // Header.
        titleLabel.font = NSFont.boldSystemFont(ofSize: 14)
        titleLabel.textColor = .labelColor
        countLabel.font = NSFont.systemFont(ofSize: 12, weight: .medium)
        countLabel.textColor = .secondaryLabelColor
        updatedLabel.font = NSFont.systemFont(ofSize: 10)
        updatedLabel.textColor = .tertiaryLabelColor

        closeButton.title = "×"
        closeButton.bezelStyle = .accessoryBarAction
        closeButton.isBordered = false
        closeButton.font = NSFont.systemFont(ofSize: 16, weight: .light)
        closeButton.target = self
        closeButton.action = #selector(handleClose)
        closeButton.translatesAutoresizingMaskIntoConstraints = false

        titleLabel.translatesAutoresizingMaskIntoConstraints = false
        countLabel.translatesAutoresizingMaskIntoConstraints = false
        updatedLabel.translatesAutoresizingMaskIntoConstraints = false

        let header = NSView()
        header.translatesAutoresizingMaskIntoConstraints = false
        header.addSubview(titleLabel)
        header.addSubview(countLabel)
        header.addSubview(updatedLabel)
        header.addSubview(closeButton)

        NSLayoutConstraint.activate([
            closeButton.leadingAnchor.constraint(equalTo: header.leadingAnchor, constant: 10),
            closeButton.centerYAnchor.constraint(equalTo: titleLabel.centerYAnchor),
            closeButton.widthAnchor.constraint(equalToConstant: 22),
            closeButton.heightAnchor.constraint(equalToConstant: 22),

            titleLabel.leadingAnchor.constraint(equalTo: closeButton.trailingAnchor, constant: 6),
            titleLabel.topAnchor.constraint(equalTo: header.topAnchor, constant: 10),

            countLabel.leadingAnchor.constraint(equalTo: titleLabel.trailingAnchor, constant: 8),
            countLabel.firstBaselineAnchor.constraint(equalTo: titleLabel.firstBaselineAnchor),

            updatedLabel.trailingAnchor.constraint(equalTo: header.trailingAnchor, constant: -14),
            updatedLabel.firstBaselineAnchor.constraint(equalTo: titleLabel.firstBaselineAnchor),

            header.heightAnchor.constraint(equalToConstant: 36),
        ])

        // Scrollable body.
        stack.orientation = .vertical
        stack.alignment = .leading
        stack.spacing = 6
        stack.translatesAutoresizingMaskIntoConstraints = false
        stack.edgeInsets = NSEdgeInsets(top: 6, left: 12, bottom: 12, right: 12)

        scroll.translatesAutoresizingMaskIntoConstraints = false
        scroll.drawsBackground = false
        scroll.hasVerticalScroller = true
        scroll.documentView = stack

        addSubview(header)
        addSubview(scroll)

        NSLayoutConstraint.activate([
            header.leadingAnchor.constraint(equalTo: leadingAnchor),
            header.trailingAnchor.constraint(equalTo: trailingAnchor),
            header.topAnchor.constraint(equalTo: topAnchor),

            scroll.leadingAnchor.constraint(equalTo: leadingAnchor),
            scroll.trailingAnchor.constraint(equalTo: trailingAnchor),
            scroll.topAnchor.constraint(equalTo: header.bottomAnchor),
            scroll.bottomAnchor.constraint(equalTo: bottomAnchor),

            stack.widthAnchor.constraint(equalTo: scroll.widthAnchor),
        ])
    }

    required init?(coder: NSCoder) { fatalError() }

    @objc private func handleClose() { onClose?() }

    func update(with snapshot: WorknowSnapshot?) {
        for v in stack.arrangedSubviews { stack.removeArrangedSubview(v); v.removeFromSuperview() }
        guard let snap = snapshot else {
            countLabel.stringValue = "—"
            updatedLabel.stringValue = "no data"
            addEmpty("No snapshot yet. Run `worknow` once.")
            return
        }
        countLabel.stringValue = "\(snap.active_tasks_count) \(snap.active_tasks_count == 1 ? "task" : "tasks")"
        updatedLabel.stringValue = relativeUpdatedLabel(isoString: snap.generated_at)

        let active = snap.repos.filter { $0.is_active }
        let idle = snap.repos.filter { !$0.is_active }

        if !active.isEmpty {
            addSectionHeader("Active")
            for repo in active { stack.addArrangedSubview(repoRow(repo)) }
        }
        if !snap.processes.isEmpty {
            addSectionHeader("Agents")
            for proc in snap.processes.prefix(10) { stack.addArrangedSubview(processRow(proc)) }
        }
        if !idle.isEmpty {
            addSectionHeader("Other tracked repos")
            for repo in idle.prefix(8) { stack.addArrangedSubview(repoRow(repo, dim: true)) }
        }
        if active.isEmpty && snap.processes.isEmpty {
            addEmpty("No active work right now.")
        }
    }

    private func addSectionHeader(_ text: String) {
        let label = NSTextField(labelWithString: text.uppercased())
        label.font = NSFont.systemFont(ofSize: 10, weight: .semibold)
        label.textColor = .tertiaryLabelColor
        let wrap = NSStackView(views: [label])
        wrap.orientation = .horizontal
        wrap.edgeInsets = NSEdgeInsets(top: 8, left: 0, bottom: 2, right: 0)
        stack.addArrangedSubview(wrap)
    }

    private func addEmpty(_ text: String) {
        let label = NSTextField(labelWithString: text)
        label.font = NSFont.systemFont(ofSize: 11)
        label.textColor = .secondaryLabelColor
        stack.addArrangedSubview(label)
    }

    private func repoRow(_ repo: WorknowSnapshot.Repo, dim: Bool = false) -> NSView {
        let nameLabel = NSTextField(labelWithString: repo.name)
        nameLabel.font = NSFont.systemFont(ofSize: 12, weight: .medium)
        nameLabel.textColor = dim ? .tertiaryLabelColor : .labelColor

        var meta = "\(repo.branch) · \(repo.changes)"
        if repo.has_active_agent { meta += " · agent" }
        let metaLabel = NSTextField(labelWithString: meta)
        metaLabel.font = NSFont.monospacedSystemFont(ofSize: 10, weight: .regular)
        metaLabel.textColor = .secondaryLabelColor

        let dot = NSTextField(labelWithString: dotForRepo(repo))
        dot.font = NSFont.systemFont(ofSize: 10)

        let titleRow = NSStackView(views: [dot, nameLabel])
        titleRow.orientation = .horizontal
        titleRow.spacing = 4

        let row = NSStackView(views: [titleRow, metaLabel])
        row.orientation = .vertical
        row.alignment = .leading
        row.spacing = 1
        return row
    }

    private func processRow(_ proc: WorknowSnapshot.Proc) -> NSView {
        let cmd = NSTextField(labelWithString: shortCommand(proc.command))
        cmd.font = NSFont.systemFont(ofSize: 11)
        cmd.lineBreakMode = .byTruncatingMiddle
        cmd.usesSingleLineMode = true

        let cwdString = proc.cwd.map { (($0 as NSString).abbreviatingWithTildeInPath) } ?? "—"
        let meta = NSTextField(labelWithString: "pid \(proc.pid) · \(cwdString)")
        meta.font = NSFont.monospacedSystemFont(ofSize: 10, weight: .regular)
        meta.textColor = .secondaryLabelColor
        meta.lineBreakMode = .byTruncatingTail
        meta.usesSingleLineMode = true

        let row = NSStackView(views: [cmd, meta])
        row.orientation = .vertical
        row.alignment = .leading
        row.spacing = 1
        return row
    }

    private func shortCommand(_ command: String) -> String {
        if command.count <= 80 { return command }
        return String(command.prefix(40)) + "…" + String(command.suffix(36))
    }

    private func dotForRepo(_ repo: WorknowSnapshot.Repo) -> String {
        if repo.dirty && repo.has_active_agent { return "🟢" }
        if repo.dirty { return "🟡" }
        if repo.has_active_agent { return "🔵" }
        return "⚪"
    }

    private func relativeUpdatedLabel(isoString: String) -> String {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        guard let date = formatter.date(from: isoString) else { return "" }
        let elapsed = Int(Date().timeIntervalSince(date))
        if elapsed < 60 { return "updated \(elapsed)s ago" }
        if elapsed < 3600 { return "updated \(elapsed / 60)m ago" }
        return "updated \(elapsed / 3600)h ago"
    }
}

// MARK: - App delegate

@main
@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    private var statusItem: NSStatusItem!
    private let panel = FloatingPanel()
    private lazy var content = WorknowContentView(frame: panel.contentLayoutRect)
    private var refreshTimer: Timer?
    private var lastSnapshot: WorknowSnapshot?
    private let positionKey = "worknow.panel.frame"

    static func main() {
        let app = NSApplication.shared
        let delegate = AppDelegate()
        app.delegate = delegate
        app.run()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        setupStatusItem()
        setupPanel()
        loadAndApply()
        startRefreshLoop()
    }

    private func setupStatusItem() {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        if let button = statusItem.button {
            button.title = "•"
            button.font = NSFont.menuBarFont(ofSize: 0)
            button.target = self
            button.action = #selector(togglePanel)
            button.sendAction(on: [.leftMouseUp, .rightMouseUp])
        }
    }

    private func setupPanel() {
        content.frame = panel.contentLayoutRect
        content.autoresizingMask = [.width, .height]
        panel.contentView = content
        content.onClose = { [weak self] in self?.hidePanel() }

        // Restore position if we have one, else anchor near the top-right.
        if let saved = UserDefaults.standard.string(forKey: positionKey) {
            panel.setFrame(NSRectFromString(saved), display: false)
        } else {
            if let screen = NSScreen.main {
                let f = screen.visibleFrame
                let size = panel.frame.size
                let origin = NSPoint(x: f.maxX - size.width - 16, y: f.maxY - size.height - 16)
                panel.setFrame(NSRect(origin: origin, size: size), display: false)
            }
        }

        // Persist position on every move/resize.
        NotificationCenter.default.addObserver(
            self,
            selector: #selector(persistFrame),
            name: NSWindow.didMoveNotification,
            object: panel
        )
        NotificationCenter.default.addObserver(
            self,
            selector: #selector(persistFrame),
            name: NSWindow.didResizeNotification,
            object: panel
        )
    }

    @objc private func persistFrame() {
        UserDefaults.standard.set(NSStringFromRect(panel.frame), forKey: positionKey)
    }

    @objc private func togglePanel() {
        if panel.isVisible { hidePanel() } else { showPanel() }
    }

    private func showPanel() {
        loadAndApply()
        panel.orderFrontRegardless()
    }

    private func hidePanel() {
        panel.orderOut(nil)
    }

    private func startRefreshLoop() {
        refreshTimer = Timer.scheduledTimer(withTimeInterval: 30, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.loadAndApply() }
        }
        RunLoop.current.add(refreshTimer!, forMode: .common)
    }

    private func loadAndApply() {
        let url = SnapshotSource.resolvedPath()
        let snap = loadSnapshot(from: url)
        lastSnapshot = snap
        updateStatusBadge(snap)
        content.update(with: snap)
    }

    private func loadSnapshot(from url: URL) -> WorknowSnapshot? {
        guard let data = try? Data(contentsOf: url) else { return nil }
        let decoder = JSONDecoder()
        return try? decoder.decode(WorknowSnapshot.self, from: data)
    }

    private func updateStatusBadge(_ snap: WorknowSnapshot?) {
        guard let button = statusItem.button else { return }
        guard let snap = snap else {
            button.title = "•"
            return
        }
        let n = snap.active_tasks_count
        button.title = n > 0 ? "● \(n)" : "○"
    }
}

