# Specimen Plate: a portable style spec

Give this whole file to a designer or an AI model, then say what you want made. It describes one visual style precisely enough to reproduce its feeling in new, original work: an app screen, a web page, an image, a logo sheet or a short film.

The last section holds settings for one specific project (Eir Count). Swap or delete it for other projects; everything above it is general.

## Where the style comes from

The style is modelled on Anthropic's 2026 launch films and pages for its Claude Fable and Opus models. Those pieces introduce new technology using the visual language of old scientific records: specimen plates, survey maps, micrographs and early space photographs, each shown alone on aged paper.

This spec captures how that look works so it can be applied to other subjects. It is a style reference only. Make original imagery for the subject at hand. Do not reproduce Anthropic's logo, wordmark, footage, typefaces, or its numeral built from butterflies.

## The feeling to hit

The viewer should feel as if they have opened a drawer in a natural history museum: quiet, careful and a little awed. Each thing on screen is small, singular and treated as precious. The unspoken message is that the subject belongs to a long human tradition of looking closely and writing things down.

Restraint carries the emotion. Nothing glows, nothing rushes and nothing shouts, so the viewer supplies the scale of the idea themselves.

A quick test: if a draft feels exciting, loud or futuristic, it has missed. If it feels like a page from an old reference book that someone clearly loved, it has landed.

## The seven rules, and why each matters

1. **One object, one page.** Show a single subject near the centre and leave most of the frame empty. The emptiness is what makes the object feel important; a second object turns a specimen into a pile.
2. **Found, never rendered.** Imagery should look borrowed from an archive: engraved or lithographed plates, survey maps, microscope images, instrument readouts, early space photographs. Anything that looks like 3D rendering, stock illustration or generated gloss breaks the spell, because the style's authority comes from looking recorded by a person.
3. **Paper is the ground.** Use a matte paper surface with visible grain where a modern design would use black, white or a gradient. Paper makes a screen read as a printed page, which slows the viewer down.
4. **A ladder of scale.** When there are several images in sequence, order them from small to vast (for example cell, leaf, insect, map, moon, horizon). The climb is how the style suggests ambition without stating it.
5. **Hands show.** Keep traces of the person who made the record: an ink dot, a hand-drawn line, a pencil tick, a slightly uneven edge. Use these sparingly; one trace per frame is enough.
6. **Stillness, then one small movement.** In motion work, hold an object still, then let it move once and slightly. A single movement after stillness reads as life; constant movement reads as advertising.
7. **Words stay small.** Set type quietly and modestly. The picture makes the claim and the words only label it.

## Materials

### Ground

- A warm off-white to tan paper. Working defaults: cream `#EFE7D6`, or a cooler bone `#F4F3EE` when yellow is unwanted.
- Fine grain over the whole ground, strong enough to see at arm's length and no stronger. On the web, an SVG `feTurbulence` fractal noise at a base frequency near 0.85, multiplied over the ground at roughly 20% opacity, works well. A second, very low-frequency layer (near 0.012) at lower opacity gives the slight unevenness of old paper.
- Matte black is the only alternative ground, used for the occasional image that needs darkness (a micrograph, a night sky).
- Keep texture on the paper only. Photographs, charts and anything the viewer must read accurately stay clean.

### Colour

- Ink: a near-black with a slight warm or green cast, never pure `#000000`.
- Image colours: muted, as if printed in ink a century ago.
- One saturated accent per frame at most, small in area (a cluster of orange dots, one red shape, a single gold mark).
- No gradients, glows, neon or glassy translucency.

### Imagery

- Choose the specimen from the subject's own world. For a pharmacy product it is a tablet or a sprig of a medicinal plant; for a mapping product, a fragment of a survey sheet. The object should be something a person in that field would recognise and respect.
- Draw or source it in a plate style: flat, even light; fine line or stipple shading; no drop shadow, or at most a faint contact shadow.
- Good public-domain sources for real archival material include the Biodiversity Heritage Library, NASA's image library, the USGS, the Wellcome Collection and Smithsonian Open Access. Check each item's licence.
- A circular crop, as if seen through a microscope or a porthole, is a useful variation for instrument and map images.
- For image generators, a starting prompt: "A single [object], drawn as a 19th-century natural history lithograph plate, centred on aged off-white paper with fine grain, wide empty margins, muted printed-ink colours with one small saturated accent, flat even light, no drop shadow, no text, no border."

### Type

- Titles, numerals and sentences: a warm book serif at regular or medium weight (free options: Newsreader, Source Serif, Lora). Slightly tight tracking on large sizes.
- Labels: a small monospace in capitals with wide letter-spacing (about 11 to 13 px, tracking 0.12 to 0.16 em), for things like plate numbers, dates and section tags.
- Interface controls and status lines: a plain, neutral sans, so working parts stay legible.
- Sentence case throughout. No exclamation marks. Short, plain, declarative sentences.

### Labels and captions

Borrow the vocabulary of printed plates: "Pl. I", "Fig. 1", "a.", "b.", plate numbers, thin leader lines from a label to a part, dotted leader lines in a contents list. One or two such devices per frame is plenty.

### Layout

- The object takes roughly 20 to 40% of the frame; margins are generous on every side.
- Centre the object, or place it slightly off-centre with its caption balancing it.
- A thin ink rule inset from the edge can frame a page like a printed plate.
- Mount photographs like tipped-in plates: a narrow lighter mat, a hairline rule, and a caption beneath.

## Motion

These are starting points, to adjust by eye.

- Hold each object for two to four seconds before anything happens.
- One movement per shot, lasting about a second, easing out gently. No bounce or overshoot.
- Transitions are plain cuts or slow fades (roughly half a second to a second). A fade up from black suits an opening.
- In a film with speakers, alternate a calm talking shot with a single-object cutaway timed to the claim being made. A small claim gets a small object; a larger claim gets a larger one.
- End on one emblem for the piece, then a wordmark alone on paper.
- Sound was not studied for this spec. Treat music and voice as open, and keep them as restrained as the picture.

## Recipes

**App screen.** Paper ground with grain. One hero element (a number, a mounted photo or a mark) with a monospace label above and a serif caption below. Controls in plain sans with clear contrast and touch targets of at least 44 px. Function comes first: if users need to judge colour or detail in a photo, show it unfiltered.

**Landing page.** A full-height opening with one object, the title in serif beneath or over it, and a short numbered contents list with dotted leaders. Body sections alternate text with single mounted images.

**Still image or poster.** One object, wide margins, a plate number in one corner and a one-line caption.

**Short film.** Follow the motion notes above and order the cutaways by scale.

## How it goes wrong

- **Clutter.** Several objects in a frame become a scrapbook. Remove things until one remains.
- **Costume vintage.** Coffee rings, heavy torn edges, typewriter fonts and a sepia wash over everything read as a theme party. The style is a well-kept archive, clean and exact.
- **Paper and serif with no specimen.** A cream background and a serif font alone produce a generic look. The singular object and its plate labels are what make the style.
- **Over-texturing.** Grain heavy enough to notice first, or laid over photos and text, hurts legibility.
- **Losing function.** In working software, counts, warnings and buttons must stay obvious. Keep text contrast at 4.5:1 or better and never tint anything the user has to verify.
- **Copying the source.** Reusing the source's specific images or emblem is both a rights problem and a failure of the brief, which asks for the method applied to a new subject.

## Checks before calling it done

- Is there one clear object per frame, with most of the frame empty?
- Does every image look recorded by a person, with nothing glossy or rendered?
- Is there at most one saturated accent?
- Is the type quiet, and would the frame still work with the words covered?
- Can every functional element be read and used without effort?
- Is all imagery original or properly licensed?

## Reference: what the source pieces show

These illustrate the rules. They are examples of the method, not images to recreate.

- **Fable 5 film (1:53).** Two staff speak in a muted green room with arches. Cutaways: an X-ray of a circuit board beside a petri dish of orange colonies, overlapping red circles on paper, a white curve on blueprint blue, a single sprig of leaves, three glowing orange cells on black. It ends with illustrated butterflies in flight on cream paper.
- **Fable 5.1 film (1:25).** The speaker sits before tall translucent banners printed with a star chart, a marbled planet and botanical forms. Cutaways: one ink dot that grows into a branching tree of nodes, then a punched data card, a colourful geological map and the cratered moon, each seen through a round lens. It ends on a pale daytime moon among pink clouds.
- **Opus 5.5 page.** Fades up from black as three tall strips like torn pages: yellow paper with ink marks over a striped texture, the thin blue and orange line of Earth's atmosphere at orbital sunrise, and a rust-coloured micrograph. The middle strip widens to fill the screen.
- **Fable 5.2 page.** A sun breaking over a planet's curved edge, violet and white on black; the darkest of the set.
- **Brand film (1:12).** The exception in pace: quick, warm clips of ordinary people's projects with single words on screen, closing on one serif sentence on off-white paper.

## Project settings: Eir Count

Replace this section for any other project.

- **Product.** Eir Count, by Asgard Pharmaceuticals: a phone app that counts tablets and capsules from a photo of a counting tray. The pharmacist always verifies the count. The app ships in English and French.
- **Ground.** Bone `#F4F3EE`, with a lighter surface `#FBFAF7` for mats and fields. The camera screen uses dark bands `#15221C`.
- **Colour.** Ink `#1B2620`; secondary text `#3A4038`; label grey `#4E5246`; western hemlock green `#457A65` for the mark and fills; deep green `#1E5341` for text and buttons; gold `#B08D56` as the single accent, decorative only.
- **Type.** Newsreader for counts and titles, Instrument Sans for the interface, IBM Plex Mono for labels. No italics anywhere; this is the owner's decision.
- **Mark.** A sprig of western hemlock whose needles are capsule shapes, tilted slightly, with one short needle followed by a gold dot (the one tablet set apart to check).
- **Plate vocabulary.** "Plate № 214" for a count, "Fig. 1" under its photo, "Index of plates" for history, "Pl. I" on the welcome screen.
- **Photos.** Tray photos stay in true colour on the camera and count screens, because pills are verified by colour. Small history thumbnails may be tinted.
- **Confidence wording.** "High confidence · 3 of 3 photos agree" or "Please check". Never a percentage.
