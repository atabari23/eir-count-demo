import Foundation

/// Sweep to target (port of Android Fill.kt). The pharmacist sets the prescribed quantity, pours, and Eir counts the pile on
/// the tray's main surface ("poured"). Pills swept into the chute are not counted by the model (it counts the main surface
/// only), so while the pharmacist sweeps, poured - live count = pills in the chute. When the tray shows poured - target,
/// Eir takes a check photo ("left"): dispensed = poured - left. Each photo is its own saved count; `CountRecord.fill`
/// links them: {"id": fill id, "role": "pour" | "rest", "target": n, "pourId": id of the pour photo (rest only)}.
enum Sweep {
    enum Phase: String { case pour, addMore, sweep, done }

    /// What to do after the pour photo.
    static func afterPour(target: Int, poured: Int) -> Phase {
        if poured == target { return .done }
        return poured < target ? .addMore : .sweep
    }

    /// Pills that should be left on the tray once the target is in the chute.
    static func leftWanted(target: Int, poured: Int) -> Int { poured - target }

    /// Positive = too many in the chute (put some back), negative = sweep more.
    static func difference(target: Int, poured: Int, left: Int) -> Int { (poured - left) - target }

    /// Whether the live view says the tray is at the wanted number (then a check photo is taken when the phone is steady).
    static func readyToCheck(target: Int, poured: Int, liveLeft: Int, visible: Bool) -> Bool { visible && liveLeft == leftWanted(target: target, poured: poured) }

    /// Add-more phase: a new pour photo once the tray shows more pills than the last pour photo.
    static func readyToRecount(lastPoured: Int, liveCount: Int, visible: Bool) -> Bool { visible && liveCount > lastPoured }

    static func fillJSON(id: String, role: String, target: Int, pourId: String? = nil) -> [String: Any] {
        var o: [String: Any] = ["id": id, "role": role, "target": target]
        if let pourId { o["pourId"] = pourId }
        return o
    }
}

/// A fill as History shows it: the latest pour photo and (after sweeping) the latest check photo of the tray.
struct FillSummary {
    let id: String
    let target: Int
    let pour: CountRecord
    let rest: CountRecord?

    var poured: Int { pour.finalCount }
    var left: Int? { rest?.finalCount }
    /// What went into the chute (or the whole pour when no sweeping was needed).
    var dispensed: Int { rest.map { poured - $0.finalCount } ?? poured }
    var difference: Int { dispensed - target }
    /// Finished and exactly on target.
    var exact: Bool { difference == 0 && (rest != nil || poured == target) }
    var latest: CountRecord { rest ?? pour }

    /// Groups the fill photos of a list of counts; counts that are not part of a fill are ignored.
    static func all(_ records: [CountRecord]) -> [String: FillSummary] {
        var byFill: [String: [CountRecord]] = [:]
        for r in records where !r.deleted { if let f = r.fillId { byFill[f, default: []].append(r) } }
        var out: [String: FillSummary] = [:]
        for (id, list) in byFill {
            guard let pour = list.filter({ $0.fillRole == "pour" }).max(by: { $0.date < $1.date }) else { continue }
            let rest = list.filter { $0.fillRole == "rest" }.max(by: { $0.date < $1.date })
            out[id] = FillSummary(id: id, target: pour.fillTarget, pour: pour, rest: rest)
        }
        return out
    }
}

/// What the next count belongs to, set before counting (all optional): the prescribed quantity, the Rx number and (from a scan in a
/// build before 0.18.2) the stock bottle. Cleared when a fill is finished. Kept in a protected file in Application Support/Eir (out of backups: an
/// Rx number does not belong in UserDefaults, which backups include) so it survives the screen being rebuilt.
final class FillDraft: ObservableObject {
    static let shared = FillDraft()
    @Published private(set) var target: Int?
    @Published private(set) var rxNumber: String?
    @Published private(set) var product: [String: Any]?
    private let url: URL

    /// `legacy`: where earlier builds kept the draft (UserDefaults); read once, then removed.
    init(url: URL = FileGuard.supportDirectory.appendingPathComponent("draft.json"), legacy: UserDefaults? = .standard) {
        self.url = url
        if let data = try? Data(contentsOf: url), let o = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] {
            let t = (o["target"] as? Int) ?? 0
            target = t > 0 ? t : nil
            rxNumber = o["rx"] as? String
            product = o["product"] as? [String: Any]
        } else if let d = legacy, d.object(forKey: "fill.rx") != nil || d.object(forKey: "fill.target") != nil || d.object(forKey: "fill.product") != nil {
            let t = d.integer(forKey: "fill.target")
            target = t > 0 ? t : nil
            rxNumber = d.string(forKey: "fill.rx")
            product = d.string(forKey: "fill.product").flatMap { $0.data(using: .utf8) }.flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] }
            persist()
        }
        if let d = legacy { for k in ["fill.target", "fill.rx", "fill.product"] { d.removeObject(forKey: k) } }
    }

    var isEmpty: Bool { target == nil && rxNumber == nil && product == nil }

    func set(target: Int?, rx: String?, product: [String: Any]?) {
        let t = target.flatMap { $0 > 0 ? $0 : nil }
        let r = rx?.trimmingCharacters(in: .whitespacesAndNewlines)
        self.target = t
        self.rxNumber = (r?.isEmpty ?? true) ? nil : r
        self.product = product
        persist()
        AppLog.event("fill_draft", ["target": t ?? 0, "rx": self.rxNumber != nil, "product": (product?["name"] as? String) ?? ""])
    }

    private func persist() {
        if isEmpty { try? FileManager.default.removeItem(at: url); return }
        var o: [String: Any] = ["target": target ?? 0]
        if let rxNumber { o["rx"] = rxNumber }
        if let product { o["product"] = product }
        guard let data = try? JSONSerialization.data(withJSONObject: o) else { return }
        FileGuard.prepare(url.deletingLastPathComponent(), protection: .completeUnlessOpen)
        try? data.write(to: url, options: FileGuard.write)
        FileGuard.excludeFromBackup(url)
    }

    func clear() { set(target: nil, rx: nil, product: nil) }
}
