# How this was built

I'm a pharmacist. Eir Count went from the first tray photo (September 13, 2026) to a redesigned app with its sixth model generation (November 21, 2026) in about ten weeks:

- native Android and iPhone apps;
- a training and rendering pipeline;
- tested builds on 13+ phone models;
- a hardware design.

Most of the engineering was done by Claude Code agents, mostly Opus with some Fable, working under my direction. The project started in ChatGPT and moved to Claude Code on September 17, where it has stayed. ChatGPT was later used as an outside reviewer.

This page covers how that worked in practice.

## My part

- **Product rules.**
  - Count the main tray surface only.
  - Never show a percentage.
  - The pharmacist verifies every count.
  - No accounts and no cloud.
  - No italics. (Some rules are taste.)
- **Data.** I photographed trays and reviewed labels, and I confirmed or rejected every proposed label correction.
- **Decisions.** Pricing, design direction, which model ships, and what goes on my phone or a tester's phone.
- **Testing on real hardware.** My own Galaxy S23 and iPhone, plus a friend's phone. Their real-world counts and corrections shaped round 06.

## How the agents worked

**Context that survives sessions.**
- A long handover file (`HANDOVER.md`, about 90 KB) records the state of everything: versions, where each file lives, decisions I made that must not be reopened, and the next steps in order.
- A small memory of my preferences and standing rules sits beside it.
- A new session reads both first, so each session starts where the last one ended.

**Remote GPU work with rules.** Training and rendering ran on my Windows PC with an RTX 5090, over SSH. That machine also runs my own jobs, so the agents worked under written rules:
- at most two of their runs at once;
- never touch my jobs;
- stop jobs by writing a STOP file, never by killing processes;
- judge timing from the job's own log clock, not from guesses.

Most rules were added as problems came up, and things still slipped: once, three runs ended up on the GPU together because one job queue didn't see the other's job.

**A coordinator and builders.**
- For larger changes, one session coordinated while others did the work.
- The app redesign was split across four builders working in parallel: foundation, camera, history, and the other screens. Their parts compiled together on the first try.
- The v0.19.1 capture checks were written by an Opus agent. The coordinating session reviewed and tested its work and sent it back once for fixes before the build was accepted.

**Reference first, then port.** New logic was written and measured in Python against real photos. Only then was it ported to Kotlin and Swift. Tests compare the app versions with the Python reference on recorded test cases. Bugs then show up as a failed comparison, not as a wrong count at a pharmacy counter.

**Independent reviews before release.** Each review was a separate agent given the code, not the builder's own conclusions:

| Review | What it found |
|---|---|
| Opus review, iPhone parity port | Bugs fixed before the build was installed |
| Opus review, Android store build | Fixed in the next build |
| Fable review, store readiness | Two billing launch blockers, both confirmed in Test Lab phone logs. One would have locked out paying users whose Google Play app was out of date. Also ranked speed fixes, such as one phone choosing an NPU 10× slower than its GPU |
| ChatGPT, outside security review | Recovered the exact model from the app file. Also found a sync path that could delete files when given a malicious record ID. That bug was fixed. The model finding changed the plan for protecting the model (see [how-it-works.md](how-it-works.md#what-protecting-the-model-taught-me)) |

**Guardrails on money and on what leaves the project.**
- Firebase Test Lab is billed per phone-minute, and Google's budgets only send emails. So the runner script enforces my CAD $5/month cap itself and logs every run's cost.
- Nothing is pushed to the website, sent to testers, installed on my phone or published without my explicit OK.

## What worked

- **Measuring before deciding.** The biggest gains came from finding out what was actually wrong. Examples:
  - the stress probe that ranked the model's weak spots;
  - the discovery that the answer key itself contained duplicate labels;
  - the Test Lab finding that a "laggy app" was really a 105-second first-time GPU setup.
- **Saying when something didn't work.** Several ideas were measured and dropped:
  - more render weight;
  - cropping to the tray before counting;
  - alternative decoders;
  - a "spread the pills" hold, which would have blocked a third of correct dense counts.
  Writing those down saved repeating them.
- **Small, reviewable releases,** each with a README that lists what was checked and what was not.

## What was hard

- **Real phones.** Emulators have no NPU, and Test Lab has quotas. Some things, like live counting speed with the newest model, can only be confirmed by a person holding a phone over a tray.
- **Keeping the model private.** It can't be done on the phone alone (see the security review above).
- **Ground truth.** Labels are only as good as the review behind them, and a model's own early mistakes can end up in its answer key.
