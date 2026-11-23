package com.asgard.pillcounter

import kotlin.math.floor
import kotlin.math.max
import kotlin.math.min
import kotlin.math.sqrt

/**
 * Blur check (0.19.1, round06 capture checks): the width of pill edges in the worst direction, in pixels of the 1280 px preview,
 * median over up to 64 pills. Port of `blur_width` and `blur_decision` in scripts/capture_checks.py (constants there, study in
 * reports/round06/capture-checks/SPEC.md). Higher = blurrier: a sharp edge is about 2-3 px wide, motion blur of L px about L.
 *
 * Pure (no Android types): CaptureChecksGoldenTest runs it on the golden vectors. The grey values are the ones Images.sharpness and
 * PillFocus use, (3 R + 6 G + B) / 10 with integer division, so every derivative is an integer: the percentiles and the top-20
 * sums are taken from 256-bin histograms, which gives the same numbers as sorting (`sorted[int(q * (n - 1))]`) without sorting.
 * `Images.sharpness` and `PillFocus` stay as they are (logging and the live gate); they do not decide anything here.
 */
object BlurCheck {
    const val WINDOW_R = 48          // half window around each dot (whole pill inside: pills are 12-80 px at 1280)
    const val TOPK = 20              // edge strength per direction = mean of the 20 largest steps
    const val MIN_CONTRAST = 25      // windows whose p98 - p2 grey range is below this have no pill edge: skipped
    const val MAX_DOTS = 64          // at most 64 dots, evenly spread over the decoder's order
    const val MIN_WINDOWS = 1        // fewer usable windows: no measurement (never refuse on that)
    const val DENSE_N = 25           // blur only changes counts of photos with 25+ dots
    const val REFUSE = 6.0           // 25+ dots: retake at or above this width
    const val CHECK = 4.5            // 25+ dots: [4.5, 6.0) is borderline, "Please check"
    const val REFUSE_ANY = 9.0       // any count: retake at or above this
    const val CHECK_ANY = 6.0        // fewer than 25 dots: [6.0, 9.0) is borderline

    enum class Decision(val key: String) { OK("ok"), CHECK("check"), REFUSE("refuse") }

    /** value: the photo's edge width (null when no window could be measured); windows: windows used; widths: per used window, the
     * widths along 0, 45, 90 and 135 degrees (in that order, as the Python reference returns them). */
    class Measure(val value: Double?, val windows: Int, val widths: List<DoubleArray>)

    /** grey: w x h row-major grey values (0-255) of the 1280 px preview; dots: centres in preview pixels, in the decoder's order. */
    fun width(grey: IntArray, w: Int, h: Int, dots: List<FloatArray>): Pair<Double?, Int> = measure(grey, w, h, dots).let { Pair(it.value, it.windows) }

    fun measure(grey: IntArray, w: Int, h: Int, dots: List<FloatArray>): Measure {
        val n = dots.size; val r = WINDOW_R
        // The reference needs the window and its 1-px border inside the picture; the app's previews are 1280 x 960.
        if (n == 0 || w < 2 * r + 3 || h < 2 * r + 3 || grey.size < w * h) return Measure(null, 0, emptyList())
        val idx = if (n <= MAX_DOTS) IntArray(n) { it } else IntArray(MAX_DOTS) { i -> floor((i * (n - 1)).toDouble() / (MAX_DOTS - 1) + 0.5).toInt() }
        val side = 2 * r + 1; val m = side * side
        val loRank = (0.02 * (m - 1)).toInt(); val hiRank = (0.98 * (m - 1)).toInt()
        val hist = IntArray(256); val h0 = IntArray(256); val h45 = IntArray(256); val h90 = IntArray(256); val h135 = IntArray(256)
        val diag = 2.0 * sqrt(2.0)
        val widths = ArrayList<DoubleArray>(); val worst = ArrayList<Double>()
        for (i in idx) {
            var x = dots[i][0].toInt(); var y = dots[i][1].toInt()   // truncation toward zero, as the reference
            x = min(max(x, r + 1), w - r - 2); y = min(max(y, r + 1), h - r - 2)
            hist.fill(0)
            for (yy in y - r..y + r) { val row = yy * w; for (xx in x - r..x + r) hist[grey[row + xx]]++ }
            val c = (rank(hist, hiRank) - rank(hist, loRank)).toDouble()
            if (c < MIN_CONTRAST) continue
            h0.fill(0); h45.fill(0); h90.fill(0); h135.fill(0)
            for (yy in y - r..y + r) {
                val row = yy * w; val up = row - w; val down = row + w
                for (xx in x - r..x + r) {
                    h0[abs(grey[row + xx + 1] - grey[row + xx - 1])]++
                    h90[abs(grey[down + xx] - grey[up + xx])]++
                    h45[abs(grey[up + xx + 1] - grey[down + xx - 1])]++
                    h135[abs(grey[down + xx + 1] - grey[up + xx - 1])]++
                }
            }
            val wd = doubleArrayOf(
                c / max(topSum(h0) / 2.0 / TOPK, 1e-3), c / max(topSum(h45) / diag / TOPK, 1e-3),
                c / max(topSum(h90) / 2.0 / TOPK, 1e-3), c / max(topSum(h135) / diag / TOPK, 1e-3))
            widths.add(wd); worst.add(wd.max())
        }
        if (worst.size < MIN_WINDOWS) return Measure(null, worst.size, widths)
        return Measure(median(worst), worst.size, widths)
    }

    /** Retake, borderline ("Please check") or fine. nDots: the decoder's count for this photo. No measurement is never a retake. */
    fun decision(value: Double?, nDots: Int): Decision {
        if (value == null) return Decision.OK
        val dense = nDots >= DENSE_N
        if (value >= REFUSE_ANY || (dense && value >= REFUSE)) return Decision.REFUSE
        if (value >= CHECK_ANY || (dense && value >= CHECK)) return Decision.CHECK
        return Decision.OK
    }

    private fun abs(v: Int) = if (v < 0) -v else v

    /** The k-th smallest value (0-based) of the values counted in `hist`. */
    private fun rank(hist: IntArray, k: Int): Int {
        var seen = 0
        for (v in 0 until 256) { seen += hist[v]; if (seen > k) return v }
        return 255
    }

    /** Sum of the TOPK largest values counted in `hist` (each value is an integer step 0-255). */
    private fun topSum(hist: IntArray): Double {
        var left = TOPK; var sum = 0L
        for (v in 255 downTo 0) { if (left == 0) break; val take = min(left, hist[v]); sum += take.toLong() * v; left -= take }
        return sum.toDouble()
    }

    /** Median: the middle of the sorted values, the mean of the two middle ones for an even count (as the reference). */
    internal fun median(values: List<Double>): Double {
        val s = values.sorted(); val n = s.size
        return if (n % 2 == 1) s[n / 2] else (s[n / 2 - 1] + s[n / 2]) / 2.0
    }
}
