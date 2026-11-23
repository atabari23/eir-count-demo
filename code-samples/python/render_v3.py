"""capsule-v3 (round06 examples): render_v2.py plus tablets, softgels, explicit tray colour and gloss, aimed
reflections, a hard shadow, stone/steel/wood benches and foreign objects. Old plans render as before.

capsule-v2 Blender scene renderer: canonical tray, sourced shell sizes, physics-settled piles,
pharmacy bench clutter, phone camera (S23 Ultra main: 6.3 mm, f/1.7, 4:3), linear EXR + exact ID pass.

blender --background --factory-startup --python render_v2.py -- --plan plan-calib.json --out D:\\AI\\Synth\\capsule-v2-calib-01 [--ids v2c-01,v2c-02] [--worker 0 --workers 1]

Units: 1 Blender unit = 1 cm (scene unit scale 0.01). Tray frame: main floor top z=0, trough toward +y.
Outputs per scene: <id>.exr (scene-linear RGB), <id>-ids.png (R + 256*G = capsule id), <id>.json (all parameters,
per-capsule pose/status/projected centre and silhouette samples, tray polygon, camera, lights).
Labels (visibility rules) and the phone-camera image pipeline are applied afterwards by finish_v2.py.
"""
import argparse, json, math, random, sys, time, traceback
from pathlib import Path
import numpy as np
import bpy, bmesh
from mathutils import Vector, Matrix, Quaternion
from bpy_extras.object_utils import world_to_camera_view

HERE = Path(__file__).resolve().parent
ASSETS = HERE / 'assets'
TRAY = json.loads((HERE / 'tray-v2.json').read_text()); G = TRAY['geometry']
HX, HY = G['floor_half']; TABLE_Z = G['table_z']; WALL = G['wall']; TR = G['trough_r']; TYC = HY + WALL + TR
RES = (1530, 2040)
SENSOR_MM, LENS_MM, FSTOP = 9.6, 6.3, 1.7
HDRI = Path(r'D:\AI\Assets\hdri\polyhaven')


def log(*a): print(time.strftime('%H:%M:%S'), *a, flush=True)


def hex_lin(h):
    c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    return tuple(x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in c)


def kelvin_rgb(k):
    """Approximate blackbody colour (normalised, linear-ish) for light tint."""
    t = k / 100
    r = 255 if t <= 66 else 329.7 * (t - 60) ** -0.1332
    g = 99.47 * math.log(t) - 161.1 if t <= 66 else 288.1 * (t - 60) ** -0.0755
    b = 255 if t >= 66 else (0 if t <= 19 else 138.5 * math.log(t - 10) - 305.0)
    c = [max(0, min(255, v)) / 255 for v in (r, g, b)]; c = [x ** 2.2 for x in c]; m = max(c)
    return tuple(x / m for x in c)


# ---------------------------------------------------------------- materials
def new_mat(name):
    m = bpy.data.materials.new(name); m.use_nodes = True; nt = m.node_tree; nt.nodes.clear()
    out = nt.nodes.new('ShaderNodeOutputMaterial'); b = nt.nodes.new('ShaderNodeBsdfPrincipled'); nt.links.new(b.outputs[0], out.inputs['Surface'])
    return m, nt, b


def img_node(nt, file, colorspace='Non-Color', ext='REPEAT'):
    t = nt.nodes.new('ShaderNodeTexImage'); t.image = bpy.data.images.load(str(ASSETS / file), check_existing=True)
    t.image.colorspace_settings.name = colorspace; t.extension = ext; return t


def mix(nt, fac, a, b):
    m = nt.nodes.new('ShaderNodeMixRGB'); m.blend_type = 'MIX'
    for sock, v in ((m.inputs[0], fac), (m.inputs[1], a), (m.inputs[2], b)):
        if isinstance(v, (int, float)): sock.default_value = v
        elif isinstance(v, (tuple, list)): sock.default_value = tuple(v) if len(v) == 4 else (*v, 1)
        else: nt.links.new(v, sock)
    return m.outputs[0]


def maprange(nt, src, a, b, c, d):
    n = nt.nodes.new('ShaderNodeMapRange'); nt.links.new(src, n.inputs[0])
    n.inputs[1].default_value, n.inputs[2].default_value, n.inputs[3].default_value, n.inputs[4].default_value = a, b, c, d
    return n.outputs[0]


TRAY_COLOURS = {'blue': (0.022, 0.12, 0.22), 'royal': (0.018, 0.05, 0.33), 'lightblue': (0.11, 0.38, 0.64), 'red': (0.45, 0.02, 0.025),
                'white': (0.74, 0.74, 0.72), 'purple': (0.13, 0.05, 0.27), 'green': (0.02, 0.2, 0.08), 'clear': (0.95, 0.97, 0.98)}


def clear_tray_material(rng):
    """Transparent polystyrene/acrylic tray: refractive shell, frosted scratches, light passes for shadows."""
    m, nt, b = new_mat('tray'); b.inputs['Transmission Weight'].default_value = 1.0; b.inputs['IOR'].default_value = rng.uniform(1.49, 1.59)
    tint = rng.choice([(0.96, 0.98, 1.0), (0.97, 0.97, 0.97), (0.9, 0.95, 1.0)]); b.inputs['Base Color'].default_value = (*tint, 1)
    tc = nt.nodes.new('ShaderNodeTexCoord'); mp = nt.nodes.new('ShaderNodeMapping'); nt.links.new(tc.outputs['Object'], mp.inputs[0])
    mp.inputs['Location'].default_value = (0.5, 10 / 22, 0); mp.inputs['Scale'].default_value = (1 / 20, 1 / 22, 1)
    sc = img_node(nt, 'tray-scratch.png'); nt.links.new(mp.outputs[0], sc.inputs[0]); strength = rng.uniform(0.4, 1.0)
    r0 = rng.uniform(0.01, 0.05); nt.links.new(maprange(nt, sc.outputs[0], 0, 1, r0, r0 + 0.35 * strength), b.inputs['Roughness'])
    shadow_transparent(nt, b)
    return m, dict(colour='clear', base_linear=tint, roughness=r0, scratch_strength=strength)


def tray_material(rng, colour, rgb=None, gloss=None, logo=None):
    """v3: rgb = explicit linear tray colour (same-colour and black trays); gloss = base roughness (glare scenes)."""
    if colour == 'clear': return clear_tray_material(rng)
    base = tuple(rgb) if rgb else tuple(c * rng.uniform(0.9, 1.1) for c in TRAY_COLOURS[colour]); kr = 0.3 if gloss is not None and gloss < 0.2 else 1.0
    m, nt, b = new_mat('tray'); b.inputs['Specular IOR Level'].default_value = 0.5; b.inputs['IOR'].default_value = 1.49
    if colour == 'white': b.inputs['Subsurface Weight'].default_value = 0.15; b.inputs['Subsurface Radius'].default_value = (1.0, 1.0, 1.0); b.inputs['Subsurface Scale'].default_value = 0.1
    tc = nt.nodes.new('ShaderNodeTexCoord'); mp = nt.nodes.new('ShaderNodeMapping'); nt.links.new(tc.outputs['Object'], mp.inputs[0])
    mp.inputs['Location'].default_value = (0.5, 10 / 22, 0); mp.inputs['Scale'].default_value = (1 / 20, 1 / 22, 1)
    sc = img_node(nt, 'tray-scratch.png'); nt.links.new(mp.outputs[0], sc.inputs[0])
    wear = img_node(nt, 'table-wear.png'); mp2 = nt.nodes.new('ShaderNodeMapping'); mp2.inputs['Scale'].default_value = (1 / 30, 1 / 30, 1); nt.links.new(tc.outputs['Object'], mp2.inputs[0]); nt.links.new(mp2.outputs[0], wear.inputs[0])
    strength = rng.uniform(0.35, 0.75)   # v3 (owner): visible in a reflection, never a scuffed-up tray
    sfac = maprange(nt, sc.outputs[0], 0, 1, 0, strength)
    colour_out = mix(nt, sfac, base, tuple(min(1, c * 1.6 + 0.04) for c in base))
    if logo:   # brand name printed on the counting floor: {file, at, size, rot_deg, ink, opacity}
        th = math.radians(logo.get('rot_deg', 0)); cs, sn = math.cos(th), math.sin(th); (ax, ay), (lw, lh) = logo['at'], logo['size']; cu = nt.nodes.new('ShaderNodeCombineXYZ')
        for k, (vx, vy, off) in enumerate(((cs / lw, sn / lw, 0.5 - (ax * cs + ay * sn) / lw), (-sn / lh, cs / lh, 0.5 - (-ax * sn + ay * cs) / lh))):   # plain rotation about the floor normal (a Mapping node mirrored the text)
            dp = nt.nodes.new('ShaderNodeVectorMath'); dp.operation = 'DOT_PRODUCT'; nt.links.new(tc.outputs['Object'], dp.inputs[0]); dp.inputs[1].default_value = (vx, vy, 0)
            ad = nt.nodes.new('ShaderNodeMath'); ad.operation = 'ADD'; nt.links.new(dp.outputs['Value'], ad.inputs[0]); ad.inputs[1].default_value = off; nt.links.new(ad.outputs[0], cu.inputs[k])
        lg = img_node(nt, logo['file'], ext='CLIP'); nt.links.new(cu.outputs[0], lg.inputs[0])
        sz = nt.nodes.new('ShaderNodeSeparateXYZ'); nt.links.new(tc.outputs['Object'], sz.inputs[0]); az = nt.nodes.new('ShaderNodeMath'); az.operation = 'ABSOLUTE'; nt.links.new(sz.outputs['Z'], az.inputs[0])
        lf = nt.nodes.new('ShaderNodeMath'); lf.operation = 'MULTIPLY'; nt.links.new(lg.outputs[0], lf.inputs[0]); nt.links.new(maprange(nt, az.outputs[0], 0.05, 0.08, logo.get('opacity', 0.85), 0.0), lf.inputs[1])
        colour_out = mix(nt, lf.outputs[0], colour_out, tuple(logo['ink']))
    nt.links.new(colour_out, b.inputs['Base Color'])
    r0 = rng.uniform(0.26, 0.38); r0 = gloss if gloss is not None else r0
    rough = nt.nodes.new('ShaderNodeMath'); rough.operation = 'MULTIPLY_ADD'; nt.links.new(sfac, rough.inputs[0]); rough.inputs[1].default_value = 0.35 * kr; rough.inputs[2].default_value = r0
    rough2 = nt.nodes.new('ShaderNodeMath'); rough2.operation = 'MULTIPLY_ADD'; nt.links.new(wear.outputs[0], rough2.inputs[0]); rough2.inputs[1].default_value = rng.uniform(0.3, 0.8) * kr; nt.links.new(rough.outputs[0], rough2.inputs[2])
    nt.links.new(rough2.outputs[0], b.inputs['Roughness'])
    bump = nt.nodes.new('ShaderNodeBump'); bump.inputs['Strength'].default_value = 0.25 * strength; bump.inputs['Distance'].default_value = 0.002; bump.invert = True; nt.links.new(sc.outputs[0], bump.inputs['Height'])
    peel = nt.nodes.new('ShaderNodeTexNoise'); peel.inputs['Scale'].default_value = 55; peel.inputs['Detail'].default_value = 4; nt.links.new(tc.outputs['Object'], peel.inputs['Vector'])
    bump2 = nt.nodes.new('ShaderNodeBump'); bump2.inputs['Strength'].default_value = 0.035; bump2.inputs['Distance'].default_value = 0.004; nt.links.new(peel.outputs['Fac'], bump2.inputs['Height']); nt.links.new(bump2.outputs[0], bump.inputs['Normal'])
    nt.links.new(bump.outputs[0], b.inputs['Normal'])
    return m, dict(colour=colour, base_linear=base, roughness=r0, scratch_strength=strength, logo=logo)


def lid_material(rng):
    m, nt, b = new_mat('lid'); b.inputs['Transmission Weight'].default_value = 1.0; b.inputs['IOR'].default_value = 1.58
    b.inputs['Base Color'].default_value = (0.93, 0.96, 0.98, 1)
    uv = nt.nodes.new('ShaderNodeTexCoord'); marks = img_node(nt, 'lid-marks.png', ext='CLIP'); nt.links.new(uv.outputs['UV'], marks.inputs[0])
    sc = img_node(nt, 'tray-scratch.png'); nt.links.new(uv.outputs['UV'], sc.inputs[0])
    frost = nt.nodes.new('ShaderNodeMath'); frost.operation = 'MAXIMUM'; nt.links.new(marks.outputs[0], frost.inputs[0]); nt.links.new(maprange(nt, sc.outputs[0], 0, 1, 0, 0.5), frost.inputs[1])
    nt.links.new(maprange(nt, frost.outputs[0], 0, 1, rng.uniform(0.02, 0.06), 0.45), b.inputs['Roughness'])
    nt.links.new(maprange(nt, marks.outputs[0], 0, 1, 1.0, 0.55), b.inputs['Transmission Weight'])
    shadow_transparent(nt, b)
    return m


def shadow_transparent(nt, bsdf):
    """Thin clear plastic: let shadow rays pass (Cycles would otherwise need refractive caustics)."""
    out = next(n for n in nt.nodes if n.type == 'OUTPUT_MATERIAL'); lp = nt.nodes.new('ShaderNodeLightPath')
    tr = nt.nodes.new('ShaderNodeBsdfTransparent'); mx = nt.nodes.new('ShaderNodeMixShader')
    nt.links.new(lp.outputs['Is Shadow Ray'], mx.inputs[0]); nt.links.new(bsdf.outputs[0], mx.inputs[1]); nt.links.new(tr.outputs[0], mx.inputs[2])
    nt.links.new(mx.outputs[0], out.inputs['Surface'])


def capsule_materials(rng, prod):
    mats = []; gloss = rng.choice([0.07, 0.10, 0.13, 0.17]); info = dict(roughness=gloss)
    ink_col = {'black': (0.012, 0.012, 0.014), 'white': (0.78, 0.78, 0.76), 'darkblue': (0.012, 0.02, 0.085)}[prod['imprint']['ink']] if prod.get('imprint') else None
    for part, hx in (('body', prod['body_hex']), ('cap', prod['cap_hex'])):
        m, nt, b = new_mat('shell-' + part); col = hex_lin(hx)
        oi = nt.nodes.new('ShaderNodeObjectInfo'); jit = maprange(nt, oi.outputs['Random'], 0, 1, 0.975, 1.025)
        hsv = nt.nodes.new('ShaderNodeHueSaturation'); hsv.inputs['Color'].default_value = (*col, 1); nt.links.new(jit, hsv.inputs['Value'])
        base = hsv.outputs[0]
        # v3 (from the friend's corrected counts): striped bodies, a wide label band, clear bodies full of beads
        tcs = nt.nodes.new('ShaderNodeTexCoord'); szz = nt.nodes.new('ShaderNodeSeparateXYZ'); nt.links.new(tcs.outputs['Object'], szz.inputs[0]); Lc = prod['shell_mm']['closed_len'] / 10
        def mth(op, a, bb):
            n = nt.nodes.new('ShaderNodeMath'); n.operation = op
            for sock, v in ((n.inputs[0], a), (n.inputs[1], bb)):
                if isinstance(v, (int, float)): sock.default_value = v
                else: nt.links.new(v, sock)
            return n.outputs[0]
        def zmask(f0, f1): return mth('MULTIPLY', mth('GREATER_THAN', szz.outputs['Z'], (f0 - 0.5) * Lc), mth('LESS_THAN', szz.outputs['Z'], (f1 - 0.5) * Lc))
        beads = prod.get('beads') if part == 'body' else None
        if beads:
            vo = nt.nodes.new('ShaderNodeTexVoronoi'); vo.inputs['Scale'].default_value = beads.get('scale', 11); nt.links.new(tcs.outputs['Object'], vo.inputs['Vector'])
            base = mix(nt, maprange(nt, vo.outputs['Distance'], 0.28, 0.5, 0, 1), hex_lin(beads['hex']), hex_lin(beads.get('gap_hex', '#6f6a60')))
        st = prod.get('stripes')
        if st and st['part'] == part:
            fr = nt.nodes.new('ShaderNodeMath'); fr.operation = 'FRACT'; nt.links.new(maprange(nt, szz.outputs['Z'], (st['z0'] - 0.5) * Lc, (st['z1'] - 0.5) * Lc, 0, st['n']), fr.inputs[0])
            base = mix(nt, mth('MULTIPLY', mth('LESS_THAN', fr.outputs[0], st.get('duty', 0.45)), zmask(st['z0'], st['z1'])), base, hex_lin(st['hex']))
        bd = prod.get('band')
        if bd and bd['part'] == part: base = mix(nt, zmask(bd['z0'], bd['z1']), base, hex_lin(bd['hex']))
        if ink_col:
            tc = nt.nodes.new('ShaderNodeTexCoord'); ink = img_node(nt, f"imprint-{prod['id']}.png", ext='EXTEND'); nt.links.new(tc.outputs['UV'], ink.inputs[0])
            dens = nt.nodes.new('ShaderNodeTexNoise'); dens.inputs['Scale'].default_value = 90; nt.links.new(tc.outputs['Object'], dens.inputs['Vector'])
            fac = nt.nodes.new('ShaderNodeMath'); fac.operation = 'MULTIPLY'; nt.links.new(ink.outputs[0], fac.inputs[0]); nt.links.new(maprange(nt, dens.outputs['Fac'], 0.3, 0.7, 0.8, 1.0), fac.inputs[1])
            base = mix(nt, fac.outputs[0], base, ink_col)
            nt.links.new(maprange(nt, fac.outputs[0], 0, 1, gloss, 0.42), b.inputs['Roughness'])
        else:
            b.inputs['Roughness'].default_value = gloss
        nt.links.new(base, b.inputs['Base Color'])
        b.inputs['Coat Weight'].default_value = 1.0 if beads else rng.uniform(0.4, 0.9); b.inputs['Coat Roughness'].default_value = rng.uniform(0.02, 0.05)
        b.inputs['Subsurface Weight'].default_value = rng.uniform(0.04, 0.12); b.inputs['Subsurface Radius'].default_value = (1.0, 0.8, 0.6); b.inputs['Subsurface Scale'].default_value = 0.04
        noise = nt.nodes.new('ShaderNodeTexNoise'); noise.inputs['Scale'].default_value = 25; noise.inputs['Detail'].default_value = 6
        tc2 = nt.nodes.new('ShaderNodeTexCoord'); nt.links.new(tc2.outputs['Object'], noise.inputs['Vector'])
        bump = nt.nodes.new('ShaderNodeBump'); bump.inputs['Strength'].default_value = rng.uniform(0.02, 0.06); bump.inputs['Distance'].default_value = 0.002
        nt.links.new(noise.outputs['Fac'], bump.inputs['Height']); nt.links.new(bump.outputs[0], b.inputs['Normal'])
        mats.append(m)
    return mats, info


def stone_material(rng, kind):
    """v3 benches: 'granite' (speckled beige, like the owner's counter), 'dark' stone, 'steel', 'wood'."""
    m, nt, b = new_mat('bench'); tc = nt.nodes.new('ShaderNodeTexCoord'); info = dict(kind=kind)
    def noise(scale, detail=6, rough=0.6):
        n = nt.nodes.new('ShaderNodeTexNoise'); n.inputs['Scale'].default_value = scale; n.inputs['Detail'].default_value = detail; n.inputs['Roughness'].default_value = rough; nt.links.new(tc.outputs['Object'], n.inputs['Vector']); return n.outputs['Fac']
    def specks(scale, thr):
        v = nt.nodes.new('ShaderNodeTexVoronoi'); v.inputs['Scale'].default_value = scale; v.inputs['Randomness'].default_value = 1.0; nt.links.new(tc.outputs['Object'], v.inputs['Vector']); return maprange(nt, v.outputs['Distance'], thr * 0.6, thr, 1, 0)
    if kind in ('granite', 'dark'):
        def blot(scale, lo, hi, detail=3.0):   # irregular islands from thresholded noise, each layer shifted to a different place
            mp = nt.nodes.new('ShaderNodeMapping'); mp.inputs['Location'].default_value = (rng.uniform(0, 500), rng.uniform(0, 500), rng.uniform(0, 500)); nt.links.new(tc.outputs['Object'], mp.inputs[0])
            n = nt.nodes.new('ShaderNodeTexNoise'); n.inputs['Scale'].default_value = scale; n.inputs['Detail'].default_value = detail; n.inputs['Roughness'].default_value = 0.65; nt.links.new(mp.outputs[0], n.inputs['Vector'])
            return maprange(nt, n.outputs['Fac'], lo, hi, 0, 1)
        if kind == 'granite': tan, cream, black, quartz, brown = (0.30, 0.22, 0.14), (0.52, 0.44, 0.33), (0.012, 0.011, 0.01), (0.62, 0.6, 0.56), (0.11, 0.055, 0.025)
        else: tan, cream, black, quartz, brown = (0.02, 0.02, 0.022), (0.05, 0.05, 0.055), (0.004, 0.004, 0.004), (0.3, 0.3, 0.32), (0.08, 0.07, 0.06)
        base = mix(nt, blot(0.25, 0.35, 0.65, 6.0), tan, cream)
        base = mix(nt, blot(0.9, 0.56, 0.6), base, quartz)
        base = mix(nt, blot(1.5, 0.6, 0.63), base, brown)
        base = mix(nt, blot(1.1, 0.61, 0.635, 4.0), base, black)
        base = mix(nt, blot(4.0, 0.66, 0.68), base, black)
        nt.links.new(base, b.inputs['Base Color']); b.inputs['Roughness'].default_value = rng.uniform(0.08, 0.2)
    elif kind == 'steel':
        b.inputs['Base Color'].default_value = (0.6, 0.6, 0.6, 1); b.inputs['Metallic'].default_value = 1.0
        mp = nt.nodes.new('ShaderNodeMapping'); mp.inputs['Scale'].default_value = (0.02, 3.0, 1); nt.links.new(tc.outputs['Object'], mp.inputs[0])
        n = nt.nodes.new('ShaderNodeTexNoise'); n.inputs['Scale'].default_value = 8; n.inputs['Detail'].default_value = 8; nt.links.new(mp.outputs[0], n.inputs['Vector'])
        nt.links.new(maprange(nt, n.outputs['Fac'], 0.3, 0.7, 0.22, 0.42), b.inputs['Roughness'])
    else:   # wood
        mp = nt.nodes.new('ShaderNodeMapping'); mp.inputs['Scale'].default_value = (0.03, 0.4, 1); nt.links.new(tc.outputs['Object'], mp.inputs[0])
        n = nt.nodes.new('ShaderNodeTexNoise'); n.inputs['Scale'].default_value = 6; n.inputs['Detail'].default_value = 10; nt.links.new(mp.outputs[0], n.inputs['Vector'])
        nt.links.new(mix(nt, maprange(nt, n.outputs['Fac'], 0.35, 0.65, 0, 1), (0.16, 0.07, 0.025), (0.38, 0.2, 0.08)), b.inputs['Base Color']); b.inputs['Roughness'].default_value = rng.uniform(0.3, 0.5)
    return m, info


def pill_materials(rng, prod):
    """v3: one material for a tablet (coated or chalky) or a softgel (see-through or opaque gelatin)."""
    col = hex_lin(prod['body_hex']); kind = prod['kind']
    if kind == 'softgel' and prod.get('clear', True):
        m = plain_mat('softgel', col, rng.uniform(0.03, 0.07), **{'Transmission Weight': 0.93, 'IOR': 1.47, 'Coat Weight': 0.6, 'Coat Roughness': 0.03})
        return [m], dict(kind='softgel-clear')
    m, nt, b = new_mat('pill'); oi = nt.nodes.new('ShaderNodeObjectInfo'); hsv = nt.nodes.new('ShaderNodeHueSaturation'); hsv.inputs['Color'].default_value = (*col, 1)
    nt.links.new(maprange(nt, oi.outputs['Random'], 0, 1, 0.97, 1.03), hsv.inputs['Value']); nt.links.new(hsv.outputs[0], b.inputs['Base Color'])
    coated = prod.get('coated', kind == 'softgel'); r = rng.uniform(0.22, 0.34) if coated else rng.uniform(0.62, 0.8)
    b.inputs['Roughness'].default_value = 0.08 if kind == 'softgel' else r
    if coated: b.inputs['Coat Weight'].default_value = rng.uniform(0.3, 0.7); b.inputs['Coat Roughness'].default_value = rng.uniform(0.05, 0.15)
    b.inputs['Subsurface Weight'].default_value = 0.1; b.inputs['Subsurface Radius'].default_value = (1.0, 0.9, 0.8); b.inputs['Subsurface Scale'].default_value = 0.05
    tc = nt.nodes.new('ShaderNodeTexCoord'); n = nt.nodes.new('ShaderNodeTexNoise'); n.inputs['Scale'].default_value = 60 if coated else 140; n.inputs['Detail'].default_value = 6; nt.links.new(tc.outputs['Object'], n.inputs['Vector'])
    base = hsv.outputs[0]
    if prod.get('bilayer_hex'):
        sx = nt.nodes.new('ShaderNodeSeparateXYZ'); nt.links.new(tc.outputs['Object'], sx.inputs[0]); base = mix(nt, maprange(nt, sx.outputs['X'], -0.004, 0.004, 0, 1), base, hex_lin(prod['bilayer_hex']))
    if prod.get('speckle_hex'):
        sp = nt.nodes.new('ShaderNodeTexNoise'); sp.inputs['Scale'].default_value = prod.get('speckle_scale', 70); sp.inputs['Detail'].default_value = 2; nt.links.new(tc.outputs['Object'], sp.inputs['Vector'])
        base = mix(nt, maprange(nt, sp.outputs['Fac'], 0.62, 0.66, 0, 1), base, hex_lin(prod['speckle_hex']))
    if base is not hsv.outputs[0]: nt.links.new(base, b.inputs['Base Color'])
    bump = nt.nodes.new('ShaderNodeBump'); bump.inputs['Strength'].default_value = 0.03 if coated else 0.12; bump.inputs['Distance'].default_value = 0.002; nt.links.new(n.outputs['Fac'], bump.inputs['Height'])
    st = prod.get('stamp')
    if st:   # {file, len_cm, wid_cm, ink: None (pressed into the tablet) | hex (printed)}; reads left to right seen from outside
        sp3 = nt.nodes.new('ShaderNodeSeparateXYZ'); nt.links.new(tc.outputs['Object'], sp3.inputs[0]); cu = nt.nodes.new('ShaderNodeCombineXYZ')
        nt.links.new(maprange(nt, sp3.outputs['Z'], -st['len_cm'] / 2, st['len_cm'] / 2, 0, 1), cu.inputs[0]); nt.links.new(maprange(nt, sp3.outputs['Y'], -st['wid_cm'] / 2, st['wid_cm'] / 2, 1, 0), cu.inputs[1])
        si = img_node(nt, st['file'], ext='CLIP'); nt.links.new(cu.outputs[0], si.inputs[0])
        sm = nt.nodes.new('ShaderNodeMath'); sm.operation = 'MULTIPLY'; nt.links.new(si.outputs[0], sm.inputs[0]); nt.links.new(maprange(nt, sp3.outputs['X'], 0.0, 0.01, 0.0, 1.0), sm.inputs[1])
        if st.get('ink'): base = mix(nt, sm.outputs[0], base, hex_lin(st['ink']))
        else:
            dk = nt.nodes.new('ShaderNodeHueSaturation'); dk.inputs['Value'].default_value = 0.8; nt.links.new(base, dk.inputs['Color']); base = mix(nt, sm.outputs[0], base, dk.outputs[0])
            ih = nt.nodes.new('ShaderNodeMath'); ih.operation = 'SUBTRACT'; ih.inputs[0].default_value = 1.0; nt.links.new(sm.outputs[0], ih.inputs[1])
            bs = nt.nodes.new('ShaderNodeBump'); bs.inputs['Strength'].default_value = 1.0; bs.inputs['Distance'].default_value = 0.022; nt.links.new(ih.outputs[0], bs.inputs['Height']); nt.links.new(bump.outputs[0], bs.inputs['Normal']); bump = bs
        nt.links.new(base, b.inputs['Base Color'])
    if prod.get('scored'):   # break line across the width of one face (object space: long axis z, width y, thickness x)
        sep = nt.nodes.new('ShaderNodeSeparateXYZ'); nt.links.new(tc.outputs['Object'], sep.inputs[0]); az = nt.nodes.new('ShaderNodeMath'); az.operation = 'ABSOLUTE'; nt.links.new(sep.outputs['Z'], az.inputs[0])
        groove = maprange(nt, az.outputs[0], 0.0, 0.035, 0.0, 1.0); top = maprange(nt, sep.outputs['X'], 0.0, -0.01 if st else 0.01, 0.0, 1.0)
        g2 = nt.nodes.new('ShaderNodeMath'); g2.operation = 'MAXIMUM'; nt.links.new(groove, g2.inputs[0]); inv = nt.nodes.new('ShaderNodeMath'); inv.operation = 'SUBTRACT'; inv.inputs[0].default_value = 1.0; nt.links.new(top, inv.inputs[1]); nt.links.new(inv.outputs[0], g2.inputs[1])
        bump2 = nt.nodes.new('ShaderNodeBump'); bump2.inputs['Strength'].default_value = 1.0; bump2.inputs['Distance'].default_value = 0.03; nt.links.new(g2.outputs[0], bump2.inputs['Height']); nt.links.new(bump.outputs[0], bump2.inputs['Normal']); bump = bump2
    nt.links.new(bump.outputs[0], b.inputs['Normal'])
    return [m], dict(kind=kind, coated=coated, roughness=r)


def table_material(rng, kind=None):
    if kind: return stone_material(rng, kind)
    m, nt, b = new_mat('bench'); tint = rng.choice([(0.80, 0.80, 0.78), (0.84, 0.83, 0.80), (0.76, 0.77, 0.77), (0.86, 0.86, 0.85), (0.78, 0.76, 0.72)])
    tc = nt.nodes.new('ShaderNodeTexCoord'); mp = nt.nodes.new('ShaderNodeMapping'); mp.inputs['Scale'].default_value = (1 / 120, 1 / 120, 1); nt.links.new(tc.outputs['Object'], mp.inputs[0])
    wear = img_node(nt, 'table-wear.png'); nt.links.new(mp.outputs[0], wear.inputs[0])
    base = mix(nt, maprange(nt, wear.outputs[0], 0, 1, 0, 0.35), tint, tuple(c * 0.8 for c in tint))
    speck = rng.random() < 0.45; info = dict(tint=tint, speckle=speck)
    if speck:
        vor = nt.nodes.new('ShaderNodeTexVoronoi'); vor.inputs['Scale'].default_value = rng.uniform(4, 9); nt.links.new(tc.outputs['Object'], vor.inputs['Vector'])
        dots = maprange(nt, vor.outputs['Distance'], 0.0, rng.uniform(0.06, 0.12), 1, 0)
        base = mix(nt, dots, base, rng.choice([(0.35, 0.35, 0.36), (0.5, 0.48, 0.45), (0.25, 0.25, 0.27)]))
    nt.links.new(base, b.inputs['Base Color'])
    r0 = rng.uniform(0.28, 0.55); nt.links.new(maprange(nt, wear.outputs[0], 0, 1, r0, min(0.9, r0 + 0.3)), b.inputs['Roughness'])
    info['roughness'] = r0; return m, info


def textured_mat(name, file, rough=0.8, colorspace='sRGB'):
    m, nt, b = new_mat(name); t = img_node(nt, file, colorspace, 'CLIP'); tc = nt.nodes.new('ShaderNodeTexCoord'); nt.links.new(tc.outputs['UV'], t.inputs[0])
    nt.links.new(t.outputs[0], b.inputs['Base Color']); b.inputs['Roughness'].default_value = rough; return m


def plain_mat(name, col, rough, **kw):
    m, nt, b = new_mat(name); b.inputs['Base Color'].default_value = (*col, 1); b.inputs['Roughness'].default_value = rough
    for k, v in kw.items(): b.inputs[k].default_value = v
    if kw.get('Transmission Weight', 0) > 0.5:
        tr = nt.nodes.new('ShaderNodeBsdfTransparent'); tr.inputs[0].default_value = (*[min(1, c * 1.6) for c in col], 1)
        out = next(n for n in nt.nodes if n.type == 'OUTPUT_MATERIAL'); lp = nt.nodes.new('ShaderNodeLightPath'); mx = nt.nodes.new('ShaderNodeMixShader')
        nt.links.new(lp.outputs['Is Shadow Ray'], mx.inputs[0]); nt.links.new(b.outputs[0], mx.inputs[1]); nt.links.new(tr.outputs[0], mx.inputs[2]); nt.links.new(mx.outputs[0], out.inputs['Surface'])
    return m


# ---------------------------------------------------------------- geometry
def capsule_mesh(prod, segs=64):
    s = prod['shell_mm']; L = s['closed_len'] / 10; rb = s['body_od'] / 20; rc = s['cap_od'] / 20; zs = L / 2 - s['cap_len'] / 10
    z0 = -L / 2; hb = rb * 1.06; hc = rc * 1.06; prof = []
    for k in range(1, 15): t = k / 14 * math.pi / 2; prof.append((z0 + hb * (1 - math.cos(t)), rb * math.sin(t), 0))
    prof += [(zs - 0.02, rb, 0), (zs - 0.004, rb, 0), (zs, rb + 0.45 * (rc - rb), 1), (zs + 0.006, rc, 1), (zs + 0.03, rc, 1)]
    for k in range(1, 15): t = k / 14 * math.pi / 2; prof.append((L / 2 - hc * (1 - math.cos(t)), rc * math.sin(t), 1))
    prof.sort(key=lambda p: p[0])
    bm = bmesh.new(); uv = bm.loops.layers.uv.new('UVMap'); rings = []
    tip0 = bm.verts.new((0, 0, z0)); tip1 = bm.verts.new((0, 0, L / 2))
    for z, r, _ in prof: rings.append([bm.verts.new((r * math.cos(2 * math.pi * j / segs), r * math.sin(2 * math.pi * j / segs), z)) for j in range(segs)])
    def u_of(z): return (z - z0) / L
    def face(vs, uvs, mi):
        f = bm.faces.new(vs); f.material_index = mi; f.smooth = True
        for lp, q in zip(f.loops, uvs): lp[uv].uv = q
    for j in range(segs):
        j1 = (j + 1) % segs; v0, v1 = 1 - j / segs, 1 - (j + 1) / segs
        face([tip0, rings[0][j1], rings[0][j]], [(0, (v0 + v1) / 2), (u_of(prof[0][0]), v1), (u_of(prof[0][0]), v0)], 0)
        for k in range(len(prof) - 1):
            za, zb = prof[k][0], prof[k + 1][0]; mi = 1 if (za + zb) / 2 > zs else 0
            face([rings[k][j], rings[k][j1], rings[k + 1][j1], rings[k + 1][j]], [(u_of(za), v0), (u_of(za), v1), (u_of(zb), v1), (u_of(zb), v0)], mi)
        face([rings[-1][j], rings[-1][j1], tip1], [(u_of(prof[-1][0]), v0), (u_of(prof[-1][0]), v1), (1, (v0 + v1) / 2)], 1)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    me = bpy.data.meshes.new('capsule-' + prod['id']); bm.to_mesh(me); bm.free()
    return me, dict(length=L, r_body=rb, r_cap=rc, seam_z=zs)


def pill_mesh(prod, proxy=False):
    """v3: tablet (round / oval / oblong, biconvex) or softgel (ellipsoid). Local axes match the capsule so the
    same pose code lays it flat: long axis z (len_mm), width y (wid_mm), thickness x (thk_mm)."""
    kind = prod['kind']; L, W, T = (prod[k] / 10 for k in ('len_mm', 'wid_mm', 'thk_mm')); a, b = L / 2, W / 2; bm = bmesh.new()
    if kind == 'softgel':
        bmesh.ops.create_uvsphere(bm, u_segments=12 if proxy else 48, v_segments=6 if proxy else 24, radius=1.0); bmesh.ops.scale(bm, vec=(T / 2, b, a), verts=bm.verts)
    else:
        shape = prod.get('shape', 'round'); band = prod.get('band_mm', prod['thk_mm'] * 0.45) / 10; cup = (T - band) / 2; s = a - b
        def R(th):
            c, sn = abs(math.cos(th)), abs(math.sin(th))
            if shape.startswith('poly'):   # rounded n-sided tablet, wid_mm = across flats
                w = 2 * math.pi / int(shape[4:]); return min(b / math.cos((th % w) - w / 2), b * prod.get('corner', 1.1))
            if shape == 'oval': return a * b / math.hypot(b * c, a * sn)
            if shape == 'oblong' and s > 1e-6:
                if sn > 1e-9 and (b / sn) * c <= s: return b / sn
                return s * c + math.sqrt(max(0.0, b * b - s * s * sn * sn))
            return b
        K, J = (2, 18) if proxy else (10, 72); rings = {1: [], -1: []}; centre = {1: bm.verts.new((band / 2 + cup, 0, 0)), -1: bm.verts.new((-band / 2 - cup, 0, 0))}
        for sg in (1, -1):
            for k in range(1, K + 1):
                rho = k / K; h = band / 2 + cup * (1 - rho ** 2.4)
                rings[sg].append([bm.verts.new((sg * h, rho * R(2 * math.pi * j / J) * math.sin(2 * math.pi * j / J), rho * R(2 * math.pi * j / J) * math.cos(2 * math.pi * j / J))) for j in range(J)])
            for j in range(J):
                j1 = (j + 1) % J; bm.faces.new([centre[sg], rings[sg][0][j], rings[sg][0][j1]])
                for k in range(K - 1): bm.faces.new([rings[sg][k][j], rings[sg][k + 1][j], rings[sg][k + 1][j1], rings[sg][k][j1]])
        for j in range(J):
            j1 = (j + 1) % J; bm.faces.new([rings[1][-1][j], rings[-1][-1][j], rings[-1][-1][j1], rings[1][-1][j1]])
    for f in bm.faces: f.smooth = True
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    sample = [tuple(v.co) for v in bm.verts][::3]
    me = bpy.data.meshes.new(f"{kind}-{prod['id']}"); bm.to_mesh(me); bm.free()
    return me, dict(length=L, r_body=b, r_cap=b, seam_z=0.0, r_z=T / 2, sample=sample)


def build_foreign(rng, spec, caps, geo, colour, info):
    """v3: things that must not be counted, resting on the tray floor away from the pills (names start with 'foreign')."""
    placed = [(ob.matrix_world.translation.x, ob.matrix_world.translation.y, geo['length'] / 2 + 0.15) for ob in caps]; made = []
    def spot(r, near=None):
        for _ in range(600):
            if near: x, y = near[0] + rng.uniform(-2.2, 2.2), near[1] + rng.uniform(-2.2, 2.2)
            else: x, y = rng.uniform(-HX, HX), rng.uniform(-HY, HY)
            if round_box_2d(x, y, HX, HY, G['corner_r']) < -(r + 0.3) and all(math.hypot(x - px, y - py) > r + pr for px, py, pr in placed): placed.append((x, y, r)); return x, y
        return None
    for kind in spec.get('foreign', []):
        if kind == 'desiccant':
            s = spot(1.3)
            if s: o = cylinder('foreign-desiccant', 0.7, 1.9); o.data.materials.append(plain_mat('desiccant', (0.78, 0.78, 0.76), 0.5, **{'Subsurface Weight': 0.1})); o.location = (s[0], s[1], 0.7); o.rotation_euler = (math.pi / 2, 0, rng.uniform(0, 6.3)); made.append(kind)
        elif kind == 'cap':
            s = spot(1.8)
            if s: o = cylinder('foreign-cap', 1.6, 1.2); o.data.materials.append(plain_mat('fcap', (0.8, 0.8, 0.78), 0.4)); o.location = (s[0], s[1], 0.6); made.append(kind)
        elif kind == 'cotton':
            s = spot(1.6)
            if s:
                bm = bmesh.new(); bmesh.ops.create_icosphere(bm, subdivisions=5, radius=1.3); me = bpy.data.meshes.new('cotton'); bm.to_mesh(me); bm.free()
                for p in me.polygons: p.use_smooth = True
                o = link(bpy.data.objects.new('foreign-cotton', me)); tex = bpy.data.textures.new('cottontex', 'CLOUDS'); tex.noise_scale = 0.5; tex.noise_depth = 4
                dm = o.modifiers.new('fluff', 'DISPLACE'); dm.texture = tex; dm.strength = 0.5; o.scale = (1, rng.uniform(0.8, 1.2), 0.62); o.location = (s[0], s[1], 0.8)
                o.data.materials.append(plain_mat('cottonm', (0.86, 0.86, 0.84), 1.0, **{'Subsurface Weight': 0.6, 'Sheen Weight': 0.8})); made.append(kind)
        elif kind == 'crumbs':
            m = plain_mat('crumb', colour, 0.8); n = 0
            for _ in range(spec.get('crumbs', 28)):
                base = rng.choice(caps).matrix_world.translation if caps and rng.random() < 0.7 else None; r = rng.uniform(0.03, 0.11); s = spot(r, near=(base.x, base.y) if base is not None else None)
                if not s: continue
                bm = bmesh.new(); bmesh.ops.create_icosphere(bm, subdivisions=1, radius=r)
                for v in bm.verts: v.co *= rng.uniform(0.7, 1.2)
                me = bpy.data.meshes.new('crumb'); bm.to_mesh(me); bm.free(); o = link(bpy.data.objects.new(f'foreign-crumb-{n}', me)); o.data.materials.append(m); o.scale = (1, 1, 0.6); o.location = (s[0], s[1], r * 0.55); n += 1
            made.append(f'crumbs x{n}')
    info['foreign'] = made


def link(ob): bpy.context.scene.collection.objects.link(ob); return ob


def cylinder(name, r, h, segs=48, cap=True):
    bm = bmesh.new(); bmesh.ops.create_cone(bm, cap_ends=cap, cap_tris=False, segments=segs, radius1=r, radius2=r, depth=h, calc_uvs=True)
    me = bpy.data.meshes.new(name); bm.to_mesh(me); bm.free()
    for p in me.polygons: p.use_smooth = abs(p.normal.z) < 0.5
    return link(bpy.data.objects.new(name, me))


def plane(name, w, h, nx=1, ny=1, curl=0.0, rng=None):
    bm = bmesh.new(); uv = bm.loops.layers.uv.new('UVMap'); vs = {}
    for i in range(nx + 1):
        for j in range(ny + 1):
            x, y = (i / nx - 0.5) * w, (j / ny - 0.5) * h; z = curl * (max(0, (i / nx) - 0.8) / 0.2) ** 2 if curl else 0
            vs[i, j] = bm.verts.new((x, y, z))
    for i in range(nx):
        for j in range(ny):
            f = bm.faces.new([vs[i, j], vs[i + 1, j], vs[i + 1, j + 1], vs[i, j + 1]])
            for lp, (a, b) in zip(f.loops, [(i, j), (i + 1, j), (i + 1, j + 1), (i, j + 1)]): lp[uv].uv = (a / nx, b / ny)
    me = bpy.data.meshes.new(name); bm.to_mesh(me); bm.free(); return link(bpy.data.objects.new(name, me))


def box(name, dx, dy, dz):
    bm = bmesh.new(); bmesh.ops.create_cube(bm, size=1); bmesh.ops.scale(bm, vec=(dx, dy, dz), verts=bm.verts)
    me = bpy.data.meshes.new(name); bm.to_mesh(me); bm.free(); return link(bpy.data.objects.new(name, me))


def round_box_2d(px, py, hx, hy, r):
    rr = (r['tr'] if py > 0 else r['br']) if px > 0 else (r['tl'] if py > 0 else r['bl'])
    qx, qy = abs(px) - hx + rr, abs(py) - hy + rr
    return min(max(qx, qy), 0) + math.hypot(max(qx, 0), max(qy, 0)) - rr


def floor_polygon(n=24):
    """Main counting-floor outline at z=0 (rounded rectangle), counter-clockwise, cm."""
    r = G['corner_r']; pts = []
    for (cx, cy, key, a0) in ((HX, -HY, 'br', -90), (HX, HY, 'tr', 0), (-HX, HY, 'tl', 90), (-HX, -HY, 'bl', 180)):
        rr = r[key]; ox, oy = cx - math.copysign(rr, cx), cy - math.copysign(rr, cy)
        for k in range(n // 4 + 1): a = math.radians(a0 + 90 * k / (n // 4)); pts.append((ox + rr * math.cos(a), oy + rr * math.sin(a)))
    return pts


# ---------------------------------------------------------------- placement + physics
def seg_dist(p1, q1, p2, q2):
    d1, d2, r = q1 - p1, q2 - p2, p1 - p2; a, e, f = d1 @ d1, d2 @ d2, d2 @ r; c = d1 @ r; b = d1 @ d2; den = a * e - b * b
    s = np.clip((b * f - c * e) / den, 0, 1) if den > 1e-9 else 0.0
    t = (b * s + f) / e
    if t < 0: t, s = 0.0, np.clip(-c / a, 0, 1)
    elif t > 1: t, s = 1.0, np.clip((b - c) / a, 0, 1)
    return float(np.linalg.norm(p1 + d1 * s - p2 - d2 * t))


def spawn_poses(rng, spec, geo):
    n = spec['count']; L, rc = geo['length'], geo['r_cap']; half = max(L / 2 - rc, 1e-3); foot = L * 2 * rc; lay = spec['layout']
    rz = geo.get('r_z', rc); flat = spec['product'].get('kind') == 'tablet'; on_edge = spec.get('on_edge', 0.0)
    def roll(): return (math.pi / 2 if rng.random() < on_edge else rng.choice([0.0, math.pi])) + rng.gauss(0, 0.04) if flat else rng.uniform(0, 2 * math.pi)
    density = {'sparse': 0.10, 'spread': 0.30, 'dense': 1.0, 'heap': 3.0, 'groups': 0.4, 'edge': 0.5, 'raft': 1.0}[lay]
    area = max(4.0, min(HX * HY * 4 * 0.86, n * foot / density)); ar = rng.uniform(0.7, 1.4)
    ax = min(HX - 0.6, math.sqrt(area / math.pi * ar)); ay = min(HY - 0.6, area / math.pi / ax); rot = rng.uniform(0, math.pi)
    cx = rng.uniform(-(HX - ax), HX - ax) * 0.8 if ax < HX - 0.6 else 0.0; cy = rng.uniform(-(HY - ay), HY - ay) * 0.8 if ay < HY - 0.6 else 0.0
    walls = rng.choice([['l'], ['r'], ['b'], ['l', 'b'], ['r', 'b'], ['l', 'r']]) if lay == 'edge' else []
    band = max(2.2, min(4.5, n * foot / (0.55 * 2 * (HX + HY) * max(1, len(walls)))))
    gap = {'sparse': 0.45, 'spread': 0.06, 'dense': 0.01, 'heap': 0.005, 'groups': 0.02, 'edge': 0.02, 'raft': 0.01}[lay]
    layers = [[] for _ in range(60)]; poses = []

    def inside(x, y):
        if round_box_2d(x, y, HX, HY, G['corner_r']) > -(rc + 0.12): return False
        if lay == 'sparse': return True
        if lay == 'edge':   # band hugging the chosen wall(s)
            return any({'l': x < -HX + band, 'r': x > HX - band, 'b': y < -HY + band}[w] for w in walls)
        u, v = (x - cx) * math.cos(rot) + (y - cy) * math.sin(rot), -(x - cx) * math.sin(rot) + (y - cy) * math.cos(rot)
        return (u / ax) ** 2 + (v / ay) ** 2 <= 1

    def fits(x, y, yaw, k, g):
        d = np.array([math.cos(yaw), math.sin(yaw)]); a2, b2 = np.array([x, y]) - half * d, np.array([x, y]) + half * d
        for q in (a2, b2):
            if round_box_2d(q[0], q[1], HX, HY, G['corner_r']) > -(rc + 0.1): return None
        a3, b3 = np.append(a2, 0), np.append(b2, 0)
        for (p, q) in layers[k]:
            if seg_dist(a3, b3, p, q) < 2 * rc + g: return None
        return (a3, b3)

    def add(x, y, yaw, k, seg, drop=0.03):
        layers[k].append(seg); step = 2 * rz + (0.012 if lay == 'heap' else 0.03 if lay == 'dense' else 0.12)
        z = rz + drop + k * step; pitch = rng.gauss(0, math.radians(4 if k == 0 else 10) * (0.3 if flat else 1))
        poses.append(dict(x=x, y=y, z=z, yaw=yaw, pitch=pitch, roll=roll(), layer=k))

    if lay == 'raft':   # v3: one close-packed layer (hexagonal), pills touching on all sides
        pitch = 2 * rc + 0.012; px = L + 0.02 if half > 0.01 else pitch; py = pitch if half > 0.01 else pitch * 0.866; cand = []; mm = int(2 * max(HX, HY) / pitch) + 2
        for i in range(-mm, mm + 1):
            for j in range(-mm, mm + 1):
                x, y = cx + (i + 0.5 * (j % 2)) * px, cy + j * py
                if max(round_box_2d(x - half, y, HX, HY, G['corner_r']), round_box_2d(x + half, y, HX, HY, G['corner_r'])) < -(rc + 0.15): cand.append((math.hypot(x - cx, y - cy), x, y))
        for _, x, y in sorted(cand)[:n]:
            a3 = np.array([x, y, 0.0]); layers[0].append((a3 - np.array([half, 0, 0]), a3 + np.array([half, 0, 0])))
            poses.append(dict(x=x + rng.gauss(0, 0.002), y=y + rng.gauss(0, 0.002), z=rz + 0.02, yaw=rng.uniform(0, math.pi) if half < 0.01 else 0.0, pitch=0.0, roll=roll(), layer=0))
    if lay == 'groups':   # counting groups of five, visibly separated from each other
        tries = 0
        while len(poses) < n and tries < 400:
            tries += 1; k5 = min(5, n - len(poses)); yaw = rng.uniform(0, math.pi); gx, gy = rng.uniform(-HX + 1.2, HX - 1.2), rng.uniform(-HY + 1.2, HY - 1.2)
            nrm = np.array([-math.sin(yaw), math.cos(yaw)]); jit = [yaw + rng.gauss(0, 0.04) for _ in range(k5)]
            cand = [(gx + nrm[0] * (i - (k5 - 1) / 2) * (2 * rc + 0.01), gy + nrm[1] * (i - (k5 - 1) / 2) * (2 * rc + 0.01), jit[i]) for i in range(k5)]
            segs = [fits(px, py, yy, 0, rng.uniform(0.45, 0.9)) for px, py, yy in cand]
            if all(sg is not None for sg in segs):
                for (px, py, yy), sg in zip(cand, segs): add(px, py, yy, 0, sg, drop=0.01)
    n_bridge = int(n * (rng.uniform(0.15, 0.3) if lay == 'heap' else rng.uniform(0.06, 0.18))) if lay in ('heap', 'dense') and n >= 12 else 0
    target = n - n_bridge
    k = 0; attempts = 0
    while len(poses) < target and k < 60:
        attempts += 1
        if lay in ('sparse', 'edge'): x, y = rng.uniform(-HX, HX), rng.uniform(-HY, HY)
        else: x, y = cx + rng.uniform(-ax, ax), cy + rng.uniform(-ay, ay)
        yaw = rng.uniform(0, math.pi)
        if lay == 'edge' and rng.random() < 0.5:
            w = rng.choice(walls); yaw = (math.pi / 2 if w in ('l', 'r') else 0.0) + rng.gauss(0, 0.1)
        if inside(x, y):
            sg = fits(x, y, yaw, k, gap)
            if sg is not None: add(x, y, yaw, k, sg); attempts = 0
        if attempts > 600: k += 1; attempts = 0
    # capsules resting across two others (how real poured piles overlap); physics settles them onto their supports
    base = [p for p in poses if p['layer'] == 0]; tries = 0
    while len(poses) < n and tries < 3000 and len(base) >= 2:
        tries += 1; i, j = rng.sample(range(len(base)), 2); pi, pj = base[i], base[j]; dx, dy = pj['x'] - pi['x'], pj['y'] - pi['y']; d = math.hypot(dx, dy)
        if not (2 * rc * 1.05 < d < L * 0.95): continue
        yaw = math.atan2(dy, dx) + rng.gauss(0, 0.25); mx, my = (pi['x'] + pj['x']) / 2, (pi['y'] + pj['y']) / 2
        kk = max(pi['layer'], pj['layer']) + 1; sg = fits(mx, my, yaw, kk, 0.01)
        if sg is None: continue
        layers[kk].append(sg); poses.append(dict(x=mx, y=my, z=3 * rz + 0.01 + (kk - 1) * 2 * rz, yaw=yaw, pitch=rng.gauss(0, 0.05), roll=roll(), layer=kk, bridge=True))
    while len(poses) < n and k < 60:   # anything the bridges could not place goes back to random fill
        attempts += 1; x, y = cx + rng.uniform(-ax, ax), cy + rng.uniform(-ay, ay); yaw = rng.uniform(0, math.pi)
        if inside(x, y):
            sg = fits(x, y, yaw, k, gap)
            if sg is not None: add(x, y, yaw, k, sg); attempts = 0
        if attempts > 600: k += 1; attempts = 0
    if len(poses) < n: raise RuntimeError(f'spawn failed {len(poses)}/{n}')
    tro = []   # trough capsules start inside the half-pipe (under a closed lid), never interpenetrating each other
    for _ in range(spec.get('trough_distractors', 0)):
        for _try in range(80):
            x, lev = rng.uniform(-HX + 1, HX - 1), 0 if _try < 40 else 1   # second row only when the first is full
            if all(abs(x - tx) > L + 0.08 for tx, tl in tro if tl == lev): break
        else: continue   # trough full: place fewer (labels always come from the settled objects)
        tro.append((x, lev)); z = G['trough_zc'] + rng.uniform(0.0, 0.3) + lev * (2 * rc + 0.08)
        poses.append(dict(x=x, y=TYC + rng.gauss(0, 0.15), z=z, yaw=rng.gauss(0, 0.15), pitch=0, roll=roll(), layer=-1, trough=True))
    for _ in range(spec.get('bench_distractors', 0)):   # loose capsules on the bench beside the tray (never counted)
        side = rng.choice(['l', 'r', 'b']); off = rng.uniform(0.8, 6.0)
        x = {'l': -HX - 0.6 - off, 'r': HX + 0.9 - 0 + off}.get(side, rng.uniform(-HX, HX)); y = {'b': -HY - 1.2 - off, 't': TYC + TR + 0.6 + off}.get(side, rng.uniform(-HY, HY))
        poses.append(dict(x=x, y=y, z=TABLE_Z + rz + 0.3, yaw=rng.uniform(0, math.pi), pitch=0, roll=roll(), layer=-2, bench=True))
    return poses


def rigid(ob, kind, shape, **kw):
    with bpy.context.temp_override(object=ob, active_object=ob, selected_objects=[ob], selected_editable_objects=[ob]):
        bpy.ops.rigidbody.object_add(type=kind)
    rb = ob.rigid_body; rb.collision_shape = shape
    for k, v in kw.items(): setattr(rb, k, v)
    return rb


def simulate(scene, capsules, collider, table, rng, max_frames=420, heavy=False, lid=None, shape='CAPSULE'):
    bpy.ops.rigidbody.world_add(); w = scene.rigidbody_world
    w.substeps_per_frame = 24; w.solver_iterations = 40; w.point_cache.frame_start = 1; w.point_cache.frame_end = max_frames
    scene.gravity = (0, 0, -981.0); scene.render.fps = 60; scene.frame_start, scene.frame_end = 1, max_frames
    rigid(collider, 'PASSIVE', 'MESH', friction=0.5 if not heavy else 1.0, restitution=0.05 if not heavy else 0.0, use_margin=True, collision_margin=0.004)
    rigid(table, 'PASSIVE', 'BOX', friction=0.6, restitution=0.05)
    if lid is not None: rigid(lid, 'PASSIVE', 'MESH', friction=0.5, restitution=0.05, use_margin=True, collision_margin=0.004)
    fr = rng.uniform(0.35, 0.6) if not heavy else 1.0; ld, ad = (0.3, 0.6) if not heavy else (0.9, 0.99)
    for ob in capsules: rigid(ob, 'ACTIVE', shape, mass=0.4, friction=fr, restitution=0.04, linear_damping=ld, angular_damping=ad, use_margin=True, collision_margin=0.0, use_deactivation=True, deactivate_linear_velocity=0.15, deactivate_angular_velocity=0.25)
    prev = None; settled_at = None
    for f in range(1, max_frames + 1):
        scene.frame_set(f)
        if not capsules: break
        if f % 20 == 0:
            cur = np.array([ob.matrix_world.translation[:] for ob in capsules])
            if prev is not None and np.abs(cur - prev).max() < 0.02 and f >= 120: settled_at = f; break   # < 0.2 mm drift per 1/3 s
            prev = cur
    mats = [ob.matrix_world.copy() for ob in capsules]
    bpy.ops.rigidbody.world_remove()
    for ob, m in zip(capsules, mats): ob.matrix_world = m
    for ob in (collider, table, lid):
        if ob is not None and ob.rigid_body:
            with bpy.context.temp_override(object=ob, active_object=ob, selected_objects=[ob]): bpy.ops.rigidbody.object_remove()
    return dict(settled_frame=settled_at, frames=f, friction=fr)


def classify(ob, geo):
    c = ob.matrix_world.translation; ax = (ob.matrix_world.to_3x3() @ Vector((0, 0, 1))).normalized(); half = geo['length'] / 2
    ends = [c - ax * half, c + ax * half]; rc = geo['r_cap']
    if c.z < TABLE_Z + 2 * rc + 0.2 and (abs(c.x) > HX + 0.5 or c.y < -HY - 0.5 or c.y > TYC + TR + 0.5): return 'table'
    if c.y > HY + 0.05 and abs(c.x) < HX + 1.6:
        return 'trough' if all(e.y > HY + 0.1 for e in ends) and c.z < G['trough_h'] else 'ambiguous-divider'
    if round_box_2d(c.x, c.y, HX, HY, G['corner_r']) < 0.2 or (c.x > HX - 1.5 and c.y < -HY + 1.5):
        if any(e.y > HY + 0.12 for e in ends): return 'ambiguous-divider'
        if c.z > 3.2 * rc + 0.5: return 'ambiguous-high'
        return 'main'
    return 'ambiguous-rim'


# ---------------------------------------------------------------- scene
def build_clutter(rng, spec, info, avoid=()):
    objs = []; placed = [(0.0, 1.2, 12.5)] + list(avoid)   # tray keep-out circle and loose bench capsules (x, y, r)
    def spot(r_obj, rmin=12.0, rmax=34.0):
        for _ in range(200):
            a = rng.uniform(0, 2 * math.pi); d = rng.uniform(rmin, rmax); x, y = d * math.cos(a), 1.2 + d * math.sin(a)
            if all(math.hypot(x - px, y - py) > r_obj + pr + 0.5 for px, py, pr in placed): placed.append((x, y, r_obj)); return x, y
        return None
    z0 = TABLE_Z
    papers = rng.randint(0, 2) + (1 if spec.get('paper_under') else 0)
    for k in range(papers):
        w, h = rng.choice([(21.6, 27.9), (21.6, 27.9), (14.0, 21.6), (21.0, 29.7)])
        under = k == 0 and spec.get('paper_under')
        if under: x, y = rng.uniform(-6, 6), rng.uniform(-6, 8)
        else:
            a = rng.uniform(0, 2 * math.pi); d = rng.uniform(16, 30); x, y = d * math.cos(a), 1 + d * math.sin(a)
        # curled edges only where the sheet cannot reach under the tray (a curl would poke through the tray bottom)
        far = math.hypot(x, y - 1.2) - math.hypot(w, h) / 2 > 12.5
        p = plane(f'paper-{k}', w, h, 12, 12, curl=rng.uniform(0, 0.6) if far else 0.0); p.data.materials.append(textured_mat(f'paper-{k}', f'paper-{rng.randint(0, 7):02d}.png', rng.uniform(0.75, 0.9)))
        p.location = (x, y, z0 + 0.004 + 0.003 * k); p.rotation_euler = (0, 0, rng.uniform(-0.6, 0.6) + rng.choice([0, math.pi / 2])); objs.append(('paper', p.name))
    for k in range(spec.get('clutter', 2)):
        kind = rng.choice(['vial', 'vial', 'bottle', 'spatula', 'pen', 'sticky', 'receipt', 'cap'])
        if kind == 'vial':
            r, h = rng.uniform(1.5, 2.3), rng.uniform(5, 8.5); s = spot(r + 1)
            if not s: continue
            v = cylinder('vial', r, h, cap=True); v.data.materials.append(plain_mat('amber', (0.55, 0.16, 0.02), rng.uniform(0.05, 0.15), **{'Transmission Weight': 0.95, 'IOR': 1.5}))
            upright = rng.random() < 0.6
            if upright: v.location = (s[0], s[1], z0 + h / 2)
            else: v.location = (s[0], s[1], z0 + r); v.rotation_euler = (math.pi / 2, 0, rng.uniform(0, 6.3))
            if rng.random() < 0.6:
                cp = cylinder('vialcap', r + 0.15, 1.3); cp.data.materials.append(plain_mat('capwhite', (0.8, 0.8, 0.78), 0.45))
                cp.location = (v.location + (v.rotation_euler.to_matrix() @ Vector((0, 0, h / 2 + 0.65)))) if not upright else Vector((s[0], s[1], z0 + h + 0.65)); cp.rotation_euler = v.rotation_euler
        elif kind == 'bottle':
            r, h = rng.uniform(2.6, 3.4), rng.uniform(8, 11); s = spot(r + 1, 16, 34)
            if not s: continue
            b = cylinder('bottle', r, h); b.data.materials.append(plain_mat('hdpe', (0.78, 0.78, 0.76), 0.4, **{'Subsurface Weight': 0.1}))
            b.location = (s[0], s[1], z0 + h / 2)
            lb = cylinder('label', r + 0.02, h * 0.55, cap=False); lb.data.materials.append(textured_mat('label', f'label-{rng.randint(0, 5):02d}.png', 0.6)); lb.location = (s[0], s[1], z0 + h * 0.45); lb.rotation_euler = (0, 0, rng.uniform(0, 6.3))
            cp = cylinder('bcap', r * 0.8, 1.6); cp.data.materials.append(plain_mat('bcapm', rng.choice([(0.8, 0.8, 0.78), (0.05, 0.05, 0.05), (0.02, 0.1, 0.4)]), 0.5)); cp.location = (s[0], s[1], z0 + h + 0.8)
        elif kind == 'spatula':
            s = spot(7, 11, 22)
            if not s: continue
            steel = rng.random() < 0.4; sp = box('spatula', 2.4, 14, 0.12)
            sp.data.materials.append(plain_mat('steel', (0.62, 0.62, 0.6), 0.22, Metallic=1.0) if steel else plain_mat('spplastic', rng.choice([(0.02, 0.12, 0.3), (0.8, 0.8, 0.78)]), 0.35))
            sp.location = (s[0], s[1], z0 + 0.06); sp.rotation_euler = (0, 0, rng.uniform(0, 6.3))
        elif kind == 'pen':
            s = spot(7, 12, 30)
            if not s: continue
            pn = cylinder('pen', 0.45, 14); pn.data.materials.append(plain_mat('pen', rng.choice([(0.02, 0.02, 0.02), (0.02, 0.05, 0.3), (0.8, 0.8, 0.8), (0.4, 0.02, 0.02)]), 0.35))
            pn.location = (s[0], s[1], z0 + 0.45); pn.rotation_euler = (math.pi / 2, 0, rng.uniform(0, 6.3))
        elif kind in ('sticky', 'receipt'):
            s = spot(5, 12, 30)
            if not s: continue
            w, h, f = ((7.6, 7.6, f'sticky-{rng.randint(0, 3):02d}.png') if kind == 'sticky' else (8, 26, 'receipt-00.png'))
            p = plane(kind, w, h, 6, 6, curl=rng.uniform(0, 0.3)); p.data.materials.append(textured_mat(kind, f, 0.85)); p.location = (s[0], s[1], z0 + 0.01); p.rotation_euler = (0, 0, rng.uniform(0, 6.3))
        elif kind == 'cap':
            s = spot(2.5, 11, 25)
            if not s: continue
            cp = cylinder('loosecap', rng.uniform(1.7, 2.6), 1.3); cp.data.materials.append(plain_mat('lcap', rng.choice([(0.8, 0.8, 0.78), (0.6, 0.6, 0.6)]), 0.45)); cp.location = (s[0], s[1], z0 + 0.65)
        objs.append((kind, None))
    info['clutter'] = [k for k, _ in objs]


def lights(scene, rng, spec, target, up, cam_loc, info):
    w = bpy.data.worlds.new('world'); scene.world = w; w.use_nodes = True; nt = w.node_tree; bg = nt.nodes['Background']
    hdr = rng.choice(['cayley_interior_2k.hdr', 'empty_warehouse_01_2k.hdr', 'studio_small_03_2k.hdr', 'studio_small_05_2k.hdr', 'photo_studio_loft_hall_2k.hdr', 'artist_workshop_2k.hdr'])
    env = nt.nodes.new('ShaderNodeTexEnvironment'); env.image = bpy.data.images.load(str(HDRI / hdr)); mp = nt.nodes.new('ShaderNodeMapping'); tc = nt.nodes.new('ShaderNodeTexCoord')
    nt.links.new(tc.outputs['Generated'], mp.inputs[0]); mp.inputs['Rotation'].default_value = (0, 0, rng.uniform(0, 6.3)); nt.links.new(mp.outputs[0], env.inputs[0]); nt.links.new(env.outputs[0], bg.inputs['Color'])
    world_strength = rng.uniform(0.15, 0.45); world_strength = spec.get('world_strength', world_strength); bg.inputs['Strength'].default_value = world_strength
    kind = spec['light']; L = []
    def area(name, loc, size, size_y, power, kelvin, aim=False):
        l = bpy.data.lights.new(name, 'AREA'); l.shape = 'RECTANGLE'; l.size, l.size_y = size, size_y; l.energy = power; l.color = kelvin_rgb(kelvin)
        o = link(bpy.data.objects.new(name, l)); o.location = loc; o.rotation_euler = (target - Vector(loc)).to_track_quat('-Z', 'Y').to_euler() if aim else (0, 0, rng.uniform(0, 3.14)); L.append(dict(name=name, loc=list(loc), power=power, kelvin=kelvin, size=[size, size_y]))
        return o
    if kind.startswith('panels') or kind == 'mixed':
        kel = {'panels-cool': rng.uniform(4800, 6200), 'panels-neutral': rng.uniform(3900, 4700), 'panels-warm': rng.uniform(3000, 3600), 'mixed': rng.uniform(4500, 6000)}[kind]
        for k in range(rng.randint(1, 4)):
            h = rng.uniform(170, 230); sz = rng.choice([(60, 60), (30, 120), (120, 30), (15, 120), (45, 45), 'disk'])
            o = area(f'panel{k}', (rng.uniform(-140, 140), rng.uniform(-140, 140), TABLE_Z + h), *((50, 50) if sz == 'disk' else sz), rng.uniform(2.0e5, 5.0e5) * (h / 200) ** 2, kel)
            if sz == 'disk': o.data.shape = 'DISK'; o.data.size = rng.uniform(30, 80)
    if kind == 'window':
        s = bpy.data.lights.new('sun', 'SUN'); s.energy = rng.uniform(2.5, 6); s.angle = math.radians(rng.uniform(1, 8)); s.color = kelvin_rgb(rng.uniform(5200, 6500))
        so = link(bpy.data.objects.new('sun', s)); el = math.radians(rng.uniform(25, 60)); az = rng.uniform(0, 6.3); so.rotation_euler = (0, math.pi / 2 - el, az)
        area('window', (220 * math.cos(az), 220 * math.sin(az), TABLE_Z + 120), 150, 100, 1.5e6, 6500, aim=True); L.append(dict(name='sun', elevation=math.degrees(el)))
        area('fill', (rng.uniform(-100, 100), rng.uniform(-100, 100), TABLE_Z + 200), 60, 60, 1.5e5, 4500)
    if kind in ('desk-lamp', 'mixed'):
        l = bpy.data.lights.new('lamp', 'SPOT'); l.energy = rng.uniform(2.5e4, 6e4); l.spot_size = math.radians(rng.uniform(60, 100)); l.shadow_soft_size = rng.uniform(4, 10); l.color = kelvin_rgb(rng.uniform(2700, 3300))
        o = link(bpy.data.objects.new('lamp', l)); a = rng.uniform(0, 6.3); o.location = (45 * math.cos(a), 45 * math.sin(a), TABLE_Z + rng.uniform(40, 65))
        o.rotation_euler = (target - o.location).to_track_quat('-Z', 'Y').to_euler(); L.append(dict(name='lamp', loc=list(o.location), power=l.energy))
        if kind == 'desk-lamp': area('ceiling', (rng.uniform(-100, 100), rng.uniform(-100, 100), TABLE_Z + 210), 60, 60, 1.2e5, 4000)
    # phone, hand and photographer's body: shadow casters invisible to the camera
    if spec.get('body_shadow', True):
        side = -up
        ph = box('phone-occluder', 7.8, 16.3, 0.9); bv = ph.modifiers.new('round', 'BEVEL'); bv.width = 0.9; bv.segments = 6; ph.location = cam_loc + Vector((0, 0, 0.6)); ph.rotation_euler = (0, 0, math.atan2(up.y, up.x) - math.pi / 2)
        arm = cylinder('arm-occluder', 4.0, 45); arm.location = cam_loc + side * 22 + Vector((0, 0, 10)); arm.rotation_euler = (Vector((side.x, side.y, 0.45)).to_track_quat('Z', 'Y')).to_euler()
        body = link(bpy.data.objects.new('body-occluder', bpy.data.meshes.new('body')))
        bm = bmesh.new(); bmesh.ops.create_uvsphere(bm, u_segments=24, v_segments=12, radius=1); bmesh.ops.scale(bm, vec=(22, 14, 30), verts=bm.verts); bm.to_mesh(body.data); bm.free()
        body.location = target + side * rng.uniform(45, 65) + Vector((0, 0, rng.uniform(35, 60)))
        for o in (ph, arm, body): o.visible_camera = False; o.visible_glossy = False; o.data.materials.append(plain_mat('occ', (0.05, 0.05, 0.05), 0.8))
    # v3: reflections placed at the mirror angle so the highlight lands on a chosen floor point
    for gi, g in enumerate(spec.get('glare', [])):
        if g['kind'] == 'torch':   # phone light on: a small lamp beside the lens
            l = bpy.data.lights.new(f'glare{gi}-torch', 'POINT'); l.energy = 5000 * g.get('power', 1.0); l.shadow_soft_size = 0.25; l.color = kelvin_rgb(5500)
            o = link(bpy.data.objects.new(l.name, l)); o.location = cam_loc + up * 1.4 + Vector((0, 0, -0.3)); L.append(dict(name=l.name, glare='torch', loc=list(o.location), power=l.energy)); continue
        at = spec.get('_pile', (0, 0)) if g.get('at', 'pile') == 'pile' else g['at']; off = g.get('offset', (0, 0))
        P = Vector((at[0] + off[0], at[1] + off[1], 0)); d = (P - cam_loc).normalized(); r = Vector((d.x, d.y, -d.z)); c0 = P + r * g.get('dist', 160)
        q = (P - c0).to_track_quat('-Z', 'Y'); ex, ey = q @ Vector((1, 0, 0)), q @ Vector((0, 1, 0)); pw = g.get('power', 1.0)
        def emit(name, off, sx, sy, power, disk=False):
            l = bpy.data.lights.new(name, 'AREA'); l.shape = 'DISK' if disk else 'RECTANGLE'; l.size = sx; l.energy = power * pw; l.color = kelvin_rgb(g.get('kelvin', 6000))
            if not disk: l.size_y = sy
            o = link(bpy.data.objects.new(name, l)); o.location = c0 + ex * off[0] + ey * off[1]; o.rotation_euler = q.to_euler(); L.append(dict(name=name, glare=g['kind'], loc=list(o.location), power=l.energy))
        k = g['kind']
        if k == 'window':
            for ix in (-1, 1):
                for iy in (-1, 1): emit(f'glare{gi}-pane{ix}{iy}', (ix * 27, iy * 36), 46, 62, 1.2e5)
        elif k == 'panel': emit(f'glare{gi}-panel', (0, 0), *g.get('size', (60, 60)), 2.5e5)
        elif k == 'tube': emit(f'glare{gi}-tube', (0, 0), 120, 3.5, 3.0e4)
        elif k == 'bulb': emit(f'glare{gi}-bulb', (0, 0), 6, 0, 2.5e4, disk=True)
        elif k == 'leds':
            nled = g.get('n', 8)
            for i in range(nled): emit(f'glare{gi}-led{i}', ((i - (nled - 1) / 2) * g.get('pitch', 4.0), 0), g.get('size', 2.2), 0, 350, disk=True)
    hsl = spec.get('hard_shadow') or []
    for hi, hs in enumerate(hsl if isinstance(hsl, list) else [hsl]):   # small hard lamps; a forearm-sized occluder puts a shadow edge across the chosen floor point
        at = spec.get('_pile', (0, 0)) if hs.get('at', 'pile') == 'pile' else hs['at']
        P = Vector((at[0], at[1], 0)); az, el = math.radians(hs['az']), math.radians(hs['el']); Ld = Vector((math.cos(az) * math.cos(el), math.sin(az) * math.cos(el), math.sin(el)))
        l = bpy.data.lights.new(f'hard{hi}', 'SPOT'); l.energy = hs.get('power', 2.5e5); l.spot_size = math.radians(50); l.shadow_soft_size = hs.get('soft', 0.6); l.color = kelvin_rgb(hs.get('kelvin', 3600))
        o = link(bpy.data.objects.new(f'hard{hi}', l)); o.location = P + Ld * 140; o.rotation_euler = (P - o.location).to_track_quat('-Z', 'Y').to_euler(); L.append(dict(name=f'hard{hi}', loc=list(o.location), power=l.energy))
        if not hs.get('occluder', True): continue
        occ = box(f'arm-occluder-hard{hi}', hs.get('width', 9), 60, 4); occ.location = P + Ld * 30 + Vector((-math.sin(az), math.cos(az), 0)) * hs.get('offset', 4.5); occ.rotation_euler = (0, 0, az - math.pi / 2)
        occ.visible_camera = False; occ.visible_glossy = False; occ.data.materials.append(plain_mat('occ-hard', (0.05, 0.05, 0.05), 0.8))
    info['lights'] = dict(kind=kind, hdri=hdr, world_strength=world_strength, lamps=L, body_shadow=spec.get('body_shadow', True))


def setup_camera(scene, rng, spec, info):
    yaw = spec['tray_yaw'] if spec['tray_yaw'] is not None else rng.uniform(-180, 180)
    yaw = math.radians(yaw + rng.gauss(0, 5)); up = Vector((-math.sin(yaw), math.cos(yaw), 0))
    target = Vector((rng.gauss(0, 0.8), rng.uniform(-0.5, 3.0), 0)); tilt = math.radians(spec['tilt_deg']); side_tilt = math.radians(rng.gauss(0, 3))
    right = up.cross(Vector((0, 0, 1))); d = spec['distance_cm']
    dirv = (Vector((0, 0, 1)) * math.cos(tilt) - up * math.sin(tilt) + right * math.sin(side_tilt)).normalized()
    cam = bpy.data.cameras.new('phone'); cam.lens = LENS_MM; cam.sensor_fit = 'AUTO'; cam.sensor_width = SENSOR_MM; cam.clip_start = 1; cam.clip_end = 2000
    co = link(bpy.data.objects.new('phone', cam)); scene.camera = co
    roll0 = math.radians(rng.gauss(0, 2))
    def place(dist):
        co.location = target + dirv * dist
        zc = (co.location - target).normalized(); yc = (up - zc * up.dot(zc)).normalized(); xc = yc.cross(zc)
        m = Matrix((xc, yc, zc)).transposed(); co.rotation_mode = 'QUATERNION'
        co.rotation_quaternion = Quaternion(zc, roll0) @ m.to_quaternion(); bpy.context.view_layer.update()
    place(d)
    cam.dof.use_dof = True; focus = (target - co.location).length + rng.gauss(0, 0.4); cam.dof.focus_distance = focus
    cam.dof.aperture_fstop = FSTOP * scene.unit_settings.scale_length   # aperture in scene units: f/N with lens mm -> cm scene
    info['camera'] = dict(yaw_deg=math.degrees(yaw), tilt_deg=spec['tilt_deg'], side_tilt_deg=math.degrees(side_tilt), distance_cm=d, target=list(target), lens_mm=LENS_MM, sensor_mm=SENSOR_MM, fstop=FSTOP, focus_cm=focus)
    return co, cam, target, up, place


def project(scene, cam, p):
    v = world_to_camera_view(scene, cam, p); return (v.x, 1 - v.y, v.z)


COUNT_MIN, HIDDEN_MAX = 0.25, 0.06


def convex_mask(poly, W, H):
    """Boolean mask of a convex polygon given in normalised (u, v) image coordinates."""
    P = np.array(poly, np.float64) * [W, H]; yy, xx = np.mgrid[0:H, 0:W] + 0.5; m = np.ones((H, W), bool)
    Q = np.roll(P, -1, 0); sgn = 1.0 if (P[:, 0] * Q[:, 1] - Q[:, 0] * P[:, 1]).sum() >= 0 else -1.0   # polygon orientation
    for (x0, y0), (x1, y1) in zip(P, Q):
        m &= ((x1 - x0) * (yy - y0) - (y1 - y0) * (xx - x0)) * sgn >= 0
    return m


def hull_area(pts):
    P = sorted(map(tuple, pts))
    def half(seq):
        h = []
        for p in seq:
            while len(h) >= 2 and (h[-1][0] - h[-2][0]) * (p[1] - h[-2][1]) - (h[-1][1] - h[-2][1]) * (p[0] - h[-2][0]) <= 0: h.pop()
            h.append(p)
        return h
    H = half(P)[:-1] + half(P[::-1])[:-1]
    return 0.5 * abs(sum(H[i][0] * H[(i + 1) % len(H)][1] - H[(i + 1) % len(H)][0] * H[i][1] for i in range(len(H))))


def id_checks(path, rows, info, scene, co):
    """Reject scenes a person could not label unambiguously, or where bench/paper shows through the tray."""
    W, H = RES; img = bpy.data.images.load(str(path)); buf = np.empty(W * H * 4, np.float32); img.pixels.foreach_get(buf); bpy.data.images.remove(img)
    rgb = np.rint(buf.reshape(H, W, 4)[::-1, :, :3] * 255).astype(np.int32); ids = rgb[..., 0] + 256 * rgb[..., 1]; surf = rgb[..., 2]
    trough = [list(project(scene, co, Vector((x, y, -0.6)))[:2]) for x, y in ((-HX + 0.4, TYC - TR + 0.35), (HX - 0.4, TYC - TR + 0.35), (HX - 0.4, TYC + TR - 0.35), (-HX + 0.4, TYC + TR - 0.35))]
    through = int(((surf == 128) & (convex_mask(info['tray_polygon_uv'], W, H) | convex_mask(trough, W, H))).sum())
    info['surface_through_tray_px'] = through
    if through > 25 and info['spec']['tray_colour'] != 'clear' and not info['spec'].get('no_tray'): raise RuntimeError(f'bench/paper visible through the tray ({through} px)')
    amb = []
    for r in rows:
        vis = int((ids == r['id']).sum()); area = hull_area(np.array(r['silhouette_uv']) * [W, H]); r['visible_fraction'] = min(1.0, vis / max(area, 1.0))
        if r['status'] == 'main' and HIDDEN_MAX <= r['visible_fraction'] < COUNT_MIN: amb.append((r['id'], round(r['visible_fraction'], 3)))
    if amb: raise RuntimeError(f'ambiguous visibility {amb}')


def render_scene(spec, out, args):
    t0 = time.time(); rng = random.Random(spec['seed']); prod = spec['product']
    bpy.ops.wm.read_factory_settings(use_empty=True); scene = bpy.context.scene
    scene.unit_settings.system = 'METRIC'; scene.unit_settings.scale_length = 0.01
    scene.render.resolution_x, scene.render.resolution_y = RES; scene.render.resolution_percentage = 100   # before any projection
    with bpy.data.libraries.load(str(HERE / 'tray-v2.blend'), link=False) as (src, dst): dst.objects = ['tray_visual', 'tray_collider', 'lid']
    tray, collider, lid = dst.objects
    for o in dst.objects: link(o)
    collider.hide_render = True; collider.display_type = 'WIRE'
    info = dict(spec=spec, tray_geometry_sha256=TRAY['geometry_sha256'], tray_version=G['version'])
    tm, info['tray_material'] = tray_material(rng, spec['tray_colour'], spec.get('tray_rgb'), spec.get('tray_gloss'), spec.get('tray_logo')); tray.data.materials.append(tm)
    lid.data.materials.append(lid_material(rng))
    hinge_y, hinge_z = lid['hinge_y'], lid['hinge_z']
    pose = spec['lid']; lid_angle = {'closed': 0.0, 'standing': rng.uniform(95, 120)}.get(pose)
    if pose == 'open-flat':   # rotate until the free edge rests on the bench
        vs = [v.co.copy() for v in lid.data.vertices]
        for ang in np.arange(170, 260, 0.5):
            R = Matrix.Rotation(math.radians(-ang), 3, 'X')
            if min((R @ v).z for v in vs) + hinge_z <= TABLE_Z + 0.03: lid_angle = float(ang); break
        lid_angle = lid_angle or 200.0
    lid.rotation_euler = (math.radians(-lid_angle), 0, 0); info['lid'] = dict(pose=pose, angle_deg=lid_angle)
    if spec.get('no_tray'):
        for o in (tray, collider, lid): o.location.z -= 60
    tb = box('bench', 400, 400, 2); tb.location = (0, 0, TABLE_Z - 1); tmat, info['bench'] = table_material(rng, spec.get('bench')); tb.data.materials.append(tmat)
    # capsules
    kind = prod.get('kind', 'capsule')
    if kind == 'capsule': mesh, geo = capsule_mesh(prod); mats, info['shell_material'] = capsule_materials(rng, prod)
    else: mesh, geo = pill_mesh(prod); mats, info['shell_material'] = pill_materials(rng, prod)
    sim_mesh = mesh if kind == 'capsule' else pill_mesh(prod, proxy=True)[0]
    for m in mats: mesh.materials.append(m)
    poses = spawn_poses(rng, spec, geo); caps = []
    for i, p in enumerate(poses):
        ob = link(bpy.data.objects.new(f'capsule-{i + 1:03d}', sim_mesh)); ob.rotation_mode = 'QUATERNION'
        ob.rotation_quaternion = Quaternion((0, 0, 1), p['yaw']) @ Quaternion((0, 1, 0), math.pi / 2 + p['pitch']) @ Quaternion((0, 0, 1), p['roll'])
        ob.location = (p['x'], p['y'], p['z']); ob.pass_index = i + 1; caps.append(ob)
    bpy.context.view_layer.update()
    info['physics'] = simulate(scene, caps, collider, tb, rng, heavy=spec['layout'] in ('heap', 'dense', 'raft'), lid=lid, shape='CAPSULE' if kind == 'capsule' else 'CONVEX_HULL')
    for ob in caps: ob.data = mesh
    bpy.context.view_layer.update()
    status = [classify(ob, geo) for ob in caps]
    if spec.get('no_tray'): status = ['table'] * len(caps)
    bad = [s for s in status if s.startswith('ambiguous')]
    info['capsule_geometry_cm'] = {k: v for k, v in geo.items() if k != 'sample'}; info['status_counts'] = {s: status.count(s) for s in set(status)}
    if bad: raise RuntimeError(f'ambiguous placement {info["status_counts"]}')
    mains = [ob.matrix_world.translation for ob, st in zip(caps, status) if st == 'main']
    spec['_pile'] = (sum(t.x for t in mains) / len(mains), sum(t.y for t in mains) / len(mains)) if mains else (0.0, 0.0)
    build_foreign(rng, spec, [ob for ob, st in zip(caps, status) if st == 'main'], geo, hex_lin(prod['body_hex']), info)
    build_clutter(rng, spec, info, avoid=[(ob.matrix_world.translation.x, ob.matrix_world.translation.y, 1.4) for ob, st in zip(caps, status) if st == 'table'])
    co, cam, target, up, place = setup_camera(scene, rng, spec, info)
    lights(scene, rng, spec, target, up, co.location.copy(), info)
    # framing: every main capsule and the whole floor must be inside the frame, move back if needed
    floor = [Vector((x, y, 0)) for x, y in floor_polygon()]
    for _ in range(24):
        pts = [project(scene, co, ob.matrix_world.translation) for ob, s in zip(caps, status) if s == 'main'] + [project(scene, co, p) for p in floor]
        if all(0.015 < u < 0.985 and 0.015 < v < 0.985 for u, v, _ in pts): break
        info['camera']['distance_cm'] += 1.0; place(info['camera']['distance_cm']); cam.dof.focus_distance = (target - co.location).length
    else: raise RuntimeError('framing failed')
    # metadata: poses, projected centres, silhouette samples
    rows = []
    ring = [(math.cos(a), math.sin(a)) for a in np.linspace(0, 2 * math.pi, 20, endpoint=False)]
    for ob, st in zip(caps, status):
        M = ob.matrix_world; half = geo['length'] / 2 - geo['r_cap']; sil = []
        for q in geo.get('sample', ()): u, v, _ = project(scene, co, M @ Vector(q)); sil.append([round(u, 5), round(v, 5)])
        for zz in (() if 'sample' in geo else np.linspace(-geo['length'] / 2, geo['length'] / 2, 9)):
            rr = geo['r_cap'] * math.sqrt(max(0.0, 1 - max(0, abs(zz) - half) ** 2 / geo['r_cap'] ** 2)) if abs(zz) > half else geo['r_cap']
            for cx, cy in ring: u, v, _ = project(scene, co, M @ Vector((rr * cx, rr * cy, zz))); sil.append([round(u, 5), round(v, 5)])
        u, v, dep = project(scene, co, M.translation)
        rows.append(dict(id=ob.pass_index, status=st, center_world=[round(x, 4) for x in M.translation], axis_world=[round(x, 4) for x in (M.to_3x3() @ Vector((0, 0, 1))).normalized()],
                         center_uv=[u, v], depth_cm=dep, silhouette_uv=sil))
    info['tray_polygon_uv'] = [list(project(scene, co, p)[:2]) for p in floor]
    scene.render.resolution_x, scene.render.resolution_y = RES; scene.render.resolution_percentage = 100; scene.render.film_transparent = False
    # 1) exact instance ids first (cheap, no AA, no DOF, lid and shadow casters hidden) -> reject before the Cycles render
    hidden = [o for o in scene.objects if o.name.startswith(('lid', 'phone-occluder', 'arm-occluder', 'body-occluder', 'tray_collider')) or (o.name == 'tray_visual' and spec['tray_colour'] == 'clear')]
    was_hidden = {o.name: o.hide_render for o in hidden}
    for o in hidden: o.hide_render = True
    for o in scene.objects:
        if o.type == 'MESH': o.color = ((o.pass_index % 256) / 255, (o.pass_index // 256) / 255, 0, 1) if o in caps else (0, 0, 1.0, 1) if o.name == 'tray_visual' else (0, 0, 64 / 255, 1) if o.name.startswith('foreign') else (0, 0, 128 / 255, 1)
    cam.dof.use_dof = False; scene.render.engine = 'BLENDER_WORKBENCH'; sh = scene.display.shading; sh.light = 'FLAT'; sh.color_type = 'OBJECT'
    sh.show_object_outline = False; sh.show_cavity = False; sh.show_shadows = False; sh.show_specular_highlight = False; sh.show_xray = False; sh.background_type = 'VIEWPORT'; sh.background_color = (0, 0, 0)
    scene.display.render_aa = 'OFF'; scene.render.dither_intensity = 0; scene.view_settings.view_transform = 'Raw'
    s = scene.render.image_settings; s.file_format = 'PNG'; s.color_mode = 'RGB'; s.color_depth = '8'; scene.render.filepath = str(out / f"{spec['id']}-ids.png"); bpy.ops.render.render(write_still=True)
    id_checks(out / f"{spec['id']}-ids.png", rows, info, scene, co)
    for o in hidden: o.hide_render = was_hidden[o.name]
    # 2) photographic render
    cam.dof.use_dof = True; scene.render.dither_intensity = 1.0
    scene.render.engine = 'CYCLES'; prefs = bpy.context.preferences.addons['cycles'].preferences
    prefs.compute_device_type = 'OPTIX'; prefs.refresh_devices()
    for dv in prefs.devices: dv.use = dv.type == 'OPTIX'
    scene.cycles.device = 'GPU'; scene.cycles.samples = args.samples; scene.cycles.use_adaptive_sampling = True; scene.cycles.adaptive_threshold = 0.008
    scene.cycles.use_denoising = True; scene.cycles.denoiser = 'OPENIMAGEDENOISE'; scene.cycles.denoising_use_gpu = True; scene.cycles.max_bounces = 10; scene.cycles.transmission_bounces = 10
    scene.cycles.blur_glossy = 0.5; scene.cycles.caustics_reflective = False; scene.cycles.caustics_refractive = False
    scene.view_settings.view_transform = 'Standard'; scene.view_settings.look = 'None'; scene.view_settings.exposure = 0
    s.file_format = 'OPEN_EXR'; s.color_mode = 'RGB'; s.color_depth = '16'; s.exr_codec = 'ZIP'
    scene.render.filepath = str(out / f"{spec['id']}.exr"); bpy.ops.render.render(write_still=True); t_render = time.time()
    info['capsules'] = rows
    info['resolution'] = RES; info['blender'] = bpy.app.version_string; info['samples'] = args.samples
    info['timing_s'] = dict(total=time.time() - t0, render=t_render - t0)
    (out / f"{spec['id']}.json").write_text(json.dumps(info, indent=1, default=lambda x: list(x) if hasattr(x, '__iter__') else str(x)))
    log('SCENE_DONE', spec['id'], len(caps), info['status_counts'], round(time.time() - t0, 1), 's')


def main():
    a = argparse.ArgumentParser(); a.add_argument('--plan', required=True); a.add_argument('--out', required=True); a.add_argument('--ids', default='')
    a.add_argument('--worker', type=int, default=0); a.add_argument('--workers', type=int, default=1); a.add_argument('--samples', type=int, default=256); a.add_argument('--retries', type=int, default=4)
    args = a.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    scenes = json.loads(Path(args.plan).read_text())['scenes']
    if args.ids: scenes = [s for s in scenes if s['id'] in args.ids.split(',')]
    scenes = scenes[args.worker::args.workers]; fails = []
    for spec in scenes:
        if (out / 'STOP').exists(): break
        if (out / f"{spec['id']}.json").exists(): continue
        for attempt in range(args.retries):
            try:
                sp = dict(spec, seed=spec['seed'] + 7919 * attempt, attempt=attempt); render_scene(sp, out, args); break
            except Exception as e:
                log('SCENE_RETRY', spec['id'], attempt, repr(e)); traceback.print_exc()
        else: fails.append(spec['id'])
    (out / f'worker-{args.worker}.json').write_text(json.dumps(dict(failed=fails, assigned=[s['id'] for s in scenes], finished=time.time())))
    log('WORKER_DONE', args.worker, 'failed', fails)


if __name__ == '__main__':
    main()
