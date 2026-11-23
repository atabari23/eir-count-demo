"""Reference implementation of the two capture checks (blur, overlap) and the combined policy, for the Kotlin port.
Study, numbers and tuning: scripts/capture_checks_study.py, reports/round06/capture-checks/. Golden vectors:
artifacts/round06/capture-checks/golden/ (scripts/capture_checks_golden.py).

Written the way it should be ported: numpy arrays are used only as plain 2-D buffers (indexing, slicing, sums, abs),
anything with an order is an explicit loop, percentiles are `sorted[int(q * (n - 1))]` (no interpolation), medians
are explicit, no scipy. Every constant is below with where it came from.

Coordinates
  canvas px  : the 1024 x 1024 letterboxed model input (PillDecoder Point.x / Point.y)
  cell       : the 256 x 256 output grid, cell (cx, cy) has its centre at canvas ((cx + 0.5) * 4, (cy + 0.5) * 4)
  preview px : the 1280 px preview bitmap the photo is counted from (Images.loadOriented(f, 1280));
               preview = (canvas - left) / scale, CounterModel.Result.left/top/scale
"""
import math
import numpy as np

# ---------------------------------------------------------------------------------------------------- constants
# Blur (edge width): controlled blur + eye labels, reports/round06/capture-checks/blur-*.json
BLUR_WINDOW_R = 48          # half window around each dot, preview px (whole pill inside: pills are 12-80 px at 1280)
BLUR_TOPK = 20              # edge strength per direction = mean of the 20 largest |directional derivative|
BLUR_MIN_CONTRAST = 25      # windows whose p98 - p2 grey range is below this are flat (no pill edge): skipped
BLUR_MAX_DOTS = 64          # at most 64 dots, evenly spread over the decoder's order (score descending)
BLUR_MIN_WINDOWS = 1        # fewer usable windows -> no measurement (value None): never refuse on that
BLUR_DENSE_N = 25           # blur only changes counts of photos with >= 25 dots (controlled blur: 49 of 52 changes)
BLUR_REFUSE = 6.0           # >= 25 dots: edge width (px at 1280) at or above which the photo is refused (retake)
BLUR_CHECK = 4.5            # >= 25 dots: borderline band [4.5, 6.0) -> "Please check"
BLUR_REFUSE_ANY = 9.0       # any count: refused at or above this (extreme blur)
BLUR_CHECK_ANY = 6.0        # < 25 dots: [6.0, 9.0) -> "Please check"

# Overlap (unexplained heat next to counted dots): tuned on train + validation, reports/round06/capture-checks/overlap-features.json
OV_ISO_NN = 6.0             # a dot is "isolated" when its nearest other dot is >= 6 cells away
OV_SIGMA_DEFAULT = 1.6      # single-pill blob sigma (cells) when fewer than 2 isolated dots
OV_TEMPLATE_R = 3.5         # template radius = ceil(3.5 sigma) cells
OV_MIN_D = 1.5              # candidate at least 1.5 cells from the nearest counted dot
OV_FAR_D = 6.0              # >= 6 cells (24 canvas px) away: "separate glow" tier
OV_FAR_R = 0.18             # residual needed in the far tier
OV_NEAR_R = 0.45            # residual needed in the near (shoulder) tier, 1.5 <= d < 6 cells
OV_EDGE = 3                 # candidate at least 3 cells inside the tray component (chessboard distance)
OV_MERGE_PX = 12.0          # spots closer than 12 canvas px are one spot
OV_MAX_SPOTS = 12           # at most 12 spots returned (strongest first)


# ----------------------------------------------------------------------------------------------- shared helpers
def sigmoid(v):
    return 1.0 / (1.0 + np.exp(-np.clip(v, -80.0, 80.0)))


def grey_of_rgb(rgb):
    """int grey as Images.sharpness / PillFocus.measure: (3 R + 6 G + B) / 10, integer division."""
    a = rgb.astype(np.int32)
    return (a[..., 0] * 3 + a[..., 1] * 6 + a[..., 2]) // 10


def median(values):
    """Median of a non-empty list: middle of the ascending sort, mean of the two middle values for an even count."""
    s = sorted(values); n = len(s)
    return s[n // 2] if n % 2 == 1 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def largest_component(mask):
    """Largest 8-connected True region, row-major scan, first largest wins (= PillDecoder.largestComponent)."""
    h, w = mask.shape; remaining = mask.copy(); best = []
    for start in range(h * w):
        sy, sx = divmod(start, w)
        if not remaining[sy, sx]: continue
        remaining[sy, sx] = False; stack = [(sy, sx)]; comp = []
        while stack:
            cy, cx = stack.pop(); comp.append((cy, cx))
            for ny in range(max(0, cy - 1), min(h, cy + 2)):
                for nx in range(max(0, cx - 1), min(w, cx + 2)):
                    if remaining[ny, nx]: remaining[ny, nx] = False; stack.append((ny, nx))
        if len(comp) > len(best): best = comp
    out = np.zeros((h, w), bool)
    for y, x in best: out[y, x] = True
    return out


def max3(a, pad):
    """3x3 max filter, out-of-range neighbours = pad."""
    h, w = a.shape; p = np.full((h + 2, w + 2), pad, dtype=a.dtype); p[1:-1, 1:-1] = a; out = p[1:-1, 1:-1].copy()
    for dy in (0, 1, 2):
        for dx in (0, 1, 2): out = np.maximum(out, p[dy:dy + h, dx:dx + w])
    return out


# ------------------------------------------------------------------------------------------------------- blur
def blur_width(grey, dots_preview):
    """Edge width (px of the 1280 preview) of the worst motion direction, median over pills. Higher = blurrier.

    grey          : int array H x W, the 1280 px preview in grey (grey_of_rgb), H, W >= 2 * BLUR_WINDOW_R + 3
    dots_preview  : list of (x, y) dot centres in preview px, in the decoder's order (score descending)
    returns       : (value or None, windows used, per-window widths list of [w0, w45, w90, w135])

    Per dot: a (2R+1)^2 window centred on the dot (centre clamped so the window and its 1-px border are inside the
    image). C = p98 - p2 of the grey values (rank int(q * (n - 1))). For 4 directions the absolute central difference per
    pixel step: d0 = |g[y][x+1] - g[y][x-1]| / 2, d90 = |g[y+1][x] - g[y-1][x]| / 2,
    d45 = |g[y-1][x+1] - g[y+1][x-1]| / (2 sqrt 2), d135 = |g[y+1][x+1] - g[y-1][x-1]| / (2 sqrt 2), over the window pixels.
    S_d = mean of the BLUR_TOPK largest d values; width_d = C / max(S_d, 0.001). A sharp step edge gives ~2-3 px; motion
    blur of L px gives about L along the motion. Pill value = max over the 4 directions; photo value = median over pills."""
    H, W = grey.shape; R = BLUR_WINDOW_R; n = len(dots_preview)
    if n == 0: return None, 0, []
    if n <= BLUR_MAX_DOTS: idx = list(range(n))
    else: idx = [int(math.floor(i * (n - 1) / (BLUR_MAX_DOTS - 1) + 0.5)) for i in range(BLUR_MAX_DOTS)]
    g = grey.astype(np.float64); widths = []; worst = []
    for i in idx:
        x = int(dots_preview[i][0]); y = int(dots_preview[i][1])      # truncation toward zero, as Kotlin toInt()
        x = min(max(x, R + 1), W - R - 2); y = min(max(y, R + 1), H - R - 2)
        win = grey[y - R:y + R + 1, x - R:x + R + 1].ravel(); s = np.sort(win); m = len(s)
        C = float(s[int(0.98 * (m - 1))] - s[int(0.02 * (m - 1))])
        if C < BLUR_MIN_CONTRAST: continue
        ys, ye, xs, xe = y - R, y + R + 1, x - R, x + R + 1
        d0 = np.abs(g[ys:ye, xs + 1:xe + 1] - g[ys:ye, xs - 1:xe - 1]) / 2.0
        d90 = np.abs(g[ys + 1:ye + 1, xs:xe] - g[ys - 1:ye - 1, xs:xe]) / 2.0
        d45 = np.abs(g[ys - 1:ye - 1, xs + 1:xe + 1] - g[ys + 1:ye + 1, xs - 1:xe - 1]) / (2.0 * math.sqrt(2.0))
        d135 = np.abs(g[ys + 1:ye + 1, xs + 1:xe + 1] - g[ys - 1:ye - 1, xs - 1:xe - 1]) / (2.0 * math.sqrt(2.0))
        wd = []
        for d in (d0, d45, d90, d135):
            top = np.sort(d.ravel())[-BLUR_TOPK:]; wd.append(C / max(float(top.sum()) / BLUR_TOPK, 1e-3))
        widths.append(wd); worst.append(max(wd))
    if len(worst) < BLUR_MIN_WINDOWS: return None, len(worst), widths
    return float(median(worst)), len(worst), widths


def blur_decision(value, n_dots):
    """'refuse' (retake), 'check' (borderline, Please check) or 'ok'. value None (no usable window) -> 'ok'.
    n_dots = the decoder's count for this photo."""
    if value is None: return 'ok'
    dense = n_dots >= BLUR_DENSE_N
    if value >= BLUR_REFUSE_ANY or (dense and value >= BLUR_REFUSE): return 'refuse'
    if value >= BLUR_CHECK_ANY or (dense and value >= BLUR_CHECK): return 'check'
    return 'ok'


# ---------------------------------------------------------------------------------------------------- overlap
def isolated_sigma(p, kept_cells):
    """Single-pill blob size of this photo (cells). kept_cells: list of (cx, cy, score) of the counted dots.
    For each dot whose nearest other dot is >= OV_ISO_NN cells away and whose 9x9 window is inside the map:
    w = max(0, p / p_dot - 0.05) over the window, sigma_k = sqrt(sum((dx^2 + dy^2) w) / sum(w) / 2).
    Median of sigma_k when there are >= 2 of them, else OV_SIGMA_DEFAULT."""
    n = len(kept_cells)
    if n < 2: return OV_SIGMA_DEFAULT
    out = []
    for i in range(n):
        cx, cy, _ = kept_cells[i]; nn = 1e9
        for j in range(n):
            if j != i: nn = min(nn, math.hypot(kept_cells[j][0] - cx, kept_cells[j][1] - cy))
        if nn < OV_ISO_NN or not (4 <= cx < 252 and 4 <= cy < 252): continue
        pk = max(float(p[cy, cx]), 1e-6); num = 0.0; den = 0.0
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                w = float(p[cy + dy, cx + dx]) / pk - 0.05
                if w > 0: num += (dx * dx + dy * dy) * w; den += w
        if den > 0: out.append(math.sqrt(num / den / 2.0))
    return median(out) if len(out) >= 2 else OV_SIGMA_DEFAULT


def overlap_spots(centre_logits, tray_logits, kept):
    """Places where heat is left over after every counted dot has explained its own blob: a pill probably merged with,
    pressed against or hidden by a neighbour. centre_logits, tray_logits: 256 x 256 float32 (the two model channels,
    exactly what PillDecoder.decode receives). kept: the decoder's kept points [(x, y, score)] in canvas px.
    Returns (spots, info): spots = list of dict(x, y canvas px, r residual, d cells to nearest dot, tier 'far'|'near'),
    strongest first, at most OV_MAX_SPOTS."""
    p = sigmoid(centre_logits.astype(np.float64)); H, W = p.shape
    comp = largest_component(tray_logits >= 0)
    gate = max3(max3(comp.astype(np.int8), 0), 0) > 0                           # 5x5 dilation = decoder's tray gate
    edge = np.zeros((H, W), np.int32); cur = comp.copy()
    for _ in range(8):     # chessboard distance to the nearest non-tray cell, cap 8; cells outside the map count as tray
        edge += cur
        er = np.ones((H, W), bool); pad = np.ones((H + 2, W + 2), bool); pad[1:-1, 1:-1] = cur
        for dy in (0, 1, 2):
            for dx in (0, 1, 2): er &= pad[dy:dy + H, dx:dx + W]
        cur = er
    K = [(int(round(q[0] / 4.0 - 0.5)), int(round(q[1] / 4.0 - 0.5)), float(q[2])) for q in kept]
    sigma = isolated_sigma(p, K)
    r = int(math.ceil(OV_TEMPLATE_R * sigma)); T = np.zeros((H, W))
    for cx, cy, sc in K:                                                         # template = max over dots
        for y in range(max(0, cy - r), min(H, cy + r + 1)):
            for x in range(max(0, cx - r), min(W, cx + r + 1)):
                v = sc * math.exp(-((x - cx) ** 2 + (y - cy) ** 2) / (2.0 * sigma * sigma))
                if v > T[y, x]: T[y, x] = v
    R = p - T; Rmax = max3(R, -np.inf); cands = []
    for y in range(H):
        for x in range(W):
            v = R[y, x]
            if v < OV_FAR_R or v < Rmax[y, x] or not gate[y, x] or edge[y, x] < OV_EDGE: continue
            d = 1e9
            for cx, cy, _ in K: d = min(d, math.hypot(cx - x, cy - y))
            if d < OV_MIN_D: continue
            tier = 'far' if d >= OV_FAR_D else 'near'
            if tier == 'near' and v < OV_NEAR_R: continue
            cands.append((float(v), y, x, d, tier))
    cands.sort(key=lambda c: (-c[0], c[1], c[2]))                                # strongest first, then row-major
    spots = []
    for v, y, x, d, tier in cands:
        px, py = (x + 0.5) * 4.0, (y + 0.5) * 4.0
        if all(math.hypot(px - s['x'], py - s['y']) > OV_MERGE_PX for s in spots):
            spots.append(dict(x=px, y=py, r=round(v, 4), d=round(d, 3), tier=tier))
        if len(spots) >= OV_MAX_SPOTS: break
    return spots, dict(sigma=sigma, template_r=r)


# ----------------------------------------------------------------------------------------------------- policy
WEAK_DOT = 0.35             # a counted dot below this centre score is "unsure" (ring it, Please check)


def decide(blur_value, n_dots, spots, weak_dots, burst_agree, double_groups, overlap_mode='spread'):
    """Combined decision for one count. Order: refuse > spread > check > high. Returns (decision, reasons).
    blur_value / n_dots: of the chosen photo; spots: overlap_spots of the chosen photo; weak_dots: kept dots with
    score < WEAK_DOT; burst_agree: BurstChoice.Choice.agree (True for a single photo); double_groups: DoubleDotCheck groups.
    overlap_mode:
      'spread'    (as tuned and frozen) any spot -> 'spread' (rings, "Spread the pills", hold the automatic photo)
      'check_far' (RECOMMENDED for shipping, chosen after the frozen test: see the write-up) near-tier spots are ignored;
                  far-tier spots ring the place and make the count "Please check", never hold or block
      'off'       spots ignored (log only)"""
    b = blur_decision(blur_value, n_dots)
    if b == 'refuse': return 'refuse', ['blur']
    if overlap_mode == 'spread' and spots: return 'spread', ['overlap']
    why = []
    if overlap_mode == 'check_far' and any(sp['tier'] == 'far' for sp in spots): why.append('overlap ring')
    if b == 'check': why.append('blur borderline')
    if not burst_agree: why.append('burst disagree')
    if double_groups: why.append('double dot')
    if weak_dots: why.append('weak dot')
    if n_dots == 0: why.append('no pills')
    return ('check' if why else 'high'), why
