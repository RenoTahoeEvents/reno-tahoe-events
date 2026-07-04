# Monthly Manual Sweep Prompt — Reno/Tahoe Events

**How to use this file:** Once a month, open this file, copy the ENTIRE
contents below the line, paste it into a brand new Claude chat, then
upload your current `events.json` when Claude asks for it. You should
NOT need to screenshot anything unless Claude specifically tells you it
hit a wall on a particular site and names exactly what it needs.

**Last fully audited: July 2026.** Every venue below was individually
verified with a real search + fetch that night — not guessed from memory
or assumed from a name. Two real scrapers were built and two more venues
were added to Ticketmaster coverage as a direct result of that audit
(see "Graduated to automated" below).

---
---
COPY EVERYTHING BELOW THIS LINE INTO A NEW CLAUDE CHAT
---
---

I'm doing my monthly manual sweep for my Reno/Tahoe events site
(reno-tahoe-events). I'll upload my current `events.json` — please wait
for that upload before finishing your work, and check every event you
find against it so we don't create duplicates.

**Default rule: search and fetch everything yourself first.** Don't ask
me for a screenshot as a first resort. Only ask me for one if you've
actually tried to fetch the page and confirmed one of these specific
blockers — and if you do ask, name the exact URL and exact reason (e.g.
"this returned a 403" or "this is image-only with no text/dates in the
raw HTML"), not a vague "can you check this site":
- Confirmed bot-detection/CAPTCHA block (like Tixr, Resident Advisor)
- Confirmed login-walled content
- A page that's genuinely image-only or JS-rendered with zero usable
  data in a plain fetch — verify this before assuming it
- Instagram/Facebook posts specifically (these are almost never
  accessible to search/fetch, unlike a venue's own website)

For every venue below, actually search for and fetch their real
events/calendar page (not just their homepage) and find real, dated
events in the next 90 days that aren't already in my events.json.

### Tier 1 — Real venues confirmed to need manual checking (verified July 2026)

- **Dead Ringer Analog Bar** (deadringernv.com) — page shows image-only
  ticket buttons; follow the outbound Eventbrite/tixco.co links, the
  event name is usually in the URL itself. Also cross-check
  ra.co/clubs/164985, Bandsintown, and
  songkick.com/venues/4142034-dead-ringer-analog-bar.
- **Lo-Bar Social** (lobarsocial.com) — confirmed their own site only
  shows recurring weekly notices (Jazz Night Thursdays, Soul Night last
  Friday), not real dated events. Check their Eventbrite organizer page
  instead: pinklbs.eventbrite.com. Also has a Tixr group
  (tixr.com/groups/lobarsocial) but Tixr is confirmed CAPTCHA-blocked —
  don't bother with that one.
- **Chapel Tavern** (chapeltavern.com) — confirmed their events page is
  STALE (hasn't updated since Nov 2024) and Reno Scene confirms
  "no concerts currently scheduled." Instagram/Facebook only for
  anything current.
- **Village Well Taproom** — confirmed no real events calendar exists
  anywhere online. Facebook only.
- **Pignic Pub & Patio** — confirmed their own page explicitly says
  "click here to view our Facebook events" — they don't host their own
  calendar at all. Facebook only.
- **Alturas Bar / The Cellar Stage** — confirmed no dedicated venue
  website with a calendar. Check Songkick
  (songkick.com/venues/4408817-alturas-bar-cellar-stage) and
  Bandsintown instead.
- **Cypress** (music venue, Midtown) — confirmed no dedicated venue
  website with a calendar. Check Bandsintown instead.
- **Green House** — not yet individually verified, check for a real
  website and calendar from scratch.

### Tier 1b — GRADUATED to automated scrapers (do NOT need manual checks anymore)
These were confirmed real, live, structured calendars and are now
covered by working automated scrapers. Only spot-check if you notice
something clearly missing:
- ~~The Foundry / Reno Improv~~ → confirmed real Tribe/WordPress
  calendar at renoimprov.org, now automated
- ~~Alibi Ale Works (Truckee + Incline Village)~~ → confirmed real
  Tribe-style calendar, now automated
- ~~Sky Tavern~~ → confirmed real Squarespace calendar, now automated
- ~~Laugh Factory @ Silver Legacy~~ → confirmed real Ticketmaster venue
  with near-nightly shows, added to dedicated venue tracking

### Corrected/removed
- ~~LEX Nightclub~~ — CONFIRMED CLOSED. The standalone LEX Nightclub
  brand shut down; lexnightclub.com is dead. Nightlife at Grand Sierra
  now operates under "GSR Nightlife" — check
  grandsierraresort.com/entertainment/gsr-nightlife if you want to
  track this going forward, but it's likely already covered by the
  existing GSR scraper's concerts-and-shows page.
- ~~EDGE Nightclub @ Peppermill~~ — confirmed same as Peppermill's main
  site: image-only flyers with zero extractable dates/text anywhere in
  the raw HTML (checked peppermillnightlife.com/event-calendar/
  directly). Genuinely not automatable without a browser. Facebook is
  the only real source.

### Tier 2 — Real venues, confirmed to actively block automated access
Skip trying to scrape/build automation for these (already confirmed
CAPTCHA/403 blocked) — but DO check manually via search for upcoming events:
- Crystal Bay Casino – Crown Room (Tixr-ticketed)
- Valhalla Tahoe – Heller Estate
- Bartley Ranch – Robert Z. Hawkins Amphitheater

### Tier 3 — Casino showrooms/theaters (not individually re-verified this
### round — check every other month, likely partially covered already
### via GSR/Ticketmaster scrapers)
- Silver Legacy Casino + Grande Exposition Hall
- Grand Sierra Resort – Grand Theatre
- Bally's Lake Tahoe – HQ Center Bar and Opal Nightclub
- Harrah's/Harveys Lake Tahoe – South Shore Room
- Peppermill Resort – Terrace Lounge (confirmed image-only, see above)
- GSR Arena
- Celebrity Showroom – Nugget Casino Resort

### Tier 4 — Theaters/performing arts (not individually re-verified this
### round — occasional, seasonal programming)
- Reno Little Theater / Good Luck Macbeth
- UNR – Redfield Proscenium Theatre, Church Fine Arts
- Reno Ballroom
- Corrigan's Lost Highway

### Tier 5 — Truckee / North Shore (not individually re-verified this round)
- Obexer's Boat Company, Homewood
- Montrêux Golf & Country Club

### Seasonal — only actually dig into these near their announcement window
- Artown headliners: announced each spring, scraper picks it up once live
  on renoisartown.com — only check in March/April if nothing's showing yet

### Also do this every sweep (not optional)
Search for any NEW bars/venues/breweries in Reno, Sparks, Midtown,
Truckee, or South Lake Tahoe that aren't in my events.json at all and
that haven't been checked before. For anything you find, check if it has
a real structured events calendar (not just Instagram) — if so, flag it
as a candidate for a permanent automated scraper, the way Alibi Ale
Works, Sky Tavern, and Reno Improv became scrapers instead of manual
entries.

### When you're done, give me:
1. A short summary of what you found per venue, including "nothing new"
   or the specific reason for anything you genuinely couldn't access
2. Properly formatted JSON entries for everything new, matching my
   existing schema exactly
3. The complete, updated events.json file as a downloadable file —
   not a snippet — ready for me to replace on GitHub as-is
4. Confirm no duplicate IDs and valid JSON before handing it back
5. If you found a new venue worth turning into a permanent scraper,
   say so clearly, verify it the same rigorous way (real search + fetch,
   not assumption), and offer to build it
