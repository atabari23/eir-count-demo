package com.asgard.pillcounter

import android.content.Context
import org.json.JSONObject

/**
 * Sweep to target. The pharmacist sets the prescribed quantity, pours, and Eir counts the pile on the tray's main surface
 * ("poured"). Pills swept into the chute are not counted by the model (it counts the main surface only), so while the
 * pharmacist sweeps, poured − live count = pills in the chute. When the tray shows poured − target, Eir takes a check
 * photo ("left"): dispensed = poured − left. Each photo is its own saved count; `CountRecord.fill` links them:
 * {"id": fill id, "role": "pour" | "rest", "target": n, "pourId": id of the pour photo (rest only)}.
 */
object Sweep {
    enum class Phase { POUR, ADD_MORE, SWEEP, DONE }

    /** What to do after the pour photo. */
    fun afterPour(target: Int, poured: Int): Phase = when {
        poured == target -> Phase.DONE
        poured < target -> Phase.ADD_MORE
        else -> Phase.SWEEP
    }

    /** Pills that should be left on the tray once the target is in the chute. */
    fun leftWanted(target: Int, poured: Int) = poured - target

    /** Positive = too many in the chute (put some back), negative = sweep more. */
    fun difference(target: Int, poured: Int, left: Int) = (poured - left) - target

    /** Whether the live view says the tray is at the wanted number (then a check photo is taken when the phone is steady). */
    fun readyToCheck(target: Int, poured: Int, liveLeft: Int, visible: Boolean) = visible && liveLeft == leftWanted(target, poured)

    /** Add-more phase: a new pour photo once the tray shows more pills than the last pour photo. */
    fun readyToRecount(lastPoured: Int, liveCount: Int, visible: Boolean) = visible && liveCount > lastPoured

    fun fillJson(id: String, role: String, target: Int, pourId: String? = null): JSONObject =
        JSONObject().put("id", id).put("role", role).put("target", target).apply { if (pourId != null) put("pourId", pourId) }
}

/** A fill as History shows it: the latest pour photo and (after sweeping) the latest check photo of the tray. */
class FillSummary(val id: String, val target: Int, val pour: CountRecord, val rest: CountRecord?) {
    val poured get() = pour.finalCount
    val left get() = rest?.finalCount
    /** What went into the chute (or the whole pour when no sweeping was needed). */
    val dispensed get() = rest?.let { poured - it.finalCount } ?: poured
    val difference get() = dispensed - target
    /** Finished and exactly on target. */
    val exact get() = difference == 0 && (rest != null || poured == target)
    val latest get() = rest ?: pour

    companion object {
        /** Groups the fill photos of a list of counts; counts that are not part of a fill are ignored. */
        fun all(records: List<CountRecord>): Map<String, FillSummary> {
            val byFill = records.filter { it.fill != null && !it.deleted }.groupBy { it.fill!!.optString("id") }
            return byFill.mapNotNull { (id, list) ->
                val pour = list.filter { it.fill!!.optString("role") == "pour" }.maxByOrNull { it.date } ?: return@mapNotNull null
                val rest = list.filter { it.fill!!.optString("role") == "rest" }.maxByOrNull { it.date }
                id to FillSummary(id, pour.fill!!.optInt("target"), pour, rest)
            }.toMap()
        }
    }
}

/** What the next count belongs to, set before counting (all optional): the prescribed quantity, the Rx number and the
 * stock bottle (only ever set by the barcode scan of v0.17-v0.18.1; nothing sets it since v0.18.2, a draft left by an update keeps it). Cleared when a fill is finished. Kept in preferences so it survives the screen being rebuilt. */
object FillDraft {
    private lateinit var appContext: Context
    var target: Int? = null; private set
    var rxNumber: String? = null; private set
    var product: JSONObject? = null; private set

    fun init(context: Context) {
        if (::appContext.isInitialized) return
        appContext = context.applicationContext
        val p = prefs()
        target = p.getInt("target", -1).takeIf { it > 0 }
        rxNumber = p.getString("rx", null)
        product = p.getString("product", null)?.let { runCatching { JSONObject(it) }.getOrNull() }
    }

    private fun prefs() = appContext.getSharedPreferences("eir_fill", Context.MODE_PRIVATE)

    val isEmpty get() = target == null && rxNumber == null && product == null

    fun set(target: Int?, rxNumber: String?, product: JSONObject?) {
        this.target = target?.takeIf { it > 0 }; this.rxNumber = rxNumber?.trim()?.ifEmpty { null }; this.product = product
        if (::appContext.isInitialized) prefs().edit().putInt("target", this.target ?: -1).putString("rx", this.rxNumber).putString("product", product?.toString()).apply()
        AppLog.event("fill_draft", mapOf("target" to this.target, "rx" to (this.rxNumber != null), "product" to product?.optString("name")))
    }

    fun clear() = set(null, null, null)
}
