"""Eir's speed test (android/app/src/androidTest/.../PerfTest.kt) on Firebase Test Lab phones or a phone on adb.

  python3 scripts/testlab.py devices                         physical phones Test Lab offers (ids for --device)
  python3 scripts/testlab.py run --device oriole [--device MODEL[:API] ...] [--only demo] [--no-build]
  python3 scripts/testlab.py parity --device r0q:36 [...]   NPU vs GPU photo counts on verified photos (EngineParityTest)
  python3 scripts/testlab.py phoneuse --device r0q:36       is the rest of the phone laggy during/after Eir (PhoneUseTest)
  python3 scripts/testlab.py robo --device r0q:36           the shipped release APK, crawled by Test Lab (engines, crashes)
  python3 scripts/testlab.py probe [--dry-run]              the review's H4 check before promoting a build beyond internal testing:
                                                            robo (walks the terms) + demo/cached_load speed + NPU parity on a Snapdragon
                                                            phone (S23, S24) and a Pixel 6 (GPU), then a PASS/FAIL verdict
  python3 scripts/testlab.py verdict reports/testlab/<run>  the probe's verdict again, from downloaded results
  python3 scripts/testlab.py summary reports/testlab/<run>   the table again, from downloaded results
  python3 scripts/testlab.py local [--serial IP:PORT] [--only camera_auto]   same test on a phone on adb (keeps its data)

App ids (v0.18.0): the debug build (speed tests, `local`) is ca.asgardpharma.eircount.debug and the store build (Robo) is
ca.asgardpharma.eircount. Results of older runs, and phones still running the old test build com.asgard.pillcounter.test,
are read with --legacy (or --package ID); see scripts/eir_android_id.py. Robo walks through the terms screen with a generated
script (it scrolls to the end, ticks the box and agrees); the subscription screen then shows Play unavailable on Test Lab phones.

Test Lab needs the Google Cloud CLI signed in (gcloud auth login) and the Firebase project set
(gcloud config set project ID). The project is on the Blaze plan (since 2026-10-12): 30 phone-minutes a day free, then
$5 per phone-hour, billed by the minute. --timeout caps each phone, so the worst case is phones x timeout; a run whose
worst case would take this month's charges past --cap-cad (default CAD 5, the user's limit) is refused, and each run's
minutes go into reports/testlab/spend.json. Google's budgets only send emails; they never stop Test Lab. The whole
PerfTest takes ~7 minutes a phone; --only camera_auto --only demo --only cached_load ~4. Results (logcat, screen video,
perf/*.json with a 5-second timeline) are downloaded to reports/testlab/<run>/.
"""
import argparse, json, math, os, re, shutil, subprocess, time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
ANDROID = ROOT / 'android'
APP = ANDROID / 'app/build/outputs/apk/debug/app-debug.apk'
RELEASE = ANDROID / 'app/build/outputs/apk/release/app-release.apk'  # the shrunk APK friends get (v0.17.5+)
TEST = ANDROID / 'app/build/outputs/apk/androidTest/debug/app-debug-androidTest.apk'
import eir_android_id
PKG = eir_android_id.DEBUG          # the debug build: speed tests and `local` run against it
PKG_RELEASE = eir_android_id.STORE  # the store build the Robo crawler opens
LEGACY = eir_android_id.LEGACY      # the old test id: v0.17.9 and earlier runs and phones
BUILD = ANDROID / 'app/build'
SCENARIOS = {'camera_auto': 't1_cameraAuto', 'demo': 't2_demo', 'demo_full': 't3_demoFull',
             'camera_full': 't4_cameraFull', 'cached_load': 't5_cachedLoad', 'prewarm': 't6_prewarm'}
ENV = dict(os.environ, JAVA_HOME='/opt/homebrew/opt/openjdk@17', ANDROID_HOME='/opt/homebrew/share/android-commandlinetools')
ENV.setdefault('CLOUDSDK_PYTHON', '/opt/homebrew/opt/python@3.14/bin/python3.14')  # gcloud refuses macOS's Python 3.9
ENV['CLOUDSDK_STORAGE_SLICED_OBJECT_DOWNLOAD_THRESHOLD'] = '0'  # sliced downloads of the videos failed their hash check
ADB = ENV['ANDROID_HOME'] + '/platform-tools/adb'
GCLOUD = shutil.which('gcloud') or '/opt/homebrew/bin/gcloud'
# Blaze: Google's CAD price list (Cloud Billing catalog, Oct 2026). The free minutes are counted per project per day.
PRICE_CAD_MIN = 7.08325 / 60  # CAD per physical-phone minute after the free ones, each phone rounded up to the minute
FREE_MIN_DAY = 30
LEDGER = ROOT / 'reports/testlab/spend.json'


def build():
    subprocess.run([str(ANDROID / 'gradlew'), '-q', '-p', str(ANDROID), 'assembleDebug', 'assembleRelease', 'assembleDebugAndroidTest'], env=ENV, check=True)


def version():
    return re.search(r'versionName = "([^"]+)"', (ANDROID / 'app/build.gradle.kts').read_text()).group(1)


def methods(only):
    return [f'com.asgard.pillcounter.PerfTest#{SCENARIOS[s]}' for s in only or []]


def package_for(a):
    """The debug build's id for speed tests and `local`: ca.asgardpharma.eircount.debug, or --package ID / --legacy."""
    return eir_android_id.choose(getattr(a, 'package', None), getattr(a, 'legacy', False), installed=None, prefer=[PKG])


def project(a):
    return [f'--project={a.project}'] if a.project else []


def devices(a):
    subprocess.run([GCLOUD, 'firebase', 'test', 'android', 'models', 'list', '--filter=form=PHYSICAL'] + project(a), env=ENV)


def minutes(timeout):
    n = float(timeout[:-1]); return math.ceil({'s': n / 60, 'm': n, 'h': n * 60}[timeout[-1]])


def pacific_day():
    return datetime.now(ZoneInfo('America/Los_Angeles')).strftime('%Y-%m-%d')


def month_cost(entries):
    days = {}
    for e in entries: days[e['day']] = days.get(e['day'], 0) + e['minutes']
    return sum(max(0, m - FREE_MIN_DAY) for m in days.values()) * PRICE_CAD_MIN


def check_budget(a, timeout):
    """Refuses a run whose worst case (every phone running to its timeout) would take this month past --cap-cad."""
    ledger = json.loads(LEDGER.read_text()) if LEDGER.exists() else []
    month = [e for e in ledger if e['day'][:7] == pacific_day()[:7]]
    worst = len(a.device) * minutes(timeout)
    now, then = month_cost(month), month_cost(month + [{'day': pacific_day(), 'minutes': worst}])
    print(f'Test Lab this month: CAD {now:.2f}. This run: at most {worst} phone-minutes, CAD {then - now:.2f}. Limit CAD {a.cap_cad:.2f}.', flush=True)
    if then > a.cap_cad + 1e-9:
        raise SystemExit(f'Not submitted: the worst case would reach CAD {then:.2f}. Use fewer phones, a shorter --timeout, or wait for '
                         f'tomorrow\'s {FREE_MIN_DAY} free minutes (midnight Pacific).')
    return ledger


def record(ledger, name, folder, a, timeout):
    """Test minutes per phone, rounded up as billed, from the results (the timeout for a phone without results)."""
    secs = {}
    for f in Path(folder).rglob('test_result_*.xml'):
        t = re.search(r"<testsuite [^>]*time=['\"]([\d.]+)", f.read_text(errors='replace'))
        if t: secs[f.parent.name] = secs.get(f.parent.name, 0) + float(t.group(1))
    used = sum(math.ceil(v / 60) for v in secs.values()) + max(0, len(a.device) - len(secs)) * minutes(timeout)
    ledger.append({'day': pacific_day(), 'run': name, 'phones': len(a.device), 'minutes': used})
    LEDGER.parent.mkdir(parents=True, exist_ok=True); LEDGER.write_text(json.dumps(ledger, indent=1))
    month = [e for e in ledger if e['day'][:7] == pacific_day()[:7]]
    print(f'Test Lab: {used} phone-minutes; this month about CAD {month_cost(month):.2f} of {a.cap_cad:.2f}.', flush=True)


def robo_script_file(a):
    path = BUILD / 'tmp' / 'robo-terms.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(robo_terms_script(eir_android_id.choose(a.package, a.legacy, installed=None, prefer=[PKG_RELEASE])))
    return path


def submit(a, kind, targets, timeout, robo=False):
    """Builds (unless --no-build), runs the targets on every --device, downloads the results; returns (folder, gcloud code).
    robo: no test code; Test Lab's crawler opens the shipped release APK and taps through it."""
    ledger = check_budget(a, timeout)
    if not a.no_build: build()
    name = f'eir-{kind}-{version()}-{time.strftime("%Y%m%d-%H%M%S")}'
    cmd = [GCLOUD, 'firebase', 'test', 'android', 'run', f'--timeout={timeout}', f'--results-dir={name}'] + project(a) + (
        ['--type=robo', f'--app={RELEASE}', f'--robo-script={robo_script_file(a)}'] if robo else
        ['--type=instrumentation', f'--app={APP}', f'--test={TEST}', f'--directories-to-pull=/sdcard/Android/data/{package_for(a)}/files/perf',
         '--num-flaky-test-attempts=0', '--test-targets=' + ','.join('class ' + t for t in targets)])
    for d in a.device:
        model, _, api = d.partition(':')
        cmd.append(f'--device=model={model}' + (f',version={api}' if api else '') + ',locale=en,orientation=portrait')
    print('$ ' + ' '.join(cmd), flush=True)
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=ENV)
    lines = []
    for line in p.stdout: print(line, end='', flush=True); lines.append(line)
    p.wait()
    bucket = re.search(r'storage/browser/([^/\s\]]+)/', ''.join(lines))
    if not bucket: raise SystemExit('No results bucket in the gcloud output, so nothing was downloaded.')
    dest = ROOT / 'reports/testlab'; dest.mkdir(parents=True, exist_ok=True)
    # Everything but our own APKs; a failed video download must not lose the summary.
    subprocess.run([GCLOUD, 'storage', 'rsync', '--recursive', r'--exclude=.*\.apk$', f'gs://{bucket.group(1)}/{name}', str(dest / name)] + project(a), env=ENV)
    record(ledger, name, dest / name, a, timeout)
    return dest / name, p.returncode  # gcloud: 0 passed, 10 a test failed, 15/18/19 environment problems


def run(a):
    folder, code = submit(a, 'perf', methods(a.only) or ['com.asgard.pillcounter.PerfTest'], a.timeout or '30m')
    summary(folder)
    raise SystemExit(code)


def parity(a):
    folder, code = submit(a, 'parity', ['com.asgard.pillcounter.EngineParityTest'], a.timeout or '15m')
    parity_summary(folder)
    raise SystemExit(code)


def raise_exit(code):
    raise SystemExit(code)


def robo_run(a):
    global ROBO_IDS
    ROBO_IDS = [eir_android_id.choose(a.package, a.legacy, installed=None, prefer=[PKG_RELEASE])]
    folder, code = submit(a, 'robo', [], a.timeout or '5m', robo=True)
    robo_summary(folder)
    raise SystemExit(code)


ROBO_IDS = eir_android_id.ALL  # which app ids count as "the app" when reading Robo logs (older runs: any id)


def robo_terms_script(package, swipes=140):
    """Robo script for the terms screen (a fresh install of the store build): English, scroll to the end, tick the box, agree.
    Robo cannot be told to skip the terms, and the store build has no way around them; swiping the terms' scroll view up
    `swipes` times reaches the end of the 46,000-character text. Returns the script as text."""
    rid = lambda name: [{'resourceId': f'{package}:id/{name}'}]
    actions = [{'eventType': 'WAIT', 'delayTime': 2500}, {'eventType': 'VIEW_CLICKED', 'elementDescriptors': rid('btnReadEn')}, {'eventType': 'WAIT', 'delayTime': 1000}]
    actions += [{'eventType': 'VIEW_SWIPED', 'swipeDirection': 'Up', 'elementDescriptors': rid('termsScroll')} for _ in range(swipes)]
    actions += [{'eventType': 'VIEW_CLICKED', 'elementDescriptors': rid('cbVerify')}, {'eventType': 'VIEW_CLICKED', 'elementDescriptors': rid('btnAgree')}, {'eventType': 'WAIT', 'delayTime': 2000}]
    return json.dumps([{'id': 1000, 'description': 'accept the terms', 'maxNumberOfRuns': 1, 'contextDescriptor': {'condition': 'app_under_test_shown'}, 'actions': actions}], indent=1)


def robo_summary(folder):
    """What the app logged while the crawler used it: version, engines (AI chip or graphics chip), tier, errors, crashes."""
    files = sorted(Path(folder).rglob('logcat'))
    if not files: print('No Robo results found.'); return
    for f in files:
        text = f.read_text(errors='replace'); ev = []
        for m in re.finditer(r'PillCounter\s*:\s*(\{.*\})\s*$', text, re.M):
            try: ev.append(json.loads(m.group(1)))
            except ValueError: pass
        last = lambda t, **kv: next((e for e in reversed(ev) if e.get('type') == t and all(e.get(k) == v for k, v in kv.items())), None)
        start, tier, live, photo = last('app_start'), last('device_tier'), last('session', screen='capture'), last('session', screen='verify')
        npu = [e for e in ev if e.get('type') == 'accel_attempt' and 'qnn' in str(e.get('config'))]
        crashes = len(re.findall(r'FATAL EXCEPTION.*\n.*Process: (?:' + '|'.join(re.escape(p) for p in ROBO_IDS) + ')', text))
        print(f"\n{f.parent.name}: Eir Count {start.get('version') if start else '?'} on {start.get('device') if start else '?'} ({start.get('soc') if start else '?'})")
        print(f"  live engine: {live.get('provider') if live else 'never loaded'}{'  (tier ' + tier.get('tier') + ', ' + str(tier.get('warm_ms')) + ' ms)' if tier else ''}")
        print(f"  photo engine: {photo.get('provider') if photo else 'never loaded'}")
        if npu: print('  AI chip: ' + ('accepted' if any(e.get('accepted') for e in npu) else 'failed: ' + str(npu[-1].get('error'))[:120]))
        print(f"  app errors logged: {sum(1 for e in ev if e.get('type') == 'error')}; crashes: {crashes}")


# ---- H4 of the review of 0.18.0: LiteRT 1.0.1 + QNN 2.34 + CameraX 1.4.2 on the paths that matter ------------------------------------
# A native crash inside Interpreter(...) on a Snapdragon phone cannot be caught by the app's try/catch, and a quiet fallback from the NPU
# to the GPU is a speed regression. The probe runs the three things that show either on real phones; it needs Test Lab (gcloud login)
# or `local` on the owner's phone, so it has NOT been run for versionCode 47 (the login had expired). docs: the release README's open blockers.
PROBE_SNAPDRAGON = ['dm1q:35', 'e1q:36']  # Galaxy S23, S24: the QNN NPU path (the S22, r0q:36, can be added)
PROBE_GPU = ['oriole']                     # Pixel 6: the GPU path and the "light" tier
REFERENCE_PERF = ROOT / 'reports/testlab/eir-perf-0.17.5-small-20261012-183106'      # the last full speed run (TFLite 2.16, as v0.17.9)
REFERENCE_PARITY = ROOT / 'reports/testlab/eir-parity-0.17.3-idle-20261001-155447'   # NPU vs GPU on 109 verified photos (S22, S23, S24)
SLOWER_FACTOR = 1.5    # a median or a load time more than this many times the reference is a regression
READY_S_LIMIT = 3.0    # saved-copy load time for a phone that has no reference run
PARITY_EXACT_SLACK = 2 # the NPU may get this many fewer photos exactly right than in the reference


def probe_plan(a):
    """What the probe runs: (kind, test targets, phones, timeout)."""
    snap, gpu = a.snapdragon or PROBE_SNAPDRAGON, a.gpu or PROBE_GPU
    return [('robo', [], snap + gpu, '5m'),
            ('perf', ['com.asgard.pillcounter.PerfTest#' + SCENARIOS['demo'], 'com.asgard.pillcounter.PerfTest#' + SCENARIOS['cached_load']], snap + gpu, '12m'),
            ('parity', ['com.asgard.pillcounter.EngineParityTest'], snap, '15m')]


def probe(a):
    plan = probe_plan(a)
    worst = sum(len(devs) * minutes(t) for _, _, devs, t in plan)
    print('Probe plan (Firebase Test Lab):')
    for kind, targets, devs, t in plan: print(f'  {kind:7s} on {", ".join(devs)} (at most {t} each)')
    cost = max(0, worst - FREE_MIN_DAY) * PRICE_CAD_MIN
    print(f'Worst case {worst} phone-minutes = CAD {cost:.2f} (the first {FREE_MIN_DAY} minutes of a day are free; cap CAD {a.cap_cad:.2f}).'
          + ('  Over the cap: use fewer phones (--snapdragon e1q:36 --gpu oriole) or run it on two days.' if cost > a.cap_cad else ''))
    if a.dry_run: print('Dry run: nothing was built or submitted.'); return
    folders = []
    for kind, targets, devs, t in plan:
        step = argparse.Namespace(**{**vars(a), 'device': devs})
        global ROBO_IDS
        if kind == 'robo': ROBO_IDS = [eir_android_id.choose(a.package, a.legacy, installed=None, prefer=[PKG_RELEASE])]
        folder, code = submit(step, kind, targets, t, robo=(kind == 'robo'))
        folders.append(folder); a.no_build = True  # built once
    bad = False
    for f in folders: bad |= not verdict(f)
    raise SystemExit(1 if bad else 0)


def _soc_is_snapdragon(soc):
    return bool(re.search(r'qualcomm|snapdragon|\bSM\d{4}|\bSM-?8', soc or '', re.I)) and 'tensor' not in (soc or '').lower()


def verdict(folder, reference_perf=None, reference_parity=None):
    """PASS/FAIL lines for a downloaded probe run (robo, perf or parity results in `folder`). Returns True when nothing failed.
    Checks: no crash; the demo counts 50 then 20; the saved-copy load is no slower than SLOWER_FACTOR x the reference run on that phone;
    on Snapdragon phones the QNN NPU engine was accepted; NPU vs GPU photo counts and speed stay within the reference run's."""
    folder = Path(folder); reference_perf = Path(reference_perf or REFERENCE_PERF); reference_parity = Path(reference_parity or REFERENCE_PARITY)
    ok = True
    def say(good, text):
        nonlocal ok
        ok &= good; print(('PASS  ' if good else 'FAIL  ') + text)
    for phone in sorted({f.parent for f in folder.rglob('logcat*')}):
        text = '\n'.join(f.read_text(errors='replace') for f in sorted(phone.glob('logcat*')))
        crashed = re.findall(r'FATAL EXCEPTION.*\n.*Process: (ca\.asgardpharma\.eircount[\w.]*)', text)
        sigsegv = re.findall(r'Fatal signal \d+ \(SIG\w+\).*?(?:ca\.asgardpharma\.eircount|PillCounter)', text)
        say(not crashed and not sigsegv, f'{phone.name}: no crash ({len(crashed)} Java, {len(sigsegv)} native)')
        ev = []
        for m in re.finditer(r'PillCounter\s*:\s*(\{.*\})\s*$', text, re.M):
            try: ev.append(json.loads(m.group(1)))
            except ValueError: pass
        start = next((e for e in ev if e.get('type') == 'app_start'), {})
        npu = [e for e in ev if e.get('type') == 'accel_attempt' and 'qnn' in str(e.get('config')).lower()]
        if _soc_is_snapdragon(start.get('soc')):
            accepted = any(e.get('accepted') for e in npu)
            say(accepted, f"{phone.name}: Qualcomm NPU (QNN HTP fp16) {'accepted' if accepted else 'NOT accepted: ' + (str(npu[-1].get('error'))[:100] if npu else 'never tried')}")
    for demo in sorted(folder.rglob('demo.json')):
        d = json.loads(demo.read_text()); phone = demo.parts[len(folder.parts)]
        counts = [p.get('count') for p in d.get('app', {}).get('photos', [])]
        say(counts == [50, 20], f'{phone}: demo counts {counts} (expected [50, 20]) on {d.get("app", {}).get("engine", "?")}')
    for load in sorted(folder.rglob('cached_load.json')):
        d = json.loads(load.read_text()); phone = load.parts[len(folder.parts)]
        ref = next((json.loads(r.read_text()).get('ready_s') for r in reference_perf.glob(f'{phone}/**/cached_load.json')), None)
        limit = max(ref * SLOWER_FACTOR, ref + 1.0) if ref else READY_S_LIMIT
        say(d.get('ready_s') is not None and d['ready_s'] <= limit, f"{phone}: saved-copy load {d.get('ready_s')} s (limit {limit:.1f} s{', reference ' + str(ref) if ref else ', no reference run for this phone'})")
    for par in sorted(folder.rglob('parity-*.json')):
        s = json.loads(par.read_text())['summary']; phone = par.parts[len(folder.parts)]
        ref = next((json.loads(r.read_text())['summary'] for r in reference_parity.rglob(f"parity-{s['chip']}.json")), None)
        if not s.get('npu_engine'): say(False, f"{phone}: no NPU engine ran on {s['chip']}"); continue
        n = s['photos']
        if ref:
            say(s['npu_exact'] >= ref['npu_exact'] - PARITY_EXACT_SLACK, f"{phone}: NPU exact {s['npu_exact']}/{n} (reference {ref['npu_exact']}/{ref['photos']})")
            say(s['npu_median_ms'] <= ref['npu_median_ms'] * SLOWER_FACTOR, f"{phone}: NPU {s['npu_median_ms']} ms per photo (reference {ref['npu_median_ms']} ms)")
        else:
            say(s['npu_exact'] >= s['gpu_exact'] - PARITY_EXACT_SLACK, f"{phone}: NPU exact {s['npu_exact']}/{n} vs GPU {s['gpu_exact']}/{n} (no reference for {s['chip']})")
    print('\nVERDICT: ' + ('all checks passed' if ok else 'at least one check FAILED'))
    return ok


def phoneuse(a):
    folder, code = submit(a, 'phoneuse', ['com.asgard.pillcounter.PhoneUseTest'], a.timeout or '25m')
    phoneuse_summary(folder)
    raise SystemExit(code)


def phoneuse_summary(folder):
    """The phone's own apps (Settings, the shade, home) before, during and after Eir: dropped frames and frame times."""
    rows = [(f.parent.name, json.loads(m.group(1))) for f in sorted(Path(folder).rglob('logcat*'))
            for m in re.finditer(r'EirPhoneUse\s*:\s*(\{.*\})\s*$', f.read_text(errors='replace'), re.M)]
    if not rows: print('No phone-use results found.'); return
    out = [['phone', 'phase', 'app', 'frames', 'jank %', 'p50 ms', 'p90 ms', 'p99 ms', 'heat start→end', 'battery °C', 'Eir live checks']]
    for phone, r in rows:
        for app in ('settings', 'shade', 'home', 'eir'):
            g = r.get(app)
            if not g or not g.get('frames'): continue  # not used in this phase
            out.append([phone, r['phase'], app, g.get('frames'), g.get('jank_pct'), g.get('p50_ms'), g.get('p90_ms'), g.get('p99_ms'),
                        f"{r.get('thermal_start')}→{r.get('thermal_end')}", f"{r.get('battery_c_start')}→{r.get('battery_c_end')}", r.get('eir_live_events', '')])
    out = [['' if x is None else str(x) for x in row] for row in out]
    widths = [max(len(x) for x in col) for col in zip(*out)]
    for row in out: print('  '.join(x.ljust(w) for x, w in zip(row, widths)))
    print('\nshade = notification shade + quick settings (SystemUI); home = the launcher. Compare each app across phases: '
          'without_eir is the baseline.')


def parity_summary(folder):
    """One block per phone: counts vs the verified totals for the GPU (today's photo engine) and the NPU, and where they differ."""
    files = sorted(Path(folder).rglob('parity-*.json'))
    if not files: print('No parity results found.'); return
    for f in files:
        d = json.loads(f.read_text()); s = d['summary']; n = s['photos']
        print(f"\n{s['device']} ({s['chip']}, Android {s['android']}, {s['version']}): {n} photos")
        print(f"  GPU fp32 (photos today): {s['gpu_exact']}/{n} exact vs verified, total error {s['gpu_abs_error']} pills, {s['gpu_median_ms']} ms per photo")
        if s.get('npu_engine'):
            print(f"  NPU fp16 ({s['npu_engine']}): {s['npu_exact']}/{n} exact vs verified, total error {s['npu_abs_error']} pills, {s['npu_median_ms']} ms per photo")
            print(f"  NPU = GPU on {s['same_count']}/{n} photos (largest difference {s['max_count_difference']}); dots without a partner: {s['unmatched_dots']}")
            for line in s.get('differing', []): print('    differs: ' + line)
        else:
            print('  no supported NPU on this phone')


def local(a):
    if not a.no_build: build()
    adb = [ADB] + (['-s', a.serial] if a.serial else [])
    for apk in (APP, TEST): subprocess.run(adb + ['install', '-r', '-t', str(apk)], check=True)  # -r: the app keeps its history
    since = subprocess.run(adb + ['shell', 'date', '+%s'], capture_output=True, text=True, check=True).stdout.strip()
    cmd = adb + ['shell', 'am', 'instrument', '-w', '-r']
    cmd += ['-e', 'class', ','.join(methods(a.only) or ['com.asgard.pillcounter.PerfTest'])]
    subprocess.run(cmd + [f'{package_for(a)}.test/androidx.test.runner.AndroidJUnitRunner'])
    log = subprocess.run(adb + ['logcat', '-d', '-T', since + '.000', '-s', 'EirPerf:I'], capture_output=True, text=True).stdout
    table([('adb', r) for r in parse(log)])


def parse(text):
    rows = []
    for line in text.splitlines():
        m = re.search(r'EirPerf\s*:\s*(\{.*\})\s*$', line)
        if m:
            try: rows.append(json.loads(m.group(1)))
            except ValueError: pass
    return rows


def summary(folder):
    table([(f.parent.name, r) for f in sorted(Path(folder).rglob('logcat*')) for r in parse(f.read_text(errors='replace'))])


# ---- Developer-option toggles on a real phone (test-only, 2026-11-01; the S24 "developer tools crash the phone" report) ----------------
DEVTOGGLE = {'engine_toggles': 't1_engineToggles', 'gpu_live_max': 't2_gpuLiveMax', 'rapid_switch': 't3_rapidSwitch', 'model_test_live': 't4_modelTestLive'}


def devtoggle(a):
    """DevToggleTest: flips the engine Developer options while the camera screen is up (debug build) and records the phone's reaction.
    About 6 minutes a phone for everything; --only engine_toggles alone ~4 min. Default --timeout 10m, so one S24 costs at most 10 phone-minutes."""
    targets = [f'com.asgard.pillcounter.DevToggleTest#{DEVTOGGLE[s]}' for s in a.only or []] or ['com.asgard.pillcounter.DevToggleTest']
    folder, code = submit(a, 'devtoggle', targets, a.timeout or '10m')
    devtoggle_summary(folder)
    raise SystemExit(code)


def devtoggle_summary(folder):
    """Per change: did the counter come back, how long it took, main-thread freeze, System UI jank, and the system-level logcat lines."""
    rows = []
    for f in sorted(Path(folder).rglob('logcat*')):
        for line in f.read_text(errors='replace').splitlines():
            m = re.search(r'EirDevToggle\s*:\s*(\{.*\})\s*$', line)
            if m:
                try: rows.append((f.parent.name, json.loads(m.group(1))))
                except ValueError: pass
    if not rows: print('No EirDevToggle results found.'); return
    for phone, r in rows:
        print(f"\n{phone}: {r.get('scenario')} on {r.get('device')} ({r.get('soc')}), Eir {r.get('version')}" + (f"  skipped: {r['skipped']}" if r.get('skipped') else ''))
        for s in r.get('steps', []):
            sysui = s.get('systemui') or {}
            print(f"  {s.get('change'):<60} ready={s.get('ready')!s:<5} {s.get('ready_s', '?'):>6} s  live={str(s.get('live_engine'))[:22]:<22} main max {s.get('main_max_ms')} ms"
                  f"  SystemUI jank {sysui.get('jank_pct', '?')} % p99 {sysui.get('p99_ms', '?')} ms  heat {s.get('thermal')}  builds {s.get('engine_builds')}")
            for line in (s.get('system') or [])[:-1]: print('      ' + line)
            for e in s.get('errors') or []: print('      app error: ' + e)
    print('\nready: the photo counter came back after the change (false = the stall this test looks for). main max: longest the app took to answer a tap.\n'
          'SystemUI jank: the phone\'s own status bar / navigation process while the change settled (a few % is normal). The indented lines are the\n'
          'phone\'s own warnings during that step (DSP, GPU, ANR, low memory, watchdog, camera service).')


def secs(ms):
    return None if ms is None else round(ms / 1000, 1)


def table(rows):
    if not rows: print('No EirPerf results found.'); return
    out = [['phone', 'run', 'engine', 'tier', 'tuning', 'prewarm s', 'ready s', 'live eng s', 'photo eng s', 'load jank %', 'jank %', 'p95 ms', 'main p95', 'main max', 'cpu', 'live s', 'live fps', 'model ms', 'heat', 'photos', 'errors']]
    for phone, r in rows:
        s, a = r.get('steady', {}), r.get('app', {})
        out.append([phone, r.get('scenario'), a.get('engine', '?'), a.get('tier', '?'), r.get('gpu_tuning'), r.get('prewarm_s'), r.get('ready_s'), *([None, None] if r.get('load') == 'loaded' else [secs(a.get('live_session_ms')), secs(a.get('photo_session_ms'))]), r.get('startup', {}).get('jank_pct'), s.get('jank_pct'), s.get('p95_ms'),
                    s.get('main_p95_ms'), s.get('main_max_ms'), s.get('cpu_cores'), a.get('frame_events'), a.get('live_fps'),
                    a.get('live_model_ms', a.get('light_model_ms')), s.get('thermal_max'), ' '.join(str(p.get('count')) for p in a.get('photos', [])), len(a.get('errors', []))])
    out = [['' if x is None else str(x) for x in row] for row in out]
    widths = [max(len(x) for x in col) for col in zip(*out)]
    for row in out: print('  '.join(x.ljust(w) for x, w in zip(row, widths)))
    print('\nload jank % / jank %: frames that missed their deadline while loading / afterwards, with a spinner drawing every frame (a few % is smooth).\n'
          'prewarm s: the warm-up the terms screen starts (prewarm run only). ready s: launch until the photo counter was ready (live eng s / photo eng s: building each engine; 0.x s = loaded from the saved copy). main p95/max: ms the app took to answer a tap\n'
          '(over 100 feels sticky, over 500 frozen). cpu: cores busy on average. heat: Android thermal status (0 none, 1 light,\n'
          '2 moderate, 3 severe). photos: counts of the photos taken (demo expects 50 then 20). live s: seconds with live analysis (0 in a camera run: no camera).\n'
          'Blank steady columns: the counter never became ready.')


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    d = sub.add_parser('devices'); d.add_argument('--project')
    r = sub.add_parser('run'); r.add_argument('--device', action='append', required=True, help='Test Lab model id, optionally :API level (oriole = Pixel 6)')
    r.add_argument('--project'); r.add_argument('--no-build', action='store_true'); r.add_argument('--only', action='append', choices=SCENARIOS)
    q = sub.add_parser('parity'); q.add_argument('--device', action='append', required=True, help='Snapdragon phones, e.g. r0q:36 (S22) dm1q:35 (S23) e1q:36 (S24)')
    q.add_argument('--project'); q.add_argument('--no-build', action='store_true')
    pu = sub.add_parser('phoneuse'); pu.add_argument('--device', action='append', required=True); pu.add_argument('--project'); pu.add_argument('--no-build', action='store_true')
    rb = sub.add_parser('robo'); rb.add_argument('--device', action='append', required=True); rb.add_argument('--project'); rb.add_argument('--no-build', action='store_true')
    dt = sub.add_parser('devtoggle', help='flip the engine Developer options while the camera runs (DevToggleTest, debug build; e.g. --device e1q:36 --only engine_toggles)')
    dt.add_argument('--device', action='append', required=True); dt.add_argument('--project'); dt.add_argument('--no-build', action='store_true'); dt.add_argument('--only', action='append', choices=DEVTOGGLE)
    pb = sub.add_parser('probe', help="the review's H4 check on real phones (robo + demo/cached_load speed + NPU parity, then a verdict)")
    pb.add_argument('--snapdragon', action='append', help=f'Snapdragon phones, default {PROBE_SNAPDRAGON}'); pb.add_argument('--gpu', action='append', help=f'GPU phones, default {PROBE_GPU}')
    pb.add_argument('--project'); pb.add_argument('--no-build', action='store_true'); pb.add_argument('--dry-run', action='store_true', help='print the plan and the worst-case cost; build and submit nothing')
    pb.add_argument('--timeout', help=argparse.SUPPRESS)
    vd = sub.add_parser('verdict', help='PASS/FAIL for a downloaded probe run'); vd.add_argument('folder')
    s = sub.add_parser('summary'); s.add_argument('folder')
    l = sub.add_parser('local'); l.add_argument('--serial'); l.add_argument('--no-build', action='store_true'); l.add_argument('--only', action='append', choices=SCENARIOS)
    for x in (d, r, q, pu, rb, dt, pb, s, l): eir_android_id.add_arguments(x)
    for x in (r, q, pu, rb, dt): x.add_argument('--timeout', help='longest each phone may run, e.g. 6m (Blaze bills per phone-minute)')
    for x in (r, q, pu, rb, dt, pb): x.add_argument('--cap-cad', type=float, default=5.0, help='refuse runs that could take this month past this (CAD)')
    a = ap.parse_args()
    {'devices': devices, 'run': run, 'parity': parity, 'phoneuse': phoneuse, 'robo': robo_run, 'devtoggle': devtoggle, 'probe': probe, 'verdict': lambda a: raise_exit(0 if verdict(a.folder) else 1),
     'summary': lambda a: (summary(a.folder), parity_summary(a.folder), phoneuse_summary(a.folder), robo_summary(a.folder), devtoggle_summary(a.folder)), 'local': local}[a.cmd](a)
