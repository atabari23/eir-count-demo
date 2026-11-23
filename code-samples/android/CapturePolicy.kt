package com.asgard.pillcounter

/**
 * How the 0.19.1 capture checks (BlurCheck, OverlapCheck, weak dots) combine with the existing ones (burst agreement, double dots)
 * into one outcome for a count. Port of `decide()` in scripts/capture_checks.py with the shipped overlap use ('check_far' or
 * 'off'); the reference's 'spread' mode (hold the photo, "Spread the pills") is not built, by the coordinator's decision.
 * Order: retake > please check > high confidence. The app still shows the result through Confidence.of exactly as before (a check
 * line makes the count "Please check", never a percentage). Pure: CapturePolicyTest, CaptureChecksGoldenTest.
 */
object CapturePolicy {
    /** A counted dot below this centre score is "unsure": circled, and the count is "Please check". */
    const val WEAK_DOT = 0.35f

    enum class Decision(val key: String) { RETAKE("refuse"), CHECK("check"), HIGH("high") }

    /** reasons: the reference's words ("blur", "overlap ring", "blur borderline", "burst disagree", "double dot", "weak dot", "no pills"). */
    class Outcome(val decision: Decision, val reasons: List<String>)

    /** blurValue / nDots: of the chosen photo; spots: its overlap spots; weakDots: kept dots with score below WEAK_DOT; burstAgree:
     * BurstChoice.Choice.agree (true for a single photo); doubleGroups: DoubleDotCheck groups; overlapShown: the overlap mode is
     * "check" (far spots count), false for "off" and "log only". */
    fun decide(blurValue: Double?, nDots: Int, spots: List<OverlapCheck.Spot>, weakDots: Int, burstAgree: Boolean, doubleGroups: Int, overlapShown: Boolean): Outcome {
        val blur = BlurCheck.decision(blurValue, nDots)
        if (blur == BlurCheck.Decision.REFUSE) return Outcome(Decision.RETAKE, listOf("blur"))
        val why = ArrayList<String>()
        if (overlapShown && spots.any { it.tier == OverlapCheck.Tier.FAR }) why.add("overlap ring")
        if (blur == BlurCheck.Decision.CHECK) why.add("blur borderline")
        if (!burstAgree) why.add("burst disagree")
        if (doubleGroups > 0) why.add("double dot")
        if (weakDots > 0) why.add("weak dot")
        if (nDots == 0) why.add("no pills")
        return Outcome(if (why.isEmpty()) Decision.HIGH else Decision.CHECK, why)
    }

    /** The dots of `kept` that are weak (index into the decoder's kept list). */
    fun weakDots(kept: List<Point>): List<Int> = kept.indices.filter { kept[it].score < WEAK_DOT }
}

/**
 * Blur retakes (0.19.1). A photo the blur check refuses is not saved: the camera says so and takes another once the usual
 * auto-photo conditions hold again (a fresh steady hold). So that a dim room cannot trap the pharmacist, after two retakes in a row
 * the third refused photo is saved, marked "Please check" with the blur line. Any photo that is not refused (saved, or "no pills")
 * ends the run; so do a new count (the bubble closing) and returning to the camera. Demo photos (fixed sample pictures) are never
 * refused. The manual shutter follows the same rule. Pure and thread-safe (the capture thread asks, the main thread resets).
 */
class BlurRetakes(val maxRetakes: Int = MAX_RETAKES) {
    enum class Action { SAVE, RETAKE, SAVE_AFTER_RETAKES }

    /** retakesBefore: how many photos in a row were refused just before this one (logged with the count). */
    class Step(val action: Action, val retakesBefore: Int)

    private var run = 0
    @get:Synchronized val consecutive: Int get() = run

    /** refused: the blur check said retake (CapturePolicy.Decision.RETAKE); demo: a demo photo. */
    @Synchronized fun onPhoto(refused: Boolean, demo: Boolean): Step {
        val before = run
        return when {
            demo || !refused -> { run = 0; Step(Action.SAVE, before) }
            run >= maxRetakes -> { run = 0; Step(Action.SAVE_AFTER_RETAKES, before) }
            else -> { run++; Step(Action.RETAKE, before) }
        }
    }

    /** A photo that was not a count (no pills found): the run of blurred photos is broken. */
    @Synchronized fun onNotCounted() { run = 0 }

    @Synchronized fun reset() { run = 0 }

    companion object { const val MAX_RETAKES = 2 }
}

/**
 * The capture checks' verdict on one chosen photo, as CaptureActivity.process applies it (pure, so every flow is unit-tested in
 * CapturePolicyTest): whether to save or retake, which check lines to add and which spots to circle.
 *  - measured: CapturePolicy.decide on the photo as it is.
 *  - judged: the verdict the saved count gets. A demo photo, or a refused photo saved after two retakes, is judged without its blur
 *    (the other checks still apply); the latter then gets its own blur line.
 * Overlap mode: OFF and LOG never add a line or a circle (LOG still logs the spots); CHECK circles FAR spots only. NEAR spots are
 * never shown in any mode.
 */
object CaptureOutcome {
    enum class Line { BLUR_AFTER_RETAKES, BLUR_SLIGHT, OVERLAP, WEAK_DOT }

    class Result(val step: BlurRetakes.Step, val measured: CapturePolicy.Outcome, val judged: CapturePolicy.Outcome, val lines: List<Line>,
                 val circleFarSpots: Boolean, val circleWeakDots: Boolean) {
        val retake get() = step.action == BlurRetakes.Action.RETAKE
    }

    fun of(blurValue: Double?, nDots: Int, spots: List<OverlapCheck.Spot>, weakDots: Int, burstAgree: Boolean, doubleGroups: Int,
           mode: OverlapCheck.Mode, demo: Boolean, retakes: BlurRetakes): Result {
        val shown = mode == OverlapCheck.Mode.CHECK
        val measured = CapturePolicy.decide(blurValue, nDots, spots, weakDots, burstAgree, doubleGroups, shown)
        val step = retakes.onPhoto(measured.decision == CapturePolicy.Decision.RETAKE, demo)
        val judged = if (demo || measured.decision == CapturePolicy.Decision.RETAKE) CapturePolicy.decide(null, nDots, spots, weakDots, burstAgree, doubleGroups, shown) else measured
        val lines = ArrayList<Line>()
        if (step.action == BlurRetakes.Action.RETAKE) return Result(step, measured, judged, lines, false, false)
        if (step.action == BlurRetakes.Action.SAVE_AFTER_RETAKES) lines.add(Line.BLUR_AFTER_RETAKES)
        else if ("blur borderline" in judged.reasons) lines.add(Line.BLUR_SLIGHT)
        val far = "overlap ring" in judged.reasons; val weak = "weak dot" in judged.reasons
        if (far) lines.add(Line.OVERLAP)
        if (weak) lines.add(Line.WEAK_DOT)
        return Result(step, measured, judged, lines, far, weak)
    }
}

/**
 * After a blur retake, what the camera must want again so the photo is retaken once the usual conditions hold (pure: CapturePolicyTest).
 * Every flow also restarts the steady-hold gates (a fresh hold), and light mode checks again without waiting for the picture to change.
 *  - Sweep to target, check photo ("rest"): the check photo is still wanted, as if the refused one was never taken.
 *  - Sweep to target, a recount after adding pills ("pour" while adding): the recount is wanted again at the same live count.
 *  - A first pour photo, a plain count, a running total's next tray: nothing was saved, so the state they wait in is unchanged.
 */
object RetakeRearm {
    class Plan(val checkWantedAgain: Boolean, val recountWantedAgain: Boolean)

    fun plan(role: String?, phase: Sweep.Phase?): Plan = Plan(role == "rest", role == "pour" && phase == Sweep.Phase.ADD_MORE)
}

/**
 * What every count (and every blur retake) logs about the 0.19.1 capture checks, to AppLog and the beta recorder, so beta reports can
 * re-tune the thresholds: numbers and decisions only (never an Rx number, initials or a note). Lists are comma-joined strings, as the
 * existing "burst_counts" field. Pure: CapturePolicyTest.
 */
object CaptureCheckLog {
    fun facts(blur: BlurCheck.Measure, blurDecision: BlurCheck.Decision, burstBlur: List<Double?>, blurMs: List<Long>, step: BlurRetakes.Step,
              mode: OverlapCheck.Mode, overlap: OverlapCheck.Result?, weakDots: Int, measured: CapturePolicy.Outcome, judged: CapturePolicy.Outcome): LinkedHashMap<String, Any?> {
        val f = LinkedHashMap<String, Any?>()
        f["blur_width"] = blur.value?.let { r3(it) }
        f["blur_windows"] = blur.windows
        f["blur_decision"] = blurDecision.key
        f["burst_blur"] = burstBlur.joinToString(",") { v -> v?.let { "%.3f".format(java.util.Locale.US, it) } ?: "-" }
        f["blur_ms"] = blurMs.joinToString(",")
        f["blur_retakes"] = step.retakesBefore
        f["blur_action"] = step.action.name.lowercase()
        f["overlap_mode"] = mode.key
        f["overlap_far"] = overlap?.spots?.count { it.tier == OverlapCheck.Tier.FAR }
        f["overlap_near"] = overlap?.spots?.count { it.tier == OverlapCheck.Tier.NEAR }
        f["overlap_max_r"] = overlap?.spots?.maxOfOrNull { it.r }?.let { r3(it) }
        f["overlap_sigma"] = overlap?.let { r3(it.sigma) }
        f["overlap_ms"] = overlap?.ms
        f["weak_dots"] = weakDots
        f["measured_decision"] = measured.decision.key
        f["decision"] = when (step.action) {
            BlurRetakes.Action.RETAKE -> "retake"
            BlurRetakes.Action.SAVE_AFTER_RETAKES -> "check"
            BlurRetakes.Action.SAVE -> judged.decision.key
        }
        f["reasons"] = (if (step.action == BlurRetakes.Action.SAVE_AFTER_RETAKES) listOf("blur after retakes") + judged.reasons
            else if (step.action == BlurRetakes.Action.RETAKE) measured.reasons else judged.reasons).joinToString(",")
        return f
    }

    /** The same facts for the count's record (perf.checks): JSON null for what was not measured. */
    fun json(facts: Map<String, Any?>): org.json.JSONObject = org.json.JSONObject().apply { for ((k, v) in facts) put(k, v ?: org.json.JSONObject.NULL) }

    private fun r3(v: Double) = Math.round(v * 1000.0) / 1000.0
}

/**
 * The camera's "photo was blurred" state after a retake (0.19.1 review): the state chip ("Image blurred", warn colour) and the hint
 * under the shutter ("Photo was blurred. Hold still.") are kept for HOLD_MS from the refusal, then the live states take over again,
 * whether or not a photo has started (the next photo may wait precisely for what the live view has to say). A burst started inside
 * the HOLD_MS does not end it early. Inside the HOLD_MS, "Point at the tray" and "Move closer" still win: the camera cannot see a
 * tray properly, and that is what the pharmacist must act on first. A second refusal restarts the HOLD_MS. The camera clears it when
 * a count is shown or saved, on "no pills found", on a new count, and when it leaves the screen or comes back. Pure and
 * thread-safe: CapturePolicyTest.
 */
class RetakeNotice(val holdMs: Long = HOLD_MS) {
    private var shownAt = -1L

    /** A photo was refused for blur at `now` (elapsed ms). A second refusal restarts the notice. */
    @Synchronized fun show(now: Long) { shownAt = now }

    @Synchronized fun clear() { shownAt = -1L }

    /** True inside the HOLD_MS after the refusal (and clears itself after it). */
    @Synchronized fun active(now: Long): Boolean {
        if (shownAt < 0) return false
        if (now - shownAt >= holdMs) { shownAt = -1L; return false }
        return true
    }

    /** True when the notice keeps the chip and hint over this live state (the state names the camera logs; null for no live state,
     * such as the Sweep header or a photo starting). */
    fun holds(now: Long, liveState: String?): Boolean = active(now) && liveState !in OVERRIDING

    companion object {
        const val HOLD_MS = 4000L
        /** Live states that still show during the notice: the camera cannot see a tray properly. */
        val OVERRIDING = setOf("Point at the tray", "Move closer")
    }
}
