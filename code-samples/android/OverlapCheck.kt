package com.asgard.pillcounter

import kotlin.math.ceil
import kotlin.math.exp
import kotlin.math.hypot
import kotlin.math.max
import kotlin.math.min
import kotlin.math.sqrt

/**
 * Overlap spots (0.19.1, round06 capture checks): places where the model's centre heat is left over after every counted dot has
 * explained its own blob, so a pill probably merged with, pressed against or is hidden by a neighbour. Port of `overlap_spots` and
 * `isolated_sigma` in scripts/capture_checks.py (constants there; study and decisions in reports/round06/capture-checks/SPEC.md).
 *
 * Inputs are exactly what PillDecoder.decode receives (the two 256 x 256 logit maps) and the decoder's kept dots in canvas pixels
 * (the 1024 letterbox). Spot coordinates are canvas pixels too: cell c has its centre at (c + 0.5) x 4.
 * Tiers: FAR (6+ cells from the nearest dot, R >= 0.18) and NEAR (1.5 to 6 cells, R >= 0.45). Only FAR spots are ever shown to the
 * user (in the "check" mode); NEAR spots are logged only (7 of 21 correct dense photos were falsely flagged by them).
 * Pure (no Android types): CaptureChecksGoldenTest runs it on the golden vectors. Runs in Counter.count right after the decoder.
 */
object OverlapCheck {
    const val ISO_NN = 6.0           // a dot is "isolated" when its nearest other dot is 6+ cells away
    const val SIGMA_DEFAULT = 1.6    // single-pill blob sigma (cells) when fewer than 2 isolated dots
    const val TEMPLATE_R = 3.5       // template radius = ceil(3.5 sigma) cells
    const val MIN_D = 1.5            // candidate at least 1.5 cells from the nearest counted dot
    const val FAR_D = 6.0            // 6+ cells away: the far tier
    const val FAR_R = 0.18           // residual needed in the far tier
    const val NEAR_R = 0.45          // residual needed in the near tier
    const val EDGE = 3               // candidate at least 3 cells inside the tray component (chessboard distance)
    const val MERGE_PX = 12.0        // spots closer than 12 canvas px are one spot
    const val MAX_SPOTS = 12         // at most 12 spots, strongest first

    enum class Tier(val key: String) { FAR("far"), NEAR("near") }

    /** x, y: canvas pixels; r: leftover heat; d: cells to the nearest counted dot. */
    class Spot(val x: Float, val y: Float, val r: Double, val d: Double, val tier: Tier)

    /** ms: how long the check took (set by the caller that timed it; 0 when not timed). */
    class Result(val spots: List<Spot>, val sigma: Double, val templateR: Int) { @Volatile var ms: Long = 0 }

    /** Developer option "Overlap check" (DevSettings.OVERLAP): off (not computed), log only (computed and logged, never shown),
     * check (the default: FAR spots are circled and make the count "Please check"). */
    enum class Mode(val key: String) {
        OFF("off"), LOG("log"), CHECK("check");
        companion object { fun of(key: String?): Mode = values().firstOrNull { it.key == key } ?: CHECK }
    }

    fun spots(centre: FloatArray, tray: FloatArray, h: Int = 256, w: Int = 256, kept: List<Point>): List<Spot> = analyse(centre, tray, h, w, kept).spots

    fun analyse(centre: FloatArray, tray: FloatArray, h: Int, w: Int, kept: List<Point>): Result {
        val n = h * w
        val p = DoubleArray(n) { 1.0 / (1.0 + exp(-centre[it].toDouble().coerceIn(-80.0, 80.0))) }
        val comp = PillDecoder.largestComponent(BooleanArray(n) { tray[it] >= 0f }, h, w)
        val gate = dilate(dilate(comp, h, w), h, w)                           // 5 x 5 dilation = the decoder's tray gate
        // Chessboard distance to the nearest non-tray cell, capped at 8; cells outside the map count as tray.
        val edge = IntArray(n); var cur = comp
        repeat(8) {
            for (i in 0 until n) if (cur[i]) edge[i]++
            cur = erode(cur, h, w)
        }
        val k = kept.size
        val kx = IntArray(k) { Math.rint(kept[it].x / 4.0 - 0.5).toInt() }    // Python round(): half to even (cell centres are exact)
        val ky = IntArray(k) { Math.rint(kept[it].y / 4.0 - 0.5).toInt() }
        val ks = DoubleArray(k) { kept[it].score.toDouble() }
        val sigma = isolatedSigma(p, h, w, kx, ky)
        val tr = ceil(TEMPLATE_R * sigma).toInt()
        val t = DoubleArray(n)
        for (j in 0 until k) {                                                // template = max over the dots' Gaussians
            val cx = kx[j]; val cy = ky[j]; val sc = ks[j]
            for (y in max(0, cy - tr) until min(h, cy + tr + 1)) for (x in max(0, cx - tr) until min(w, cx + tr + 1)) {
                val dx = (x - cx).toDouble(); val dy = (y - cy).toDouble()
                val v = sc * exp(-(dx * dx + dy * dy) / (2.0 * sigma * sigma))
                if (v > t[y * w + x]) t[y * w + x] = v
            }
        }
        val res = DoubleArray(n) { p[it] - t[it] }
        class Cand(val v: Double, val y: Int, val x: Int, val d: Double, val tier: Tier)
        val cands = ArrayList<Cand>()
        for (y in 0 until h) for (x in 0 until w) {
            val i = y * w + x; val v = res[i]
            if (v < FAR_R || !gate[i] || edge[i] < EDGE) continue
            var local = Double.NEGATIVE_INFINITY                               // 3 x 3 max; out-of-range neighbours ignored
            for (yy in max(0, y - 1)..min(h - 1, y + 1)) for (xx in max(0, x - 1)..min(w - 1, x + 1)) local = max(local, res[yy * w + xx])
            if (v < local) continue
            var d = 1e9
            for (j in 0 until k) d = min(d, hypot((kx[j] - x).toDouble(), (ky[j] - y).toDouble()))
            if (d < MIN_D) continue
            val tier = if (d >= FAR_D) Tier.FAR else Tier.NEAR
            if (tier == Tier.NEAR && v < NEAR_R) continue
            cands.add(Cand(v, y, x, d, tier))
        }
        cands.sortWith(compareBy<Cand>({ -it.v }, { it.y }, { it.x }))          // strongest first, then row-major
        val out = ArrayList<Spot>()
        for (c in cands) {
            val px = (c.x + 0.5) * 4.0; val py = (c.y + 0.5) * 4.0
            if (out.all { hypot(px - it.x, py - it.y) > MERGE_PX }) out.add(Spot(px.toFloat(), py.toFloat(), c.v, c.d, c.tier))
            if (out.size >= MAX_SPOTS) break
        }
        return Result(out, sigma, tr)
    }

    /** Single-pill blob size of this photo (cells): for each dot whose nearest other dot is 6+ cells away and whose 9 x 9 window is
     * inside the map, sqrt(sum((dx^2 + dy^2) w) / sum(w) / 2) with w = max(0, p / p_dot - 0.05). Median of those when there are at
     * least 2, else 1.6. */
    internal fun isolatedSigma(p: DoubleArray, h: Int, w: Int, kx: IntArray, ky: IntArray): Double {
        val n = kx.size
        if (n < 2) return SIGMA_DEFAULT
        val out = ArrayList<Double>()
        for (i in 0 until n) {
            val cx = kx[i]; val cy = ky[i]; var nn = 1e9
            for (j in 0 until n) if (j != i) nn = min(nn, hypot((kx[j] - cx).toDouble(), (ky[j] - cy).toDouble()))
            if (nn < ISO_NN || !(cx >= 4 && cx < w - 4 && cy >= 4 && cy < h - 4)) continue
            val pk = max(p[cy * w + cx], 1e-6); var num = 0.0; var den = 0.0
            for (dy in -4..4) for (dx in -4..4) {
                val wt = p[(cy + dy) * w + cx + dx] / pk - 0.05
                if (wt > 0) { num += (dx * dx + dy * dy) * wt; den += wt }
            }
            if (den > 0) out.add(sqrt(num / den / 2.0))
        }
        return if (out.size >= 2) BlurCheck.median(out) else SIGMA_DEFAULT
    }

    /** 3 x 3 dilation, cells outside the map are empty. */
    private fun dilate(a: BooleanArray, h: Int, w: Int): BooleanArray = BooleanArray(h * w) { i ->
        val y = i / w; val x = i % w; var any = false
        for (yy in max(0, y - 1)..min(h - 1, y + 1)) for (xx in max(0, x - 1)..min(w - 1, x + 1)) if (a[yy * w + xx]) { any = true }
        any
    }

    /** 3 x 3 erosion, cells outside the map count as set. */
    private fun erode(a: BooleanArray, h: Int, w: Int): BooleanArray = BooleanArray(h * w) { i ->
        val y = i / w; val x = i % w; var all = true
        for (yy in max(0, y - 1)..min(h - 1, y + 1)) for (xx in max(0, x - 1)..min(w - 1, x + 1)) if (!a[yy * w + xx]) { all = false }
        all
    }
}
