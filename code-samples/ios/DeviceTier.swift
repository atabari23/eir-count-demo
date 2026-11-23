import CoreML
import Foundation
import UIKit

/// How much live work this iPhone can take (port of Android's DeviceTier, v0.17.1-v0.17.3).
///
/// Nobody here can measure an iPhone 11 (A13) or 12 (A14): on the A15 the model takes 32 ms on the Neural Engine. So
/// nothing is assumed: on the first run, and again after every app update or iOS update, the model is timed on this
/// phone (a warm run on a flat grey frame, median of three). A phone where it is quick (FAST) gets live dots; any other
/// phone gets light mode, where nothing runs continuously: the model runs only when the picture has been steady for the
/// hold time, and after a failed check it waits for the picture to move before it looks again.
///
/// Compute units (0.18.1, store-readiness review §2.3 #3): the Neural Engine alone is tried first (the fastest unit for this network on
/// every A12 and later, and the only choice with the 150 ms allowance), then Core ML's own choice, then CPU+GPU, stopping at the first
/// FAST one; the fastest is kept (light mode runs the model rarely, so the GPU's UI cost does not matter). Each candidate is a full
/// Core ML load and compile (5-10 s on an older chip), so a phone that is FAST on the Neural Engine pays one instead of up to three.
/// The result, with every candidate's timing, goes to the log (`device_tier` event) for scripts/iphone_log.py.
enum DeviceTier {
    enum Tier: String { case fast, slow }

    /// A live analysis at most this long (warm, ms) counts as fast on any compute units...
    static let fastMs = 100
    /// ...and up to this long when the Neural Engine may be doing the work (the A15 measured 32 ms).
    static let fastNeuralMs = 150

    /// Fast = quick enough for a live view paced at four analyses a second without the phone doing nothing else.
    static func decide(units: ComputeChoice, warmMs: Int) -> Tier {
        guard warmMs > 0 else { return .slow }
        if warmMs <= fastMs { return .fast }
        return units.usesNeuralEngine && warmMs <= fastNeuralMs ? .fast : .slow
    }

    struct Candidate: Codable, Equatable {
        var units: String
        var warmMs: Int
        var loadMs: Int
    }

    struct Result: Codable, Equatable {
        var tier: String
        var units: String
        var warmMs: Int
        var candidates: [Candidate]
        var model: String
        var device: String
        var ios: String
        var app: String
        var measuredAt: Date
    }

    /// The candidate to keep: the lowest warm time (ties: the earlier one, which is Core ML's own choice).
    static func best(_ candidates: [Candidate]) -> Candidate? {
        candidates.filter { $0.warmMs > 0 }.min { $0.warmMs < $1.warmMs }
    }

    /// One saved measurement per app build, iOS version, model and phone: an update or a new model measures again.
    static func cacheKey(app: String, ios: String, model: String, device: String) -> String { "tier|\(app)|\(ios)|\(model)|\(device)" }

    private static let defaults = UserDefaults.standard

    static func currentKey(model: String) -> String {
        let info = Bundle.main.infoDictionary ?? [:]
        let app = "\(info["CFBundleShortVersionString"] ?? "?")(\(info["CFBundleVersion"] ?? "?"))"
        return cacheKey(app: app, ios: UIDevice.current.systemVersion, model: model, device: AppLog.hardwareModel())
    }

    static func saved(model: String) -> Result? {
        guard let data = defaults.data(forKey: currentKey(model: model)) else { return nil }
        return try? JSONDecoder.iso.decode(Result.self, from: data)
    }

    static func save(_ r: Result) {
        if let data = try? JSONEncoder.iso.encode(r) { defaults.set(data, forKey: currentKey(model: r.model)) }
    }

    /// Forget the saved measurement (Developer options: measure again).
    static func forget(model: String) { defaults.removeObject(forKey: currentKey(model: model)) }

    /// The one-time explanation of light mode was shown on this phone.
    static var noticeShown: Bool {
        get { defaults.bool(forKey: "lightNoticeShown") }
        set { defaults.set(newValue, forKey: "lightNoticeShown") }
    }

    /// Median of three warm runs (after one run that may include late compilation) of `model` on a grey frame.
    static func warmMs(of model: CounterModel) -> Int {
        guard let frame = CounterModel.greyFrame() else { return -1 }
        _ = try? model.count(frame)
        var times: [Int] = []
        for _ in 0..<3 { if let r = try? model.count(frame) { times.append(max(1, r.runMs)) } }
        guard !times.isEmpty else { return -1 }
        return times.sorted()[times.count / 2]
    }

    /// The bundled sample tray counts 88 on the Mac (90 labelled). A compute unit that is fast but wrong (seen: the simulator's
    /// GPU path counts 500) must never be chosen, so each candidate also counts this photo and must land close.
    static let probeRange = 80...96

    static func probeImage() -> CGImage? {
        Bundle.main.url(forResource: "sample", withExtension: "jpg").flatMap { Images.loadOriented(url: $0, longSide: 1280) }
    }

    static func probeCount(_ model: CounterModel, image: CGImage?) -> Int? {
        guard let image, let r = try? model.count(image) else { return nil }
        return r.decoded.kept.count
    }

    /// Only one timing or load runs at a time in the whole process (review H2): two screens timing at once slow each other down
    /// and the slower result would be cached for the next update. The second caller waits, then reuses the first one's result.
    static let measureLock = NSLock()      // internal: tests hold it so the host app's own start-up timing cannot interleave

    /// The loaded counter for this phone: the saved measurement when there is one, else a timing. `forced` is a Developer choice.
    /// `background` says whether the app left the screen at any time since the timing began (see `isUnreliable`).
    static func loadOrMeasure(provider: ModelProvider = ModelProviders.current, forced: ComputeChoice? = nil, background: () -> Bool = { false }, progress: (() -> Void)? = nil) throws -> (CounterModel, Result) {
        measureLock.lock()
        defer { measureLock.unlock() }
        if forced == nil, let saved = saved(model: provider.name), let units = ComputeChoice.allCases.first(where: { $0.logName == saved.units }),
           let m = try? CounterModel(requested: units, provider: provider) {
            if m.choice == units { return (m, saved) }
            m.close()                                    // the units that were fastest no longer load: time again
            forget(model: provider.name)
        }
        progress?()
        return try measure(provider: provider, forced: forced, background: background)
    }

    /// A SLOW result that may only be bad luck (the phone is warm or in Low Power Mode, the app was in the background during the timing,
    /// or it is within 1.5x of the threshold) is not cached: the next launch times again, at most this many times (review M2: one slow
    /// timing used to stick until the next update). Once since 0.18.1: each timing is a full load and compile on an older chip, and a
    /// phone timed SLOW by bad luck is still promoted at run time when its light-mode checks come in quick (`shouldPromote`).
    static let maxRetimings = 1

    /// Whether a slow measurement should not be trusted yet. `background`: the app left the screen while the model was timed (the
    /// timing can run during the guide, 0.18.1), when iOS may slow or pause it.
    static func isUnreliable(tier: Tier, units: ComputeChoice, warmMs: Int, thermal: Int, lowPower: Bool, background: Bool = false) -> Bool {
        guard tier == .slow else { return false }
        let threshold = units.usesNeuralEngine ? fastNeuralMs : fastMs
        return thermal >= 1 || lowPower || background || (warmMs > 0 && Double(warmMs) <= 1.5 * Double(threshold))
    }

    /// The order the compute units are timed in on a first start (a Developer choice times only that one).
    static func candidateOrder(forced: ComputeChoice?) -> [ComputeChoice] { forced.map { [$0] } ?? [.neuralEngine, .auto, .gpu] }

    private static func attemptsKey(_ model: String) -> String { "tierAttempts|" + currentKey(model: model) }

    /// The threshold in ms that makes `units` FAST.
    static func threshold(for units: ComputeChoice) -> Int { units.usesNeuralEngine ? fastNeuralMs : fastMs }

    /// Times the model on this phone, keeps the faster compute units and returns the loaded counter with the saved
    /// measurement. Slow: call off the main thread. `forced` (Developer options) skips the timing of other candidates.
    /// Candidates (`candidateOrder`): the Neural Engine alone, then Core ML's own choice, then the GPU, stopping at the first quick one.
    /// Only the best counter so far stays loaded.
    static func measure(provider: ModelProvider = ModelProviders.current, forced: ComputeChoice? = nil, probe: Bool = true, progress: ((String) -> Void)? = nil,
                        thermal: Int = ProcessInfo.processInfo.thermalState.rawValue, lowPower: Bool = ProcessInfo.processInfo.isLowPowerModeEnabled,
                        background: () -> Bool = { false }, timer: ((CounterModel) -> Int)? = nil) throws -> (CounterModel, Result) {
        var candidates: [Candidate] = []
        var best: (Candidate, CounterModel)?
        let order = candidateOrder(forced: forced)
        func consider(_ m: CounterModel, loadMs: Int, requested: ComputeChoice, check: Bool) {
            var warm = timer?(m) ?? warmMs(of: m)        // `timer`: tests stand in for the clock
            if check, probe, let n = probeCount(m, image: probeImage()), !probeRange.contains(n) {
                AppLog.event("device_tier_rejected", ["units": m.choice.logName, "probe_count": n, "warm_ms": warm])
                warm = -1          // fast but wrong: never chosen
            }
            let c = Candidate(units: m.choice.logName, warmMs: warm, loadMs: loadMs)
            candidates.append(c)
            AppLog.event("device_tier_candidate", ["units": c.units, "requested": requested.logName, "warm_ms": warm, "load_ms": loadMs])
            if warm > 0, best == nil || warm < best!.0.warmMs { best?.1.close(); best = (c, m) } else { m.close() }   // free the loser at once
        }
        for choice in order {
            progress?(choice.logName)
            let t0 = Date()
            guard let m = try? CounterModel(requested: choice, provider: provider) else { continue }
            let loadMs = Int(Date().timeIntervalSince(t0) * 1000)
            // The same units again (a request that fell back to one already timed) is not timed twice.
            if candidates.contains(where: { $0.units == m.choice.logName }) { m.close(); continue }
            consider(m, loadMs: loadMs, requested: choice, check: true)
            // A run that is already quick: no need to time the rest.
            if forced == nil, let b = best, decide(units: b.1.choice, warmMs: b.0.warmMs) == .fast { break }
        }
        if best == nil, forced != .cpu, !candidates.contains(where: { $0.units == ComputeChoice.cpu.logName }) {
            // Nothing else gave a sane answer: the CPU always does.
            let t0 = Date()
            if let m = try? CounterModel(requested: .cpu, provider: provider) { consider(m, loadMs: Int(Date().timeIntervalSince(t0) * 1000), requested: .cpu, check: false) }
        }
        guard let (top, winner) = best else {
            throw NSError(domain: "PillCounter", code: 20, userInfo: [NSLocalizedDescriptionKey: "the counter could not be timed"])
        }
        let info = Bundle.main.infoDictionary ?? [:]
        let tier = decide(units: winner.choice, warmMs: top.warmMs)
        let r = Result(tier: tier.rawValue, units: winner.choice.logName, warmMs: top.warmMs, candidates: candidates, model: provider.name,
                       device: AppLog.hardwareModel(), ios: UIDevice.current.systemVersion, app: "\(info["CFBundleShortVersionString"] ?? "?")(\(info["CFBundleVersion"] ?? "?"))", measuredAt: Date())
        let attemptsKey = attemptsKey(provider.name)
        let attempts = defaults.integer(forKey: attemptsKey)
        let away = background()
        let unreliable = forced == nil && isUnreliable(tier: tier, units: winner.choice, warmMs: top.warmMs, thermal: thermal, lowPower: lowPower, background: away) && attempts < maxRetimings
        if unreliable { defaults.set(attempts + 1, forKey: attemptsKey) } else { save(r); defaults.removeObject(forKey: attemptsKey) }
        AppLog.event("device_tier", ["provider": "Core ML", "units": r.units, "warm_ms": r.warmMs, "tier": r.tier, "candidates": candidates.map { "\($0.units)=\($0.warmMs)ms" }.joined(separator: " "),
                                     "device": r.device, "ios": r.ios, "cores": ProcessInfo.processInfo.activeProcessorCount, "ram_mb": Int(ProcessInfo.processInfo.physicalMemory / 1_048_576),
                                     "low_power": lowPower, "thermal": thermal, "background": away, "cached": !unreliable, "attempts": attempts + (unreliable ? 1 : 0)])
        return (winner, r)
    }

    /// A SLOW phone whose light-mode checks keep coming in under the threshold (the timing was unlucky) becomes FAST at run time.
    static func shouldPromote(tier: Tier, units: ComputeChoice, recentMs: [Int64]) -> Bool {
        guard tier == .slow, recentMs.count >= 5 else { return false }
        let median = Int(recentMs.sorted()[recentMs.count / 2])
        return median > 0 && decide(units: units, warmMs: median) == .fast
    }

    /// The saved measurement, with its tier changed (a promotion).
    static func savePromoted(model: String) {
        guard var r = saved(model: model) else { return }
        r.tier = Tier.fast.rawValue
        save(r)
    }
}

/// How the live view behaves, from the speed tier, the Developer option and the phone's heat.
enum LiveMode: String { case auto, full, light }

enum LiveModeChoice {
    /// Light mode now: chosen in Developer options, or Auto on a slow phone, or any phone in critical thermal state.
    static func isLight(option: LiveMode, tier: DeviceTier.Tier, thermal: Int) -> Bool {
        switch option {
        case .light: return true
        case .full: return false
        case .auto: return tier == .slow || thermal >= FramePacer.critical
        }
    }
}
