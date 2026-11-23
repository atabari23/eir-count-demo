import Foundation

/// One detected centre in input-canvas pixels (the 1024 px letterboxed canvas).
struct PillPoint {
    let x: Float
    let y: Float
    let score: Float
    let trayProbability: Float
}

/// trayBox = [x0, y0, x1, y1] in canvas pixels of the largest tray component, or nil.
struct Decoded {
    let kept: [PillPoint]
    let rejected: [PillPoint]
    let trayBox: [Float]?
    let trayCells: Int
    /// Highest centre probability anywhere in the map (diagnostic: 0 means the model saw nothing pill-like).
    let topScore: Float
}

/// Port of scripts/postprocess.py decode_clean() (same as the Android PillDecoder.kt): centre peaks gated by the
/// largest 8-connected tray component, 3x3 local maxima at 0.3, 5x5 border tolerance at 0.5, centre = (cell + 0.5) * 4.
enum PillDecoder {
    static let threshold: Float = 0.3
    static let regionThreshold: Float = 0.5
    static let maxPeaks = 500

    @inline(__always) static func sigmoid(_ v: Float) -> Float { 1 / (1 + expf(-min(max(v, -80), 80))) }

    /// Max filter with an odd kernel; neighbours outside the map are ignored (equivalent to -inf padding).
    static func maxFilter(_ a: [Float], _ h: Int, _ w: Int, _ kernel: Int) -> [Float] {
        let r = kernel / 2
        var out = [Float](repeating: 0, count: h * w)
        a.withUnsafeBufferPointer { src in
            out.withUnsafeMutableBufferPointer { dst in
                for y in 0..<h {
                    let y0 = max(0, y - r), y1 = min(h - 1, y + r)
                    for x in 0..<w {
                        let x0 = max(0, x - r), x1 = min(w - 1, x + r)
                        var m = -Float.infinity
                        for dy in y0...y1 {
                            let row = dy * w
                            for dx in x0...x1 { m = max(m, src[row + dx]) }
                        }
                        dst[y * w + x] = m
                    }
                }
            }
        }
        return out
    }

    /// Largest 8-connected true region (row-major scan, first largest wins), as in postprocess.largest_component.
    static func largestComponent(_ mask: [Bool], _ h: Int, _ w: Int) -> [Bool] {
        var remaining = mask
        var best: [Int] = []
        var stack = [Int](repeating: 0, count: h * w)
        var component = [Int](repeating: 0, count: h * w)
        for start in 0..<(h * w) {
            if !remaining[start] { continue }
            remaining[start] = false
            var sp = 0
            stack[sp] = start; sp += 1
            var n = 0
            while sp > 0 {
                sp -= 1
                let c = stack[sp]
                component[n] = c; n += 1
                let cy = c / w, cx = c % w
                for ny in max(0, cy - 1)...min(h - 1, cy + 1) {
                    for nx in max(0, cx - 1)...min(w - 1, cx + 1) {
                        let idx = ny * w + nx
                        if remaining[idx] { remaining[idx] = false; stack[sp] = idx; sp += 1 }
                    }
                }
            }
            if n > best.count { best = Array(component[0..<n]) }
        }
        var result = [Bool](repeating: false, count: h * w)
        for i in best { result[i] = true }
        return result
    }

    /// centre / tray: stride-4 logit maps, row-major, h*w each. Returns centres in canvas pixels.
    static func decode(centre: [Float], tray: [Float], h: Int, w: Int) -> Decoded {
        let mask = largestComponent(tray.map { $0 >= 0 }, h, w)
        let cleaned: [Float] = mask.map { $0 ? 20 : -20 }
        let p = centre.map(sigmoid)
        let local = maxFilter(p, h, w, 3)
        let trayGate = maxFilter(cleaned.map(sigmoid), h, w, 5)
        let rawTray = maxFilter(tray.map(sigmoid), h, w, 5)
        var candidates: [Int] = []
        for i in 0..<(h * w) where p[i] == local[i] && p[i] >= threshold { candidates.append(i) }
        candidates.sort { p[$0] > p[$1] || (p[$0] == p[$1] && $0 < $1) }
        if candidates.count > maxPeaks { candidates.removeSubrange(maxPeaks...) }
        var kept: [PillPoint] = []
        var rejected: [PillPoint] = []
        for i in candidates {
            let y = i / w, x = i % w
            let point = PillPoint(x: (Float(x) + 0.5) * 4, y: (Float(y) + 0.5) * 4, score: p[i], trayProbability: rawTray[i])
            if trayGate[i] >= regionThreshold { kept.append(point) } else { rejected.append(point) }
        }
        var x0 = w, y0 = h, x1 = -1, y1 = -1, cells = 0
        for i in 0..<(h * w) where mask[i] {
            cells += 1
            let y = i / w, x = i % w
            if x < x0 { x0 = x }
            if x > x1 { x1 = x }
            if y < y0 { y0 = y }
            if y > y1 { y1 = y }
        }
        let box: [Float]? = x1 >= 0 ? [Float(x0) * 4, Float(y0) * 4, Float(x1 + 1) * 4, Float(y1 + 1) * 4] : nil
        return Decoded(kept: kept, rejected: rejected, trayBox: box, trayCells: cells, topScore: p.max() ?? 0)
    }
}
