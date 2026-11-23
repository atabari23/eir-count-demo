package com.asgard.pillcounter

import android.content.Context
import android.graphics.Bitmap
import android.graphics.Color
import android.os.Build

/**
 * How much live work this phone can take. Phones with a supported AI chip (Qualcomm Hexagon NPU through QNN) run the
 * model in about 50 ms and get live dots while aiming. Others (Google Tensor, Exynos, MediaTek, older Snapdragons) run it
 * on the graphics chip, which also draws the phone's screen: running the model there several times a second made a Pixel 6
 * lag badly (2026-09-27). Those phones get light mode: the model runs only when the phone is held still.
 *
 * v0.18.1 (store-readiness review §2.3 #1-#2, the iPhone's rules): a SLOW timing that may only be bad luck (the phone was warm or
 * in battery saver, or the time is within 1.5x of the line) is not kept; the next start times again, at most MAX_RETIMINGS times.
 * A SLOW phone whose light-mode checks keep coming in under the line becomes FAST while it runs (and stays so). A Qualcomm NPU
 * slower than NPU_SLOW_MS is dropped for the graphics chip (the razr+ 2024's took 3.3 s against 0.35 s on its GPU), and that is
 * remembered for the phone's chip until the next app update.
 */
object DeviceTier {
    enum class Tier { FAST, SLOW }

    /** A live analysis at most this long (warm, ms) counts as fast. */
    const val FAST_MS = 150L
    /** ...but only on the NPU; on any other engine only when it is clearly quick (a future fast GPU; the Pixel 6 lesson). */
    const val FAST_OTHER_MS = 100L
    /** A Qualcomm NPU slower than this (warm) is not used: the graphics chip counts faster (review §2.3 #1). */
    const val NPU_SLOW_MS = 400L
    /** An unreliable SLOW timing is not kept this many times; the next one is kept whatever it says. */
    const val MAX_RETIMINGS = 3
    /** Light-mode checks looked at for a promotion to FAST, and how many are needed at least. */
    const val PROMOTION_WINDOW = 10
    const val PROMOTION_MIN = 5

    private fun isNpu(provider: String) = provider.startsWith("QNN")

    /** The line for this engine: 150 ms on the NPU, 100 ms on anything else. */
    fun threshold(provider: String): Long = if (isNpu(provider)) FAST_MS else FAST_OTHER_MS

    /** Fast = the NPU engine and a quick warm run; any other engine only when it is clearly quick (a future fast GPU). */
    fun decide(provider: String, warmMs: Long): Tier =
        if (warmMs in 1..threshold(provider)) Tier.FAST else Tier.SLOW

    /** A SLOW timing that should not be kept yet: taken while the phone was warm (thermal status 1 or more) or in battery saver, or
     * within 1.5x of the line (bad luck, or the photo engine still settling). The next start times again. */
    fun isUnreliable(tier: Tier, provider: String, warmMs: Long, thermal: Int, powerSave: Boolean): Boolean {
        if (tier != Tier.SLOW) return false
        return thermal >= 1 || powerSave || (warmMs > 0 && warmMs <= threshold(provider) * 3 / 2)
    }

    /** Whether a timing is kept for later starts: reliable ones always, unreliable ones only after MAX_RETIMINGS that were not. */
    fun keep(unreliable: Boolean, attemptsSoFar: Int): Boolean = !unreliable || attemptsSoFar >= MAX_RETIMINGS

    /** A SLOW phone whose recent light-mode checks (newest last) have a median under the line becomes FAST while it runs. */
    fun shouldPromote(tier: Tier, provider: String, recentMs: List<Long>): Boolean {
        if (tier != Tier.SLOW) return false
        val window = recentMs.takeLast(PROMOTION_WINDOW).filter { it > 0 }
        if (window.size < PROMOTION_MIN) return false
        return decide(provider, window.sorted()[window.size / 2]) == Tier.FAST
    }

    /** The Qualcomm NPU is slower than the graphics chip would be: use the graphics chip for everything (review §2.3 #1). */
    fun npuTooSlow(provider: String, warmMs: Long): Boolean = isNpu(provider) && warmMs > NPU_SLOW_MS

    /** A slow NPU is remembered for the chip, except when the timing could be heat: the phone was warm and it is within 1.5x of the line. */
    fun rememberSlowNpu(warmMs: Long, thermal: Int): Boolean = thermal < 1 || warmMs > NPU_SLOW_MS * 3 / 2

    /** false when the phone's chip is known not to be a Qualcomm Snapdragon (then the NPU attempt is skipped); null if unknown. */
    fun qualcommSoc(): Boolean? = if (Build.VERSION.SDK_INT >= 31) Build.SOC_MANUFACTURER.let { it.equals("QTI", true) || it.contains("qualcomm", true) } else null

    /** The phone's chip, as Android names it ("QTI SM8635"), for the logs and the slow-NPU memory. */
    fun soc(): String = if (Build.VERSION.SDK_INT >= 31) "${Build.SOC_MANUFACTURER} ${Build.SOC_MODEL}" else Build.HARDWARE ?: "?"  // never null on a phone; null in JVM tests

    private fun prefs(c: Context) = c.getSharedPreferences("eir_device", Context.MODE_PRIVATE)

    private fun key(engine: Counter) = "warm|${engine.provider}|${engine.asset}|${BuildConfig.VERSION_CODE}"

    /** A kept timing for this engine on this app version: (warm ms, tier), or null when it must be timed (again). */
    fun saved(context: Context, engine: Counter): Pair<Long, Tier>? {
        val p = prefs(context)
        val ms = p.getLong(key(engine), -1L).takeIf { it > 0 } ?: return null
        val tier = runCatching { Tier.valueOf(p.getString(key(engine) + "|tier", null) ?: "") }.getOrNull() ?: decide(engine.provider, ms)
        return Pair(ms, tier)
    }

    /** Warm analysis time of this engine on this phone: two runs on a grey frame (the first can include late compilation). Slow:
     * call off the main thread, after the photo engine has finished compiling (review §2.3 #2c). */
    fun measure(engine: Counter): Long {
        val frame = Bitmap.createBitmap(1280, 960, Bitmap.Config.ARGB_8888).apply { eraseColor(Color.rgb(44, 143, 184)) }
        engine.count(frame)
        return engine.count(frame).runMs.coerceAtLeast(1)
    }

    /** Keeps (or, while it may be bad luck, does not keep) a timing. Returns (kept, attempts so far) for the log. */
    fun record(context: Context, engine: Counter, warmMs: Long, tier: Tier, thermal: Int, powerSave: Boolean): Pair<Boolean, Int> {
        val p = prefs(context); val attemptsKey = "attempts|" + key(engine)
        val attempts = p.getInt(attemptsKey, 0)
        val unreliable = isUnreliable(tier, engine.provider, warmMs, thermal, powerSave)
        return if (keep(unreliable, attempts)) {
            p.edit().putLong(key(engine), warmMs).putString(key(engine) + "|tier", tier.name).remove(attemptsKey).apply(); Pair(true, attempts)
        } else {
            p.edit().putInt(attemptsKey, attempts + 1).apply(); Pair(false, attempts + 1)
        }
    }

    /** A promotion at run time is kept for later starts (on this app version). */
    fun savePromoted(context: Context, engine: Counter, medianMs: Long) {
        prefs(context).edit().putLong(key(engine), medianMs.coerceAtLeast(1)).putString(key(engine) + "|tier", Tier.FAST.name).remove("attempts|" + key(engine)).apply()
    }

    private fun npuKey(soc: String) = "npuSlow|$soc"

    /** This chip's NPU was found slower than its graphics chip on this app version: skip it (a new version, which may bring a newer
     * QNN runtime, tries the NPU once again). */
    fun npuKnownSlow(context: Context, soc: String = soc()): Boolean = prefs(context).getInt(npuKey(soc), -1) == BuildConfig.VERSION_CODE

    fun markNpuSlow(context: Context, warmMs: Long, soc: String = soc()) {
        prefs(context).edit().putInt(npuKey(soc), BuildConfig.VERSION_CODE).putLong(npuKey(soc) + "|ms", warmMs).apply()
    }

    /** Forget every timing (PerfTest's true first start; a Developer option could use it too). */
    fun forgetAll(context: Context) { prefs(context).edit().clear().apply() }

    /** The one-time explanation of light mode was shown on this phone. */
    fun noticeShown(context: Context) = prefs(context).getBoolean("lightNoticeShown", false)
    fun markNoticeShown(context: Context) = prefs(context).edit().putBoolean("lightNoticeShown", true).apply()
}
