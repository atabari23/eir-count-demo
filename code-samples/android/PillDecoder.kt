package com.asgard.pillcounter

import kotlin.math.exp
import kotlin.math.max
import kotlin.math.min

/** Port of scripts/postprocess.py decode_clean(): centre peaks gated by the largest connected tray component. */
data class Point(val x: Float, val y: Float, val score: Float, val trayProbability: Float)

/** trayBox = [x0, y0, x1, y1] in input-canvas pixels of the largest tray component, or null. overlap (0.19.1): OverlapCheck's spots for
 * the kept dots, computed in Counter.count when DecodeParams.overlap asks for them (photos only, never live frames); null otherwise. */
data class Decoded(val kept: List<Point>, val rejected: List<Point>, val trayBox: FloatArray? = null, val trayCells: Int = 0, val overlap: OverlapCheck.Result? = null)

/** Developer-option knobs of the decoder (v0.15). The defaults reproduce postprocess.decode_clean exactly:
 * centre probability >= 0.3, tray logit >= 0 (probability 0.5), 3x3 peak window. overlap (0.19.1): also run OverlapCheck on the
 * same logits after decoding (the photos of a count; the live view never asks). It does not change the decoded dots. */
data class DecodeParams(val dotThreshold: Float = 0.3f, val trayThreshold: Float = 0.5f, val peakKernel: Int = 3, val overlap: Boolean = false) {
    /** Tray probability -> logit, so the default 0.5 is exactly the v0.14 test `tray >= 0f`. */
    val trayLogit: Float get() = if (trayThreshold == 0.5f) 0f else Math.log(trayThreshold / (1.0 - trayThreshold)).toFloat()

    companion object {
        fun from(v: DevSettings.Values) = DecodeParams(v.num(DevSettings.DOT_THRESHOLD), v.num(DevSettings.TRAY_THRESHOLD), v.choice(DevSettings.PEAK_SPACING).toIntOrNull() ?: 3)
    }
}

object PillDecoder {
    private const val REGION_THRESHOLD = 0.5f
    private const val MAX_PEAKS = 500

    private fun sigmoid(v: Float): Float = 1f / (1f + exp(-v.coerceIn(-80f, 80f)))

    /** Max filter with the given odd kernel; out-of-range neighbours are ignored (equivalent to -inf padding). */
    private fun maxFilter(a: FloatArray, h: Int, w: Int, kernel: Int): FloatArray {
        val r = kernel / 2; val out = FloatArray(h * w)
        for (y in 0 until h) for (x in 0 until w) {
            var m = Float.NEGATIVE_INFINITY
            for (dy in max(0, y - r)..min(h - 1, y + r)) for (dx in max(0, x - r)..min(w - 1, x + r)) m = max(m, a[dy * w + dx])
            out[y * w + x] = m
        }
        return out
    }

    /** Largest 8-connected true region (row-major scan, first largest wins), as in postprocess.largest_component. */
    internal fun largestComponent(mask: BooleanArray, h: Int, w: Int): BooleanArray {
        val remaining = mask.copyOf(); var best = IntArray(0); val stack = IntArray(h * w); val component = IntArray(h * w)
        for (start in 0 until h * w) {
            if (!remaining[start]) continue
            remaining[start] = false; var sp = 0; stack[sp++] = start; var n = 0
            while (sp > 0) {
                val c = stack[--sp]; component[n++] = c; val cy = c / w; val cx = c % w
                for (ny in max(0, cy - 1)..min(h - 1, cy + 1)) for (nx in max(0, cx - 1)..min(w - 1, cx + 1)) {
                    val idx = ny * w + nx
                    if (remaining[idx]) { remaining[idx] = false; stack[sp++] = idx }
                }
            }
            if (n > best.size) best = component.copyOf(n)
        }
        val result = BooleanArray(h * w); for (i in best) result[i] = true
        return result
    }

    /** Max over the odd `kernel` window around cell i (same semantics as maxFilter, evaluated at one cell). */
    private inline fun windowMax(h: Int, w: Int, i: Int, kernel: Int, value: (Int) -> Float): Float {
        val r = kernel / 2; val y = i / w; val x = i % w; var m = Float.NEGATIVE_INFINITY
        for (dy in max(0, y - r)..min(h - 1, y + r)) for (dx in max(0, x - r)..min(w - 1, x + r)) m = max(m, value(dy * w + dx))
        return m
    }

    /** logits: [2][h][w] flattened per channel (centre, tray). Returns centres in input-canvas pixels. */
    fun decode(centre: FloatArray, tray: FloatArray, h: Int, w: Int, params: DecodeParams = DecodeParams(), probabilities: FloatArray? = null): Decoded {
        val n = h * w
        val trayLogit = params.trayLogit
        val mask = largestComponent(BooleanArray(n) { tray[it] >= trayLogit }, h, w)
        val p = probabilities?.also { for (i in 0 until n) it[i] = sigmoid(centre[i]) } ?: FloatArray(n) { sigmoid(centre[it]) }
        val local = maxFilter(p, h, w, params.peakKernel)
        // Candidate peaks first (few hundred at most); the 5x5 tray filters are evaluated only there, not over the whole map.
        var count = 0; val idx = IntArray(n)
        for (i in 0 until n) if (p[i] >= params.dotThreshold && p[i] == local[i]) { idx[count++] = i }
        val order = (0 until count).map { idx[it] }.sortedByDescending { p[it] }.take(MAX_PEAKS)
        val kept = ArrayList<Point>(); val rejected = ArrayList<Point>()
        for (i in order) {
            val y = i / w; val x = i % w
            val gate = windowMax(h, w, i, 5) { if (mask[it]) sigmoid(20f) else sigmoid(-20f) }
            val raw = windowMax(h, w, i, 5) { sigmoid(tray[it]) }
            val point = Point((x + 0.5f) * 4f, (y + 0.5f) * 4f, p[i], raw)
            if (gate >= REGION_THRESHOLD) kept.add(point) else rejected.add(point)
        }
        var x0 = w; var y0 = h; var x1 = -1; var y1 = -1
        for (i in 0 until h * w) if (mask[i]) { val y = i / w; val x = i % w; if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y }
        val box = if (x1 >= 0) floatArrayOf(x0 * 4f, y0 * 4f, (x1 + 1) * 4f, (y1 + 1) * 4f) else null
        return Decoded(kept, rejected, box, mask.count { it })
    }
}
