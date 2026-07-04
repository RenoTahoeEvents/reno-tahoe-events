# Monthly Manual Sweep Prompt — Reno/Tahoe Events

**How to use this file:** Once a month, open this file, copy the ENTIRE
contents below the line, paste it into a brand new Claude chat, then
upload your current `events.json` when Claude asks for it. You should
NOT need to screenshot anything unless Claude specifically tells you it
hit a wall on a particular site and names exactly what it needs.

**Last updated: July 2026**, after a live scraper run revealed a
confirmed CAPTCHA wall affecting multiple sources and a real gap in how
generic titles ("DJ Night") get their performer names filled in.

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
- Confirmed bot-detection/CAPTCHA block (like Tixr, Resident Advisor,
  and now also confirmed on Late Nite Productions and Reno Improv — see
  below)
- Confirmed login-walled content
- A page that's genuinely image-only or JS-rendered with zero usable
  data in a plain fetch — verify this before assuming it
- Instagram/Facebook posts specifically (these are almost never
  accessible to search/fetch, unlike a venue's own website)

**When you find a real event, get the actual performer/DJ/speaker name,
not just the event type.** My scraper automatically promotes a name from
the description into the title if the title is generic ("DJ Night" →
"DJ Night – DJ Rekker"), but only if the name is actually in the data
somewhere. If you write "Weekly DJ night, house and techno" with no name,
that's exactly as unhelpful as before. If a site only advertises "DJ
Night" without saying who, dig one level deeper (their Instagram, their
ticket link, whatever it takes) before giving up on that one.

For every venue below, actually search for and fetch their real
events/calendar page (not just their homepage) and find real, dated
events in the next 90 days that aren't already in my events.json.

### Tier 1 — Real venues confirmed to need manual checking (verified July 2026)

- **Dead Ringer Analog Bar** (deadringernv.com) — CONFIRMED the current
  static entry for this venue has NO DJ name anywhere in its data (just
  "Weekly DJ night... house, techno, bass"). This is the single highest-
  value fix available: find out who's actually playing THIS specific
  week. Page shows image-only ticket buttons; follow the outbound
  Eventbrite/tixco.co links, the event name is usually in the URL
  itself. Also cross-check ra.co/clubs/164985, Bandsintown, and
  songkick.com/venues/4142034-dead-ringer-analog-bar.
- **Lo-Bar Social** (lobarsocial.com) — confirmed their own site only
  shows recurring weekly notices (Jazz Night Thursdays, Soul Night last
  Friday), not real dated events with names. Check their Eventbrite
  organizer page instead: pinklbs.eventbrite.com.
- **Chapel Tavern** (chapeltavern.com) — confirmed their events page is
  STALE (hasn't updated since Nov 2024). Instagram/Facebook only for
  anything current.
- **Village Well Taproom** — confirmed no real events calendar exists
  anywhere online. Facebook only.
- **Pignic Pub & Patio** — confirmed their own page explicitly says
  "click here to view our Facebook events" — Facebook only.
- **Alturas Bar / The Cellar Stage** — confirmed no dedicated venue
  website with a calendar. Check Songkick
  (songkick.com/venues/4408817-alturas-bar-cellar-stage) and
  Bandsintown instead.
- **Cypress** (music venue, Midtown) — confirmed no dedicated venue
  website with a calendar. Check Bandsintown instead.
- **Green House** — not yet individually verified, check for a real
  website and calendar from scratch.

### Tier 1b — CAPTCHA-blocked, confirmed via live scraper runs (do NOT
### attempt to automate — check manually only)
These returned a literal CAPTCHA challenge page instead of real content,
confirmed by reading the actual raw response:
- **Late Nite Productions** (lateniteproductions.com) — SiteGround
  "sgcaptcha" wall, failed 3 identical times
- **Reno Improv / The Foundry** (renoimprov.org) — same SiteGround
  CAPTCHA wall, failed on its first automated run despite being a
  confirmed real, live Tribe calendar. Real events genuinely exist here
  (renoimprov.org/shows/), just need to be checked manually now.
- **Alibi Ale Works** (alibialeworks.com, Truckee + Incline Village) —
  NOT actually CAPTCHA-blocked, but the REST API returns 404 even
  though the front-end page is real. Needs a different (HTML-based)
  scraping approach that hasn't been built yet — treat as manual for
  now.

### Tier 1c — AT RISK, was working, now intermittently blocked
- **Holland Project** — hit the same SiteGround CAPTCHA wall once after
  being reliable most of one full night of runs. Might be a temporary
  GitHub Actions IP-reputation issue rather than a permanent block.
  Still registered as an automated scraper — just keep an eye on it. If
  it's failing consistently by your next sweep, move it to Tier 1b.
- **Eventbrite** (general Reno listings) — started returning 405 errors
  on pages that worked fine the run before. Also possibly IP-reputation
  related. Still active, not yet disabled.

### Tier 1d — GRADUATED to automated scrapers, confirmed still working
- **Sky Tavern** — real Squarespace calendar, but currently has a
  separate bug (date extraction failing on ~all events) that's being
  worked on independently of this sweep — not a manual-check item, just
  flagging it's temporarily not contributing events either.
- **Big Blue Adventure, Holland Project** (see Tier 1c caveat above),
  **Downtown Reno Partnership, Visit Lake Tahoe, South Tahoe Now, The
  Reno Scene, Atlantis Casino, Grand Sierra Resort, Reno Aces,
  Ticketmaster (general + venue-specific), Laugh Factory @ Silver
  Legacy** — all confirmed actively working as of the last run.

### Corrected/removed
- ~~LEX Nightclub~~ — CONFIRMED CLOSED. Folded into "GSR Nightlife."
  Likely already covered by the existing GSR scraper.
- ~~EDGE Nightclub @ Peppermill~~ — confirmed image-only flyers, zero
  extractable dates/text anywhere in the raw HTML. Facebook only.

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
as a candidate for a permanent automated scraper.

### When you're done, give me:
1. A short summary of what you found per venue, including "nothing new"
   or the specific reason for anything you genuinely couldn't access
2. Properly formatted JSON entries for everything new, matching my
   existing schema exactly — and make sure the description names the
   actual performer/DJ/speaker if one exists, since generic descriptions
   produce generic titles
3. The complete, updated events.json file as a downloadable file —
   not a snippet — ready for me to replace on GitHub as-is
4. Confirm no duplicate IDs and valid JSON before handing it back
5. If you found a new venue worth turning into a permanent scraper,
   say so clearly, verify it the same rigorous way (real search + fetch,
   not assumption), and offer to build it
