#!/usr/bin/env python3
"""
Reno / Tahoe Events — Master Scraper v2
Runs hourly via GitHub Actions. Pulls from all available sources,
merges with static seed events, deduplicates, writes events.json.

Deduplication strategy:
  - Static events have hand-crafted IDs (dr_, gsr_, nug_, etc.)
  - Scraped events get prefixed IDs (drp_, trs_, ra_, tm_, etc.)
  - Two-pass dedup: exact ID match, then fuzzy title+date match
  - Static events always win over scraped duplicates
"""

import json, re, time, sys, os, hashlib, argparse, html
from datetime import date, timedelta, datetime
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError
from urllib.parse import quote

# Playwright is optional — only needed for Tixr sources (which block plain
# HTTP requests with bot detection). If it's not installed, the Tixr
# scraper skips cleanly instead of crashing the whole run.
try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

# ── CONFIG ────────────────────────────────────────────────────────────────────

TODAY  = date.today().isoformat()
UNTIL  = (date.today() + timedelta(days=120)).isoformat()

# Path to events.json relative to this script (../events.json)
EVENTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'events.json')

# All keywords that confirm an event is in our coverage area
RENO_KEYWORDS = {
    'reno','sparks','washoe','tahoe','truckee','incline village','crystal bay',
    'stateline','south lake tahoe','kings beach','tahoe city','gerlach',
    'black rock','burning man','playa','pyramid lake','virginia city',
    'bartley ranch','wingfield','idlewild','rancho san rafael','sky tavern',
    'dead ringer','cargo concert','whitney peak','grand sierra','nugget casino',
    'atlantis','peppermill','silver legacy','eldorado','pioneer center',
    'holland project','crystal bay casino','heavenly village','sand harbor',
    'valhalla tahoe','bowers mansion','carson city','fernley','minden',
    'gardnerville','zephyr cove','lake tahoe',
}

# Static event ID prefixes — scrapers must NEVER use these
# This ensures scraped events never overwrite hand-curated static ones
STATIC_PREFIXES = {
    'dr_','gsr_','nug_','atl_','rec_','sl_','pc_','hp_','alp_','cu_','bar_',
    'cbc_','val_','at_','hvc_','hbr_','lal_','lex_','edge_','bal_','aces_',
    'st_','lobar_','bower_','jresort_','ftc_','ftf_','ftw_','ftt_','renomkt_',
    'ttrumkt_','tmkt_','rfm_','smkt_','ccb_','motb_','ttru_','yoga_',
    'han_','wolf_','rjo_','bba_','dnf_','ritual_','laugh_','bruka_',
    'classical_','cargo_','pops_','unr_','alt_','gh_','cor_','pcl_',
    'cyp_','emp_','rb_','rlt_','svg_','artown_','revo_','glow_',
}

UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
      'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36')

# ── HTTP HELPERS ──────────────────────────────────────────────────────────────

def get(url, headers=None, timeout=15):
    h = {'User-Agent': UA, 'Accept': 'application/json,text/html,*/*'}
    if headers: h.update(headers)
    try:
        req = Request(url, headers=h)
        with urlopen(req, timeout=timeout) as r:
            return r.read().decode('utf-8', errors='replace')
    except Exception as ex:
        # Some sites (e.g. cargoconcerthall.com) throw a TLS handshake
        # error under Python's default SSL context but work fine with a
        # standard browser. Retry once with a relaxed context before
        # giving up — this is NOT disabling certificate validation for
        # every request, only as a last-resort fallback on failure.
        if 'SSL' in str(ex) or 'TLS' in str(ex):
            try:
                import ssl
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                req = Request(url, headers=h)
                with urlopen(req, timeout=timeout, context=ctx) as r:
                    return r.read().decode('utf-8', errors='replace')
            except Exception as ex2:
                print(f'  GET error {url[:70]}: {ex2}', file=sys.stderr)
                return None
        print(f'  GET error {url[:70]}: {ex}', file=sys.stderr)
        return None

def post_json(url, payload, headers=None):
    h = {'User-Agent': UA, 'Content-Type': 'application/json', 'Accept': 'application/json'}
    if headers: h.update(headers)
    try:
        req = Request(url, data=json.dumps(payload).encode(), headers=h, method='POST')
        with urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode())
    except Exception as ex:
        print(f'  POST error {url[:70]}: {ex}', file=sys.stderr)
        return None

# ── TEXT HELPERS ──────────────────────────────────────────────────────────────

def strip_html(s):
    return re.sub(r'<[^>]+>', '', s or '').strip()

def clean(s):
    s = str(s or '')
    for _ in range(3):
        u = html.unescape(s)
        if u == s: break
        s = u
    return re.sub(r'\s+', ' ', strip_html(s)).strip()

def is_local(text):
    return any(k in text.lower() for k in RENO_KEYWORDS)

def scrape_id(prefix, uid):
    """Generate a scraper-prefixed ID that never collides with static IDs."""
    h = hashlib.md5(str(uid).encode()).hexdigest()[:10]
    return f's_{prefix}_{h}'

def parse_date(s):
    s = (s or '').strip()
    for fmt in ('%Y-%m-%dT%H:%M:%S','%Y-%m-%dT%H:%M:%SZ','%Y-%m-%d',
                '%m/%d/%Y','%B %d, %Y','%b %d, %Y','%b. %d, %Y','%d %B %Y'):
        try:
            return datetime.strptime(s[:len(fmt)], fmt).strftime('%Y-%m-%d')
        except: pass
    m = re.search(r'(\d{4}-\d{2}-\d{2})', s)
    return m.group(1) if m else None

def to_12h(s):
    if not s: return None
    m = re.search(r'T?(\d{1,2}):(\d{2})(?::\d+)?(?:\s*(AM|PM))?', s, re.I)
    if not m: return None
    hh, mm = int(m.group(1)), int(m.group(2))
    ap = (m.group(3) or '').upper()
    if not ap:
        ap = 'PM' if 12 <= hh < 24 else 'AM'
    hh = hh % 12 or 12
    return f'{hh}:{mm:02d} {ap}'

CAT_KEYWORDS = [
    ('dj',       ['dj','electronic','techno','house music','nightclub','edm',
                  'dubstep','bass music','drum and bass','trance']),
    ('concert',  ['concert','live music','band','show','perform','tour','music']),
    ('comedy',   ['comedy','comedian','stand-up','improv','laugh']),
    ('theater',  ['theater','theatre','play','musical','ballet','opera','dance']),
    ('festival', ['festival','fest ','fair ','expo ']),
    ('art',      ['art','exhibit','gallery','museum']),
    ('sports',   ['baseball','football','basketball','hockey','soccer','sport']),
    ('running',  ['run','marathon','5k','10k','half marathon','race']),
    ('triathlon',['triathlon','tri ']),
    ('mtb',      ['mountain bike','mtb','enduro','downhill']),
    ('cycling',  ['cycling','gran fondo','gravel','velodrome']),
    ('food',     ['food','beer','wine','cocktail','tasting','bbq','rib','taco']),
    ('market',   ['market','farmers market','craft fair','vendor']),
    ('outdoor',  ['hike','hiking','outdoor','nature','trail']),
    ('fishing',  ['fishing','fish','angling','derby']),
    ('fireworks',['fireworks','pyrotechnic']),
    ('family',   ['family','kids','children','youth']),
    ('wellness', ['yoga','wellness','meditation','fitness']),
    ('casino',   ['casino','showroom','resort show']),
]

def guess_cat(title, desc=''):
    text = (title + ' ' + (desc or '')).lower()
    for cat, kws in CAT_KEYWORDS:
        if any(kw in text for kw in kws):
            return cat
    return 'community'

def make_ev(eid, title, cat, date_str, region, venue, addr,
            time_str, price, is_free, desc, tags, url, src):
    """Build a normalized event dict, returning None if invalid or out of range."""
    if not date_str: return None
    if date_str < TODAY or date_str > UNTIL: return None
    if not is_local(f'{title} {venue} {addr} {desc}'): return None
    if not title or not venue: return None
    # Defensive type coercion — never let a non-string slip through into the
    # frontend, which would crash JS string methods (.toLowerCase, localeCompare etc)
    region = region if isinstance(region, str) and region else 'reno'
    cat    = cat if isinstance(cat, str) and cat else 'community'
    return {
        'id':     eid,
        'title':  html.unescape(clean(title))[:120],
        'cat':    cat,
        'date':   date_str,
        'region': region,
        'venue':  html.unescape(clean(venue))[:80],
        'addr':   html.unescape(clean(addr or '')),
        'time':   time_str if isinstance(time_str, str) else None,
        'price':  price if isinstance(price, str) else None,
        'isFree': bool(is_free),
        'desc':   html.unescape(clean(desc or ''))[:150],
        'tags':   (tags if isinstance(tags, list) else [])[:4],
        'url':    url if isinstance(url, str) else '',
        'src':    src[:40] if isinstance(src, str) else '',
    }

# ── WORDPRESS TRIBE EVENTS SCRAPER (used by many venues) ─────────────────────

def scrape_tribe(base_url, src_name, region, default_venue='', default_addr='',
                 title_prefix='', extra_tags=None, max_pages=20):
    """Generic scraper for any site using The Events Calendar (Tribe) WordPress plugin."""
    events = []
    prefix = re.sub(r'[^a-z]', '', src_name.lower())[:6]
    pages_fetched = 0
    total_html_len = 0
    total_items_seen = 0
    no_date_or_title = 0
    non_dict_items = 0
    make_ev_rejected = 0
    last_error = None
    for page in range(1, max_pages + 1):
        url = (f'{base_url}/wp-json/tribe/events/v1/events'
               f'?start_date={TODAY}&end_date={UNTIL}&per_page=50&page={page}')
        raw = get(url)
        if not raw:
            last_error = f'no response on page {page}'
            break
        pages_fetched += 1
        total_html_len += len(raw)
        try: data = json.loads(raw)
        except Exception as ex:
            last_error = f'JSON parse failed on page {page}: {ex}'
            break
        # Defensive: some sites return a bare JSON array instead of
        # {"events": [...]}, or an error object with no "events" key at all.
        # Never assume shape — just bail out to an empty list if unexpected.
        if isinstance(data, dict):
            items = data.get('events', [])
        elif isinstance(data, list):
            items = data
        else:
            last_error = f'unexpected JSON shape on page {page}: {type(data).__name__}'
            break
        if not isinstance(items, list) or not items: break
        total_items_seen += len(items)
        for item in items:
            if not isinstance(item, dict):
                non_dict_items += 1
                continue
            d     = parse_date(item.get('start_date', ''))
            title = clean(item.get('title', ''))
            if not d or not title:
                no_date_or_title += 1
                continue
            vd    = item.get('venue') or {}
            if not isinstance(vd, dict): vd = {}
            venue = clean(vd.get('venue', '') or default_venue) or default_venue
            addr  = ', '.join(filter(None, [
                clean(vd.get('address', '')),
                clean(vd.get('city', '')),
                clean(vd.get('stateprovince', '')),
            ])) or default_addr
            desc  = clean(item.get('description', ''))[:300]
            link  = item.get('url', base_url + '/events/')
            display = f'{title_prefix}{title}' if title_prefix else title
            ev = make_ev(
                scrape_id(prefix, item.get('id', title + d)),
                display, guess_cat(title, desc), d, region,
                venue, addr, to_12h(item.get('start_date', '')),
                None, False, desc,
                (extra_tags or []),
                link, src_name)
            if ev: events.append(ev)
            else: make_ev_rejected += 1
        if len(items) < 50: break
        time.sleep(0.5)
    print(f'    [{src_name}] pages_fetched={pages_fetched}, total_html_len={total_html_len}, '
          f'items_seen={total_items_seen}, no_date_or_title={no_date_or_title}, '
          f'non_dict_items={non_dict_items}, make_ev_rejected={make_ev_rejected}'
          + (f', last_error={last_error!r}' if last_error else ''),
          file=sys.stderr)
    return events

# ── SCRAPERS ──────────────────────────────────────────────────────────────────

def scrape_downtown_reno():
    print('  Downtown Reno Partnership…', file=sys.stderr)
    evts = scrape_tribe('https://downtownreno.org', 'Downtown Reno Partnership',
                        'reno', 'Downtown Reno', 'Downtown Reno, NV')
    print(f'    → {len(evts)}', file=sys.stderr)
    return evts

def scrape_reno_scene():
    # DISABLED 2026-07-01: therenoscene.com is not running the Tribe events
    # plugin (confirmed 404 on /wp-json/tribe/events/v1/events — it's a
    # custom Elementor/WP-Rocket build with no public JSON API). Needs a
    # purpose-built HTML scraper, not the generic Tribe one. Skipping
    # cleanly instead of throwing an HTTP error every hourly run.
    return []

def scrape_holland():
    print('  Holland Project…', file=sys.stderr)
    evts = scrape_tribe('https://hollandreno.org', 'Holland Project',
                        'reno', 'The Holland Project', '140 Vesta St, Reno NV',
                        extra_tags=['all ages', 'indie', 'DIY'], max_pages=3)
    print(f'    → {len(evts)}', file=sys.stderr)
    return evts

def scrape_artown():
    # DISABLED 2026-07-01: renoisartown.com resets the connection on the
    # Tribe API request (likely bot/WAF protection, not a URL problem).
    # Skipping cleanly instead of eating a timeout every hourly run.
    return []

def scrape_visit_tahoe():
    print('  Visit Lake Tahoe…', file=sys.stderr)
    evts = scrape_tribe('https://visitlaketahoe.com', 'Visit Lake Tahoe',
                        'tahoe', 'Lake Tahoe', 'Lake Tahoe, CA/NV',
                        extra_tags=['Lake Tahoe'])
    print(f'    → {len(evts)}', file=sys.stderr)
    return evts

def scrape_thisisreno():
    # DISABLED 2026-07-01: thisisreno.com returns 403 Forbidden on the
    # Tribe API — bot-blocked. Skipping cleanly instead of erroring.
    return []

def scrape_gotahoenorth():
    # DISABLED 2026-07-01: gotahoenorth.com 404s on the Tribe API — site
    # has moved off the plugin or restructured. Needs manual re-check.
    return []

def scrape_southtahoenow():
    print('  South Tahoe Now…', file=sys.stderr)
    evts = scrape_tribe('https://www.southtahoenow.com', 'South Tahoe Now',
                        'tahoe', 'South Lake Tahoe', 'South Lake Tahoe, CA',
                        extra_tags=['South Lake Tahoe'])
    print(f'    → {len(evts)}', file=sys.stderr)
    return evts

def scrape_askreno():
    # DISABLED 2026-07-01: ask-reno.com 404s on the Tribe API — site has
    # moved off the plugin or restructured. Needs manual re-check.
    return []

def scrape_bruka():
    # DISABLED 2026-07-01: confirmed Brüka Theatre has migrated off
    # WordPress entirely — now on Squarespace with SimpleTix ticketing
    # (bruka.org/events + brukatheatre.simpletix.com). Needs a new
    # Squarespace-specific scraper, not the Tribe one.
    return []

def scrape_valhalla():
    # DISABLED 2026-07-01: valhallatahoe.com returns 403 Forbidden on the
    # Tribe API — bot-blocked. Skipping cleanly instead of erroring.
    return []

def scrape_skytavern():
    # DISABLED 2026-07-01: skytavern.org 404s on the Tribe API — site has
    # moved off the plugin or restructured. Needs manual re-check.
    return []

def scrape_live_lakeview():
    # DISABLED 2026-07-01: liveatlakeview.com 404s on the Tribe API — site
    # has moved off the plugin or restructured. Needs manual re-check.
    return []

def scrape_lateniteproductions():
    print('  Late Nite Productions…', file=sys.stderr)
    # Multi-city concert promoter (Reno, Tahoe, Truckee, Vacaville, Fresno, etc.)
    # Each event carries its own venue/address from the API, so we leave
    # default_venue/default_addr blank — make_ev()'s is_local() check strips
    # out any non Reno/Tahoe-area shows (Vacaville, Fresno, Grass Valley, etc.)
    evts = scrape_tribe('https://lateniteproductions.com', 'Late Nite Productions',
                        'reno', '', '',
                        extra_tags=['concert', 'live music'], max_pages=15)
    print(f'    → {len(evts)}', file=sys.stderr)
    return evts

# ── TIXR — generic scraper for any venue selling through Tixr ─────────────
# Tixr doesn't have a public unauthenticated API (their real API needs a
# private key + HMAC signature only the venue/organizer has). But every
# individual Tixr event page is server-rendered HTML with standard
# meta/OG tags (title, event:start_time, street-address, lat/long,
# tixr-eventId) baked into <head> — that's a much more stable target than
# guessing at page layout. Strategy: fetch the group's page, pull out
# links to individual event pages, then read the meta tags off each one.
# ── TIXR (via Playwright — a real browser is required, plain HTTP is 403'd) ──
# Confirmed: tixr.com blocks plain HTTP requests with bot detection (same as
# ra.co). A real browser session bypasses this. Runs all three known Tixr
# venues (Cypress, Glow Plaza, Crystal Bay Casino) through ONE shared browser
# instance for efficiency — launching Chromium is the expensive part (~1-2s),
# so sharing it across venues instead of relaunching per-venue cuts that
# cost by ~3x. Every navigation has an explicit timeout so nothing can hang
# the CI job indefinitely, and the whole thing is wrapped so a single
# venue failing (or Playwright/Chromium being unavailable at all) can never
# crash the rest of the scraper run.
TIXR_GROUPS = [
    ('cypressreno',       'Cypress Reno',            'reno',  'Cypress',
     'Midtown Reno, NV'),
    ('glowplaza',         "J Resort's Glow Plaza",   'reno',  "J Resort's Glow Plaza",
     '670 W 4th St, Reno NV'),
    ('crystalbaycasino',  'Crystal Bay Casino',      'tahoe', 'Crystal Bay Casino',
     '14 State Route 28, Crystal Bay NV'),
]
NAV_TIMEOUT_MS = 20000  # 20s hard cap per page load — never let CI hang

def _tixr_extract_meta(page_html, name):
    m = re.search(
        rf'<meta[^>]+(?:name|property)=["\']{re.escape(name)}["\'][^>]+content=["\']([^"\']*)["\']',
        page_html, re.I)
    return html.unescape(m.group(1)) if m else ''

def _tixr_scrape_one_event(page, group_slug, slug, src_name, region, default_venue, default_addr):
    ev_url = f'https://www.tixr.com/groups/{group_slug}/events/{slug}'
    try:
        page.goto(ev_url, timeout=NAV_TIMEOUT_MS, wait_until='domcontentloaded')
    except Exception as ex:
        print(f'    nav failed for {slug}: {ex}', file=sys.stderr)
        return None
    page_html = page.content()
    raw_title = _tixr_extract_meta(page_html, 'og:title') or _tixr_extract_meta(page_html, 'twitter:title')
    if not raw_title:
        return None
    venue_m = re.search(r'Tickets at (.+?) in \w', raw_title)
    venue = html.unescape(venue_m.group(1)).strip() if venue_m else default_venue
    title = re.sub(r'\s*Tickets at.*$', '', raw_title).strip() or raw_title
    start_raw = _tixr_extract_meta(page_html, 'event:start_time')
    d = parse_date(start_raw)
    if not title or not d:
        return None
    addr_street = _tixr_extract_meta(page_html, 'og:street-address')
    addr_city   = _tixr_extract_meta(page_html, 'og:locality')
    addr_state  = _tixr_extract_meta(page_html, 'og:region')
    addr = ', '.join(filter(None, [addr_street, addr_city, addr_state])) or default_addr
    desc = _tixr_extract_meta(page_html, 'og:description') or _tixr_extract_meta(page_html, 'description')
    keywords = [k.strip() for k in _tixr_extract_meta(page_html, 'keywords').split(',') if k.strip()][:5]
    ev_id = _tixr_extract_meta(page_html, 'tixr-eventId') or slug
    return make_ev(scrape_id('tixr', ev_id), title, guess_cat(title, desc), d,
                   region, venue, addr, to_12h(start_raw), None, False,
                   desc, keywords, ev_url, src_name)

def _tixr_scrape_one_group(browser, group_slug, src_name, region, default_venue, default_addr):
    events = []
    page = browser.new_page()
    page.set_default_timeout(NAV_TIMEOUT_MS)
    try:
        group_url = f'https://www.tixr.com/groups/{group_slug}'
        try:
            page.goto(group_url, timeout=NAV_TIMEOUT_MS, wait_until='networkidle')
        except Exception as ex:
            print(f'    group page nav failed ({group_slug}): {ex}', file=sys.stderr)
            return []
        page_html = page.content()
        # Diagnostics — so a zero-result run tells us WHY, not just "0"
        html_len = len(page_html)
        challenge_markers = ['Just a moment', 'cf-browser-verification',
                              'Checking your browser', 'Attention Required',
                              'captcha', 'cf-challenge']
        hit_challenge = [m for m in challenge_markers if m.lower() in page_html.lower()]
        if hit_challenge:
            print(f'    ⚠ {group_slug}: possible bot-challenge page detected '
                  f'(markers: {hit_challenge}), html_len={html_len}', file=sys.stderr)
        # Primary method: query real DOM anchor elements (most reliable —
        # doesn't depend on guessing how the page's JS framework formats
        # raw HTML, works regardless of rendering quirks)
        try:
            hrefs = page.eval_on_selector_all(
                'a[href*="/events/"]', 'els => els.map(e => e.getAttribute("href"))')
        except Exception as ex:
            print(f'    DOM query failed ({group_slug}): {ex}', file=sys.stderr)
            hrefs = []
        slugs = set()
        for href in (hrefs or []):
            m = re.search(rf'/groups/{re.escape(group_slug)}/events/([a-z0-9-]+)', href or '', re.I)
            if m: slugs.add(m.group(1))
        # Fallback: regex on raw page HTML, in case links exist as plain
        # text/data attributes rather than real <a href> DOM elements
        if not slugs:
            slugs = set(re.findall(
                rf'/groups/{re.escape(group_slug)}/events/([a-z0-9-]+)', page_html, re.I))
        slugs = sorted(slugs)
        no_shows = 'no upcoming shows' in page_html.lower()
        print(f'    {group_slug}: html_len={html_len}, dom_links={len(hrefs or [])}, '
              f'slugs_found={len(slugs)}, "no upcoming shows" text={no_shows}', file=sys.stderr)
        for slug in slugs[:100]:
            try:
                ev = _tixr_scrape_one_event(page, group_slug, slug, src_name,
                                             region, default_venue, default_addr)
                if ev: events.append(ev)
            except Exception as ex:
                # One bad event page should never take down the whole group
                print(f'    event scrape failed ({slug}): {ex}', file=sys.stderr)
                continue
    finally:
        page.close()
    return events

def scrape_tixr_playwright():
    # DISABLED 2026-07-01: CONFIRMED via diagnostic run — tixr.com serves a
    # genuine bot-challenge/CAPTCHA page (html_len~1480, "captcha" marker
    # present) to the automated browser instead of real content, on all
    # three venues. This is deliberate, active anti-bot protection, not a
    # code bug. Not attempting to defeat it — that crosses from scraping a
    # public page into circumventing a site's active security measures,
    # which isn't something to build regardless of the reason. Tixr data
    # (Cypress, Glow Plaza, Crystal Bay Casino) isn't gettable through
    # automated scraping. Real alternative: check those venues' Tixr pages
    # manually/periodically, or see if any of them will share a direct
    # iCal/RSS feed on request.
    return []

# ── HTML SCRAPERS for venues without Tribe/WordPress APIs ─────────────────────

def scrape_html_events(url, src_name, region, venue, addr,
                       title_pattern, date_pattern, link_pattern=None,
                       extra_tags=None, cat_override=None):
    """Generic HTML scraper using regex patterns."""
    raw = get(url)
    if not raw:
        print(f'    [{src_name}] no response from {url}', file=sys.stderr)
        return []
    events = []
    prefix = re.sub(r'[^a-z]', '', src_name.lower())[:6]
    # Find all blocks containing both title and date
    # Split on likely event boundaries
    blocks = re.split(r'(?=<(?:article|div|li)[^>]*(?:event|show|listing)[^>]*>)', raw)
    seen = set()
    no_title_or_date = 0
    dup_skipped = 0
    make_ev_rejected = 0
    for block in blocks[:60]:
        t_m = re.search(title_pattern, block, re.DOTALL | re.I)
        d_m = re.search(date_pattern,  block, re.DOTALL | re.I)
        if not t_m or not d_m:
            no_title_or_date += 1
            continue
        title = clean(t_m.group(1))
        d     = parse_date(d_m.group(1))
        if not title or not d:
            no_title_or_date += 1
            continue
        key = title[:40] + d
        if key in seen:
            dup_skipped += 1
            continue
        seen.add(key)
        link = url
        if link_pattern:
            l_m = re.search(link_pattern, block, re.I)
            if l_m: link = l_m.group(1)
        ev = make_ev(
            scrape_id(prefix, title + d),
            title, cat_override or guess_cat(title), d, region,
            venue, addr, None, None, False,
            f'{title} at {venue}.',
            extra_tags or [], link, src_name)
        if ev: events.append(ev)
        else: make_ev_rejected += 1
    print(f'    [{src_name}] html_len={len(raw)}, blocks_found={len(blocks)}, '
          f'no_title_or_date={no_title_or_date}, dup_skipped={dup_skipped}, '
          f'make_ev_rejected={make_ev_rejected}', file=sys.stderr)
    return events

def scrape_cargo():
    # DISABLED 2026-07-01: two attempts failed. First fix (correcting the
    # domain from dead cargoconcerthall.com to real cargoreno.com) was
    # right, but the second fix — a regex looking for "####" markdown-style
    # headers — was wrong. That markdown formatting is an artifact of how
    # my own fetch tool renders pages for me, NOT what the real HTML looks
    # like, so the pattern never matched anything (confirmed: ran clean,
    # zero errors, zero events). Rather than guess a third time, disabling
    # this until it can be tested against the real raw HTML directly.
    # Cargo's bigger shows (Madeon, GWAR, etc.) also tend to appear on
    # Ticketmaster, so this isn't a total blackout — smaller/local shows
    # are what's missing.
    return []

def scrape_alpine():
    # NOTE 2026-07-01: thealpine-reno.com no longer runs the Tribe plugin —
    # confirmed it's now on the "Classic Venue" WP theme with TicketWeb
    # ticketing. The HTML fallback below rarely matches (titles aren't in
    # h2-h4 tags), but it's harmless — no crash, just returns 0. Rebuilding
    # this properly would need a TicketWeb-specific parser (unverified raw
    # HTML structure — not doing it blind). Alpine shows also frequently
    # appear on Ticketmaster/Songkick, so coverage isn't fully lost.
    print('  The Alpine…', file=sys.stderr)
    evts = scrape_tribe('https://www.thealpine-reno.com', 'The Alpine',
                        'reno', 'The Alpine', '324 E 4th St, Reno NV',
                        extra_tags=['The Alpine', '4th Street', 'Reno'])
    if not evts:
        raw = get('https://www.thealpine-reno.com/events/')
        if raw:
            evts = scrape_html_events(
                'https://www.thealpine-reno.com/events/',
                'The Alpine', 'reno',
                'The Alpine', '324 E 4th St, Reno NV',
                r'<h[2-4][^>]*>(.*?)</h[2-4]>',
                r'(\w+ \d{1,2},?\s*\d{4}|\d{4}-\d{2}-\d{2})',
                extra_tags=['The Alpine', 'Reno'])
    print(f'    → {len(evts)}', file=sys.stderr)
    return evts

def scrape_nugget():
    # DISABLED 2026-07-01: cnty.com returns 403 Forbidden — bot-blocked.
    # Skipping cleanly instead of erroring every hourly run.
    return []

def scrape_atlantis():
    # FIXED 2026-07-01: site is real and alive with 20+ events, confirmed
    # via direct fetch. Old regex assumed a div/article wrapper with
    # "event" in its class name — never verified, never matched. Rewritten
    # to anchor on the confirmed URL pattern for every event
    # (/more/events/{category}/{slug}) plus the "Month Day, Year" date
    # that reliably follows it — same category name always follows the
    # slash, so this doesn't depend on guessing div/heading structure.
    print('  Atlantis Casino…', file=sys.stderr)
    raw = get('https://atlantiscasino.com/more/events')
    if not raw:
        print('    → 0', file=sys.stderr)
        return []
    events = []
    seen = set()
    total_slugs = 0
    no_title = 0
    no_date = 0
    for m in re.finditer(
        r'/more/events/(casino-events|dining-events|music-events|spa-events)/([a-z0-9-]+)',
        raw, re.I):
        slug = m.group(2)
        if slug in seen or slug.endswith('-c'): continue  # '-c' suffix = duplicate calendar-grid variant of same event
        seen.add(slug)
        total_slugs += 1
        # The event's own detail URL appears twice per listing: once as the
        # title link, once as a "View Details"/"Learn More" CTA link. Both
        # point to the same href, so collect every <a href="...same slug...">
        # innerText</a> and keep whichever isn't a generic CTA label.
        href_frag = f'{m.group(1)}/{slug}'
        anchor_texts = re.findall(
            rf'<a[^>]+href="[^"]*{re.escape(href_frag)}"[^>]*>([^<]{{2,90}})</a>',
            raw, re.I)
        title = ''
        for t in anchor_texts:
            t_clean = clean(t)
            if t_clean and t_clean.lower() not in ('learn more','view details','buy tickets','details'):
                title = t_clean
                break
        if not title:
            no_title += 1
            continue
        # DIAGNOSED 2026-07-01 from live run: 300 chars was too narrow — real
        # HTML has far more attribute/class/icon markup between elements
        # than the cleaned text suggested. Widened based on evidence, not
        # a guess. Kept forward-only (not backward) since the date was
        # confirmed to appear AFTER the title link, and searching backward
        # risks grabbing a neighboring event's date instead of this one's.
        window = raw[m.end():m.end()+800]
        d_m = re.search(
            r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s*\d{4}',
            window)
        if not d_m:
            no_date += 1
            continue
        d = parse_date(d_m.group(0))
        if not d:
            no_date += 1
            continue
        link = f'https://atlantiscasino.com/more/events/{href_frag}'
        ev = make_ev(scrape_id('atl2', slug), title,
                     guess_cat(title), d, 'reno',
                     'Atlantis Casino Resort Spa', '3800 S Virginia St, Reno NV',
                     None, None, False, f'{title} at Atlantis Casino Resort Spa.',
                     ['Atlantis', 'Reno', 'casino'], link, 'Atlantis Casino')
        if ev: events.append(ev)
    print(f'    html_len={len(raw)}, unique_slugs_found={total_slugs}, '
          f'failed_no_title={no_title}, failed_no_date={no_date}', file=sys.stderr)
    print(f'    → {len(events)}', file=sys.stderr)
    return events

def scrape_peppermill():
    # DISABLED 2026-07-01: old URL (/entertainment/) was wrong — real page
    # is /resort/event-list/. But confirmed that page doesn't server-render
    # events directly; it loads them via a client-side AJAX call to
    # /library/api/related-events-embed.php?ACTION=RELATED_EVENTS&PAGE_ID=380.
    # That endpoint might return clean JSON, but I can't verify its actual
    # response format without live-testing it — not guessing a third format
    # blind after the Cargo lesson. Worth a manual test of that URL if you
    # want to revisit this.
    return []

def scrape_gsr():
    print('  Grand Sierra Resort…', file=sys.stderr)
    # GSR uses Ticketmaster — covered by TM scraper
    # Here we also try their structured events page
    raw = get('https://www.grandsierraresort.com/entertainment/concerts-and-shows')
    if not raw:
        print('    → 0', file=sys.stderr)
        return []
    events = []
    seen = set()
    # Look for structured JSON-LD data first (most reliable)
    json_ld = re.findall(r'<script type="application/ld\+json">(.*?)</script>', raw, re.DOTALL)
    for block in json_ld:
        try:
            data = json.loads(block)
            if isinstance(data, list): items = data
            elif isinstance(data, dict): items = [data]
            else: continue
            for item in items:
                if item.get('@type') not in ('Event', 'MusicEvent', 'TheaterEvent'): continue
                title = item.get('name','').strip()
                start = item.get('startDate','')
                d = parse_date(start)
                if not d or not title or title in seen: continue
                seen.add(title)
                loc = item.get('location',{})
                venue = loc.get('name','Grand Theatre – Grand Sierra Resort')
                addr  = loc.get('address','2500 E 2nd St, Reno')
                if isinstance(addr, dict):
                    addr = ', '.join(filter(None,[addr.get('streetAddress',''), addr.get('addressLocality',''), addr.get('addressRegion','')]))
                offers = item.get('offers',{})
                price = None
                if isinstance(offers, dict):
                    lo = offers.get('lowPrice')
                    hi = offers.get('highPrice')
                    if lo and hi: price = f'${float(lo):.0f}–${float(hi):.0f}'
                    elif lo: price = f'${float(lo):.0f}'
                url = item.get('url', item.get('@id', 'https://www.grandsierraresort.com/entertainment'))
                ev = make_ev(scrape_id('gsr2', title+d), title,
                             guess_cat(title), d, 'reno',
                             venue, addr, to_12h(start),
                             price, False, item.get('description','')[:300],
                             ['Grand Sierra','Reno'], url, 'Grand Sierra Resort')
                if ev: events.append(ev)
        except: continue
    # Fallback to HTML parsing if JSON-LD found nothing (confirmed via direct
    # fetch: GSR is a HubSpot site with NO JSON-LD event schema — this is the
    # real path that actually has data. Each event is one big <a> card whose
    # inner text reads like:
    # "Tickets & MoreSaturday, Jun 27Stavros HalkiasGrand Theatre | Doors @ 7:00 PMTickets & More"
    # anchored on the confirmed href pattern /entertainment/concerts-and-shows/{slug}
    if not events:
        raw = get('https://www.grandsierraresort.com/entertainment/concerts-and-shows')
        if not raw:
            print('    → 0', file=sys.stderr)
            return []
        events = []
        seen = set()
        # DIAGNOSED 2026-07-01 from live run: cards_found=0 with the absolute
        # URL requirement — real HTML almost certainly uses relative hrefs
        # (href="/entertainment/concerts-and-shows/slug"), not the full
        # https://www.grandsierraresort.com/... prefix. Made the domain
        # prefix optional so it matches either form.
        card_re = re.compile(
            r'<a[^>]+href="(?:https://www\.grandsierraresort\.com)?(/entertainment/concerts-and-shows/([a-z0-9-]+))"[^>]*>(.*?)</a>',
            re.DOTALL | re.I)
        cards_found = card_re.findall(raw)
        no_pattern_match = 0
        for href, slug, inner in cards_found:
            if slug in seen: continue
            inner_text = clean(re.sub(r'<[^>]+>', ' ', inner))
            m = re.search(
                r'(Sun|Mon|Tue|Wed|Thu|Fri|Sat)\w*,?\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s+(\d{1,2})(.*?)'
                r'(Grand Theatre|Outdoor Stage)\s*\|?\s*Doors?\s*@\s*(\d{1,2}:\d{2}\s*[AP]M)',
                inner_text, re.I)
            if not m:
                no_pattern_match += 1
                continue
            seen.add(slug)
            mon, day = m.group(2), m.group(3)
            title = clean(m.group(4))
            venue = m.group(5)
            doors = m.group(6)
            if not title or len(title) < 2: continue
            this_year = date.today().year
            d = parse_date(f'{mon} {day} {this_year}')
            if d and d < TODAY:
                d = parse_date(f'{mon} {day} {this_year + 1}')
            if not d: continue
            full_url = f'https://www.grandsierraresort.com{href}' if href.startswith('/') else href
            ev = make_ev(scrape_id('gsr2', slug), title,
                         guess_cat(title), d, 'reno',
                         f'{venue} – Grand Sierra Resort', '2500 E 2nd St, Reno NV',
                         doors, None, False, f'{title} at Grand Sierra Resort {venue}.',
                         ['Grand Sierra','Reno'], full_url, 'Grand Sierra Resort')
            if ev: events.append(ev)
        sample_text = clean(re.sub(r'<[^>]+>', ' ', cards_found[0][2]))[:150] if cards_found else '(no cards matched at all)'
        print(f'    html_len={len(raw)}, cards_found={len(cards_found)}, '
              f'pattern_mismatch={no_pattern_match}, sample_inner_text={sample_text!r}',
              file=sys.stderr)
        print(f'    → {len(events)}', file=sys.stderr)
        return events
    print(f'    → {len(events)}', file=sys.stderr)
    return events


def scrape_pioneer():
    # DISABLED 2026-07-01: pioneercenter.com 404s (site restructured, not
    # Tribe-based). CONFIRMED not a real coverage gap: Downtown Reno
    # Partnership (the 'drp' scraper, already returning ~76 events) lists
    # Pioneer Center as one of its tracked venues and pulls its shows
    # (Hell's Kitchen, Reno Phil concerts, etc.) directly. Building a
    # dedicated Pioneer Center scraper would just duplicate that.
    return []

def scrape_crystal_bay():
    # REPLACED 2026-07-01: this used to scrape crystalbaycasino.com HTML
    # directly (unreliable, was returning 0). Confirmed (via Matt's friend
    # who works there) that CBC does ALL ticketing through Tixr. Crystal
    # Bay is now scraped as part of the consolidated 'tixr' source
    # (scrape_tixr_playwright(), covers Cypress + Glow Plaza + Crystal Bay
    # in one browser session) — returning [] here so this key doesn't
    # double-scrape the same venue through two different paths.
    return []

def scrape_bba():
    # FIXED 2026-07-01: confirmed via direct fetch — bigblueadventure.com
    # runs the Tribe/WordPress events plugin (meta-tec-api-origin +
    # webcal ical subscription links both confirm it). The old scraper
    # never used the proven scrape_tribe() helper at all — it hit a
    # generic regex against the wrong page. Switched to the same reliable
    # method already working for Holland Project, Late Nite Productions, etc.
    print('  Big Blue Adventure…', file=sys.stderr)
    evts = scrape_tribe('https://bigblueadventure.com', 'Big Blue Adventure',
                        'tahoe', 'Lake Tahoe / Truckee', 'North Lake Tahoe, CA',
                        extra_tags=['Big Blue Adventure', 'Lake Tahoe', 'outdoor', 'endurance'])
    # Override category per-event based on title keywords (triathlon/swim/etc)
    # instead of the generic category scrape_tribe would guess
    for ev in evts:
        t = ev['title'].lower()
        ev['cat'] = ('triathlon' if 'tri' in t else
                     'swim' if 'swim' in t else
                     'running' if any(w in t for w in ['run','marathon','5k','10k']) else
                     'mtb' if 'bike' in t or 'gravel' in t else 'outdoor')
    print(f'    → {len(evts)}', file=sys.stderr)
    return evts

def scrape_bartley_ranch():
    # DISABLED 2026-07-01: URL was wrong (facilities/bartley_ranch.php
    # doesn't exist) AND washoecounty.gov is a government CMS, not
    # WordPress — the Tribe API call was never going to work either.
    # Correct current page is washoecounty.gov/parks/parks/park_programs.php
    # but that's a different platform requiring its own scraper — not
    # doing it blind without seeing the raw HTML. Bartley Ranch's actual
    # events (Evenings on the Ranch, Living History Day) are seasonal and
    # low-volume — lower priority to rebuild.
    return []

def scrape_reno_aces():
    # DISABLED 2026-07-01: milb.com is a heavy client-rendered React app —
    # the game data isn't in the raw server HTML at all (this regex was
    # matching against nothing that exists in a plain HTTP fetch).
    # Redundant anyway: scrape_reno_aces_v2() below uses the real official
    # MLB Stats API directly, which is the correct approach.
    return []

# ── RESIDENT ADVISOR (GraphQL) ────────────────────────────────────────────────

def scrape_ra():
    # DISABLED 2026-07-01: ra.co now runs bot detection (Cloudflare) that
    # blocks non-browser requests entirely — confirmed by direct test.
    # This is new since the scraper was built; the GraphQL endpoint used
    # to accept plain header-spoofed requests but no longer does. That's
    # why this was silently returning 0 with no visible error before —
    # the POST was "succeeding" against a challenge response, not real
    # data. Skipping cleanly. Re-enabling would require a real browser
    # session (Playwright), not a plain HTTP request.
    return []

# ── EVENTBRITE (public search) ────────────────────────────────────────────────

def scrape_eventbrite():
    # DISABLED 2026-07-01: Eventbrite's public "destination search" endpoint
    # returns 405 Method Not Allowed on every call — they've deprecated
    # unauthenticated public search. Would need an official Eventbrite API
    # key + their newer authenticated API to bring this back.
    return []

# ── TICKETMASTER Discovery API ────────────────────────────────────────────────

def scrape_ticketmaster():
    api_key = os.environ.get('TM_API_KEY', '')
    if not api_key:
        print('  Ticketmaster: TM_API_KEY not set, skipping', file=sys.stderr)
        return []
    print('  Ticketmaster…', file=sys.stderr)
    events = []
    configs = [
        ('39.5296,-119.8138', '50', 'reno'),
        ('38.9399,-119.9772', '30', 'tahoe'),
    ]
    total_items_seen = 0
    no_title_or_date = 0
    non_local_skipped = 0
    make_ev_rejected = 0
    total_pages_fetched = 0
    for latlong, radius, region in configs:
        url = (f'https://app.ticketmaster.com/discovery/v2/events.json'
               f'?apikey={api_key}&latlong={latlong}&radius={radius}&unit=miles'
               f'&startDateTime={TODAY}T00:00:00Z&endDateTime={UNTIL}T23:59:59Z'
               f'&size=100&sort=date,asc&locale=en-us')
        tm_page = 0
        while True:
            paged_url = url + f'&page={tm_page}'
            raw = get(paged_url)
            if not raw: break
            total_pages_fetched += 1
            try: data = json.loads(raw)
            except: break
            page_info = data.get('page', {})
            total_pages = page_info.get('totalPages', 1)
            items = (data.get('_embedded',{}).get('events') or [])
            total_items_seen += len(items)
            for item in items:
                title = (item.get('name') or '').strip()
                d     = (item.get('dates',{}).get('start',{}).get('localDate',''))
                if not title or not d:
                    no_title_or_date += 1
                    continue
                venues  = (item.get('_embedded',{}).get('venues') or [{}])
                vd      = venues[0]
                venue   = (vd.get('name') or '').strip()
                city    = ((vd.get('city') or {}).get('name',''))
                state   = ((vd.get('state') or {}).get('stateCode',''))
                addr_st = ((vd.get('address') or {}).get('line1',''))
                addr    = ', '.join(filter(None,[addr_st, city, state]))
                if not is_local(f'{title} {venue} {city}'):
                    non_local_skipped += 1
                    continue
                pr      = (item.get('priceRanges') or [{}])[0]
                lo, hi  = pr.get('min'), pr.get('max')
                price   = (f'${lo:.0f}–${hi:.0f}' if lo and hi else
                           f'${lo:.0f}' if lo else None)
                ev = make_ev(scrape_id('tm', item.get('id', title+d)),
                    title, guess_cat(title), d, region, venue, addr,
                    (item.get('dates',{}).get('start',{}).get('localTime','') or None),
                    price, False, '', [],
                    item.get('url','https://www.ticketmaster.com/'), 'Ticketmaster')
                if ev: events.append(ev)
                else: make_ev_rejected += 1
            if tm_page >= total_pages - 1: break
            tm_page += 1
            time.sleep(0.5)
    print(f'    pages_fetched={total_pages_fetched}, items_seen={total_items_seen}, '
          f'no_title_or_date={no_title_or_date}, non_local_skipped={non_local_skipped}, '
          f'make_ev_rejected={make_ev_rejected}', file=sys.stderr)
    print(f'    → {len(events)}', file=sys.stderr)
    return events


# ── SONGKICK — Reno/Tahoe metro area ──────────────────────────────────────
def scrape_songkick():
    # Songkick's metro calendar endpoint returns 401 without a real API key —
    # the old code used a placeholder string ('not-required-for-basic') that
    # never actually worked. Reads SONGKICK_API_KEY from GitHub Actions
    # secrets, same pattern as TM_API_KEY. Skips cleanly if not set.
    api_key = os.environ.get('SONGKICK_API_KEY', '')
    if not api_key:
        print('  Songkick: SONGKICK_API_KEY not set, skipping', file=sys.stderr)
        return []
    print('  Songkick…', file=sys.stderr)
    # Songkick metro ID for Reno: 13455
    # Lake Tahoe area is covered under Reno metro
    events = []
    total_items_seen = 0
    no_title_or_date = 0
    non_local_skipped = 0
    make_ev_rejected = 0
    for metro_id, region in [('13455','reno'), ('24843','tahoe')]:
        page = 1
        while page <= 10:
            url = (f'https://api.songkick.com/api/3.0/metro_areas/{metro_id}/calendar.json'
                   f'?apikey={api_key}&min_date={TODAY}&max_date={UNTIL}'
                   f'&per_page=50&page={page}')
            # Songkick doesn't require API key for basic metro calendar
            raw = get(url)
            if not raw or raw.startswith('ERROR'): break
            try: data = json.loads(raw)
            except: break
            results = data.get('resultsPage',{})
            items = results.get('results',{}).get('event',[])
            total = results.get('totalEntries', 0)
            if not items: break
            total_items_seen += len(items)
            for item in items:
                title = item.get('displayName','').strip()
                d = parse_date(item.get('start',{}).get('date',''))
                if not title or not d:
                    no_title_or_date += 1
                    continue
                venue_d = item.get('venue',{})
                venue = venue_d.get('displayName','')
                city  = (venue_d.get('metroArea',{}).get('displayName',''))
                if not is_local(f'{title} {venue} {city}'):
                    non_local_skipped += 1
                    continue
                perf = item.get('performance',[])
                artists = [p.get('displayName','') for p in perf if p.get('displayName')]
                display = f'{venue} – {", ".join(artists)}' if artists else title
                ev = make_ev(
                    scrape_id('sk', str(item.get('id','')) + d),
                    display, guess_cat(title), d, region,
                    venue, city,
                    item.get('start',{}).get('time'),
                    None, False,
                    f'{", ".join(artists)} live at {venue}.' if artists else title,
                    artists[:4],
                    item.get('uri','https://www.songkick.com/'),
                    'Songkick'
                )
                if ev: events.append(ev)
                else: make_ev_rejected += 1
            if len(items) < 50 or page * 50 >= total: break
            page += 1
            time.sleep(0.5)
        time.sleep(1)
    print(f'    items_seen={total_items_seen}, no_title_or_date={no_title_or_date}, '
          f'non_local_skipped={non_local_skipped}, make_ev_rejected={make_ev_rejected}',
          file=sys.stderr)
    print(f'    → {len(events)}', file=sys.stderr)
    return events


# ── TICKETMASTER VENUE-SPECIFIC LOOKUPS ───────────────────────────────────
# These pull ALL future events for specific major venues by their TM venue ID
# This is more reliable than geo search for getting 2027+ shows
def scrape_tm_venues():
    api_key = os.environ.get('TM_API_KEY', '')
    if not api_key:
        return []
    print('  Ticketmaster (venue-specific)…', file=sys.stderr)

    # Ticketmaster venue IDs for Reno/Tahoe major venues
    TM_VENUES = [
        ('KovZpZA6AAEA', 'Grand Sierra Resort – Grand Theatre', '2500 E 2nd St, Reno', 'reno'),
        ('KovZpZA6knlA', 'Reno Events Center', '400 N Center St, Reno', 'reno'),
        ('KovZpZA6AAAA', 'Nugget Casino Resort', '1100 Nugget Ave, Sparks NV', 'reno'),
        ('KovZpZA6AAJA', 'Pioneer Center for the Performing Arts', '100 S Virginia St, Reno', 'reno'),
        ('KovZpZAa6e1A', 'Silver Legacy Casino', '407 N Virginia St, Reno', 'reno'),
        ('KovZpZAEAl6A', 'Cargo Concert Hall – Whitney Peak Hotel', '255 N Virginia St, Reno', 'reno'),
        ('KovZpZA6kkAA', 'Crystal Bay Casino – Crown Room', '14 NV-28, Crystal Bay NV', 'tahoe'),
        ('KovZpZA6AekA', "Harrah's/Harveys Lake Tahoe", 'Highway 50, Stateline NV', 'tahoe'),
    ]

    events = []
    no_title_or_date = 0
    make_ev_rejected = 0
    venue_counts = []
    for venue_id, venue_name, venue_addr, region in TM_VENUES:
        before = len(events)
        page = 0
        while True:
            url = (f'https://app.ticketmaster.com/discovery/v2/events.json'
                   f'?apikey={api_key}&venueId={venue_id}'
                   f'&startDateTime={TODAY}T00:00:00Z&endDateTime={UNTIL}T23:59:59Z'
                   f'&size=100&sort=date,asc&locale=en-us&page={page}')
            raw = get(url)
            if not raw or raw.startswith('ERROR'): break
            try: data = json.loads(raw)
            except: break
            page_info = data.get('page',{})
            total_pages = page_info.get('totalPages', 1)
            items = data.get('_embedded',{}).get('events') or []
            for item in items:
                title = (item.get('name') or '').strip()
                d     = item.get('dates',{}).get('start',{}).get('localDate','')
                if not title or not d:
                    no_title_or_date += 1
                    continue
                pr    = (item.get('priceRanges') or [{}])[0]
                lo,hi = pr.get('min'), pr.get('max')
                price = (f'${lo:.0f}–${hi:.0f}' if lo and hi else
                         f'${lo:.0f}' if lo else None)
                ev = make_ev(
                    scrape_id('tmv', venue_id + item.get('id','') + d),
                    title, guess_cat(title), d, region,
                    venue_name, venue_addr,
                    item.get('dates',{}).get('start',{}).get('localTime'),
                    price, False, '',
                    [],
                    item.get('url','https://www.ticketmaster.com/'),
                    'Ticketmaster'
                )
                if ev: events.append(ev)
                else: make_ev_rejected += 1
            if page >= total_pages - 1: break
            page += 1
            time.sleep(0.3)
        time.sleep(0.5)
        venue_counts.append(f'{venue_name}={len(events)-before}')
    print(f'    per_venue: {", ".join(venue_counts)}', file=sys.stderr)
    print(f'    no_title_or_date={no_title_or_date}, make_ev_rejected={make_ev_rejected}',
          file=sys.stderr)
    print(f'    → {len(events)}', file=sys.stderr)
    return events


# ── NEVADA MUSEUM OF ART ──────────────────────────────────────────────────
def scrape_nma():
    # DISABLED 2026-07-01: nevadaart.org 404s — confirmed the museum uses
    # Blackbaud Altru for its calendar (nevadaart.org/calendar/), not
    # WordPress/Tribe at all, so this was never going to work. CONFIRMED
    # not a real coverage gap either: Downtown Reno Partnership ('drp')
    # already lists Nevada Museum of Art as a tracked venue.
    return []


# ── RENO PHILHARMONIC ─────────────────────────────────────────────────────
def scrape_reno_phil():
    # DISABLED 2026-07-01: renophilharmonic.com is gone entirely — the
    # org's site moved to renophil.com. But their ticket calendar
    # (renophil.my.salesforce-sites.com) requires JavaScript (Salesforce
    # Commerce), so even the correct domain isn't plain-HTTP scrapable.
    # Reno Phil performs almost exclusively at Pioneer Center, which is
    # already tracked through Downtown Reno Partnership ('drp'), so this
    # isn't a full coverage gap.
    return []


# ── WASHOE COUNTY PARKS (Bartley Ranch, Bowers Mansion etc) ──────────────
def scrape_washoe_parks():
    # DISABLED 2026-07-01: washoecounty.gov 404s on both the Tribe API and
    # /events/ — confirmed the county's site is a government CMS
    # (.php-based pages like /parks/calendar.php), not WordPress at all.
    # Correct current page found: washoecounty.gov/parks/calendar.php —
    # but building a scraper for an unfamiliar govt CMS without seeing its
    # raw HTML risks a bad guess. Not doing it blind. County park events
    # (concerts, campfire programs) are lower-volume/seasonal — lower
    # priority to rebuild than the concert venues.
    return []


# ── TAHOE BLUE EVENT CENTER ───────────────────────────────────────────────
def scrape_tahoe_blue():
    # DISABLED 2026-07-01: not Tribe-based (custom OVG360 ticketing
    # platform, confirmed via search — not verifiable raw HTML structure
    # without live testing). Not a real coverage gap though — confirmed
    # via Ticketmaster search that Tahoe Blue's full roster (Tahoe Knight
    # Monsters games, Nate Bargatze, Gene Simmons, Yellowcard, etc.) is
    # already showing up through the working Ticketmaster scraper.
    return []


# ── RENO ACES (MiLB proper schedule API) ─────────────────────────────────
def scrape_reno_aces_v2():
    print('  Reno Aces (schedule)…', file=sys.stderr)
    events = []
    # LIKELY ROOT CAUSE of the 0-result bug, fixed 2026-07-01: the hardcoded
    # teamId=2476 may not actually be Reno's ID. If it were wrong, the API
    # call would still succeed (valid JSON for some OTHER team), and the
    # 'Reno' in home/away name filter below would silently match nothing —
    # exactly the symptom we saw (0 results, no error). Rather than trust
    # an unverified number, look up the real ID at runtime from MLB's own
    # team list and cache it, so this is self-correcting instead of a guess.
    team_id = None
    teams_raw = get('https://statsapi.mlb.com/api/v1/teams?sportId=11')
    if teams_raw and not teams_raw.startswith('ERROR'):
        try:
            teams_data = json.loads(teams_raw)
            for t in teams_data.get('teams', []):
                name = t.get('name', '')
                if 'Reno' in name or 'Aces' in name:
                    team_id = t.get('id')
                    break
        except: pass
    if not team_id:
        print('    could not find Reno Aces team ID via API lookup, skipping', file=sys.stderr)
        return []
    print(f'    resolved team_id={team_id}', file=sys.stderr)
    year = date.today().year
    total_games_seen = 0
    away_games_skipped = 0
    make_ev_rejected = 0
    for y in [year, year+1]:
        url = f'https://statsapi.mlb.com/api/v1/schedule?sportId=11&teamId={team_id}&season={y}&gameType=R&hydrate=venue,team'
        raw = get(url)
        if not raw or raw.startswith('ERROR'):
            print(f'    season {y}: no response', file=sys.stderr)
            continue
        try: data = json.loads(raw)
        except:
            print(f'    season {y}: JSON parse failed', file=sys.stderr)
            continue
        season_games = 0
        for date_entry in data.get('dates',[]):
            d = date_entry.get('date','')
            if not d or d < TODAY or d > UNTIL: continue
            for game in date_entry.get('games',[]):
                season_games += 1
                teams = game.get('teams',{})
                home = teams.get('home',{}).get('team',{}).get('name','')
                away = teams.get('away',{}).get('team',{}).get('name','')
                if 'Reno' not in home and 'Reno' not in away: continue
                is_home = 'Reno' in home
                opponent = away if is_home else home
                title = f'Reno Aces vs {opponent}' if is_home else f'Reno Aces @ {opponent}'
                if not is_home:
                    away_games_skipped += 1
                    continue  # only show home games
                game_time = game.get('gameDate','')
                ev = make_ev(
                    scrape_id('aces3', d + opponent),
                    title, 'sports', d, 'reno',
                    'Greater Nevada Field', '250 Evans Ave, Reno',
                    to_12h(game_time), '$9–$38', False,
                    f'Reno Aces AAA baseball vs {opponent}. Home game at Greater Nevada Field.',
                    ['baseball','AAA','Reno Aces','family','sports'],
                    'https://www.milb.com/reno/schedule', 'Reno Aces / MiLB'
                )
                if ev: events.append(ev)
                else: make_ev_rejected += 1
        total_games_seen += season_games
        print(f'    season {y}: {season_games} games in window', file=sys.stderr)
        time.sleep(0.5)
    print(f'    total_games_seen={total_games_seen}, away_games_skipped={away_games_skipped}, '
          f'make_ev_rejected={make_ev_rejected}', file=sys.stderr)
    print(f'    → {len(events)}', file=sys.stderr)
    return events

# ── DEDUPLICATION ─────────────────────────────────────────────────────────────

def norm_title(title):
    """
    Normalize a title for fuzzy deduplication:
    - lowercase
    - remove all punctuation, special chars, extra words
    - strip common suffixes that sources add (ticket deals, VIP, hotel, etc.)
    - strip common prefixes (venue names prepended by scrapers)
    """
    t = title.lower()
    # Remove common scraper-added prefixes (venue – artist)
    # e.g. "Grand Sierra Resort – Eric Church" -> "eric church"
    if ' – ' in t:
        t = t.split(' – ')[-1]
    if ' - ' in t:
        t = t.split(' - ')[-1]
    # Remove common noise suffixes added by ticket sites
    noise = [
        'ticket + hotel deals', 'hotel deals', 'vip package', 'vip',
        'with special guest', 'special guest', 'night 1', 'night 2', 'night 3',
        'night one', 'night two', 'presented by', 'live in concert',
        'live', 'tour', 'the tour', 'concert', 'tickets', 'ticket',
        '- general admission', 'general admission', 'ga',
    ]
    for n in noise:
        t = re.sub(r'\b' + re.escape(n) + r'\b', '', t)
    # Remove all non-alphanumeric characters
    t = re.sub(r'[^a-z0-9\s]', '', t)
    # Collapse whitespace
    t = re.sub(r'\s+', ' ', t).strip()
    return t


def title_similarity(t1, t2):
    """
    Check if two normalized titles refer to the same event.
    Returns True if one title starts with the other (catches artist + suffix),
    or if they share enough words.
    """
    if not t1 or not t2: return False
    # Exact match after normalization
    if t1 == t2: return True
    # One is a prefix of the other (e.g. "eric church" in "eric church night 1")
    if t1.startswith(t2) or t2.startswith(t1): return True
    # Word overlap — if 80%+ of the shorter title's words appear in the longer
    words1 = set(t1.split())
    words2 = set(t2.split())
    if not words1 or not words2: return False
    shorter = words1 if len(words1) <= len(words2) else words2
    longer  = words1 if len(words1) >  len(words2) else words2
    if len(shorter) == 0: return False
    overlap = len(shorter & longer) / len(shorter)
    return overlap >= 0.8


def dedup(static_events, scraped_events):
    """
    Merge static + scraped with smart deduplication.
    Static events always win.
    Scraped events are dropped if they match any static event by:
      1. Exact ID match
      2. Same date + normalized title similarity (prefix match or 80% word overlap)
    Among scraped events themselves, same dedup logic applies.
    """
    static_ids = {ev['id'] for ev in static_events}

    # Build index of static (norm_title, date) pairs
    static_index = []  # list of (norm_title, date)
    for ev in static_events:
        static_index.append((norm_title(ev['title']), ev['date']))

    def matches_static(ev):
        nt = norm_title(ev['title'])
        d  = ev['date']
        for s_nt, s_d in static_index:
            if s_d != d: continue
            if title_similarity(nt, s_nt): return True
        return False

    # Also build venue+date index from static to block all scraped events
    # for venues we already have good static coverage of
    static_venue_dates = set()
    FULLY_COVERED_VENUES = {
        'dead ringer analog bar', 'greater nevada field', 'sky tavern bike park',
        'idlewild park', 'west street plaza', 'wingfield park',
    }
    for ev in static_events:
        v = (ev.get('venue') or '').lower()
        if any(fv in v for fv in FULLY_COVERED_VENUES):
            static_venue_dates.add((v[:30], ev['date']))

    # Filter scraped against static
    unique_scraped = []
    seen_scraped   = []  # list of (norm_title, date) already added from scraped

    for ev in scraped_events:
        if ev['id'] in static_ids: continue
        if matches_static(ev): continue
        # Skip if we have full static coverage of this venue on this date
        v = (ev.get('venue') or '').lower()
        if any(fv in v for fv in FULLY_COVERED_VENUES):
            if (v[:30], ev['date']) in static_venue_dates: continue

        nt = norm_title(ev['title'])
        d  = ev['date']

        # Check against already-accepted scraped events
        is_dup = False
        for s_nt, s_d in seen_scraped:
            if s_d == d and title_similarity(nt, s_nt):
                is_dup = True
                break
        if is_dup: continue

        seen_scraped.append((nt, d))
        unique_scraped.append(ev)

    merged = static_events + unique_scraped
    merged.sort(key=lambda e: e['date'])
    return merged, len(unique_scraped)

# ── MAIN ─────────────────────────────────────────────────────────────────────

ALL_SCRAPERS = {
    'drp':     scrape_downtown_reno,
    'trs':     scrape_reno_scene,
    'hp':      scrape_holland,
    'art':     scrape_artown,
    'vlt':     scrape_visit_tahoe,
    'tir':     scrape_thisisreno,
    'gtn':     scrape_gotahoenorth,
    'stn':     scrape_southtahoenow,
    'askr':    scrape_askreno,
    'bruka':   scrape_bruka,
    'val':     scrape_valhalla,
    'sky':     scrape_skytavern,
    'lal':     scrape_live_lakeview,
    'lnp':     scrape_lateniteproductions,
    'tixr':    scrape_tixr_playwright,
    'cargo':   scrape_cargo,
    'alpine':  scrape_alpine,
    'nugget':  scrape_nugget,
    'atlantis':scrape_atlantis,
    'pepp':    scrape_peppermill,
    'gsr':     scrape_gsr,
    'pioneer': scrape_pioneer,
    'cbc':     scrape_crystal_bay,
    'bba':     scrape_bba,
    'bart':    scrape_bartley_ranch,
    'aces':    scrape_reno_aces,
    'acesv2':  scrape_reno_aces_v2,
    'sk':      scrape_songkick,
    'tmv':     scrape_tm_venues,
    'nma':     scrape_nma,
    'phil':    scrape_reno_phil,
    'wcp':     scrape_washoe_parks,
    'tbe':     scrape_tahoe_blue,
    'ra':      scrape_ra,
    'eb':      scrape_eventbrite,
    'tm':      scrape_ticketmaster,
}

def main():
    ap = argparse.ArgumentParser(description='Reno/Tahoe Events Master Scraper')
    ap.add_argument('--out',     default=EVENTS_FILE, help='Output events.json path')
    ap.add_argument('--sources', nargs='*', help='Only run these source keys')
    ap.add_argument('--list',    action='store_true', help='List available sources and exit')
    args = ap.parse_args()

    if args.list:
        for k in ALL_SCRAPERS: print(k)
        return

    # Load static seed
    try:
        with open(args.out) as f:
            all_events = json.load(f)
        # Separate static from previously-scraped (scraped IDs start with s_)
        static   = [e for e in all_events if not e['id'].startswith('s_')]
        print(f'Loaded {len(static)} static events', file=sys.stderr)
    except Exception as ex:
        print(f'Could not load {args.out}: {ex}', file=sys.stderr)
        static = []

    # Run scrapers
    print(f'\nRunning scrapers (today={TODAY}, until={UNTIL})…', file=sys.stderr)
    scraped = []
    to_run  = args.sources or list(ALL_SCRAPERS.keys())
    for key in to_run:
        if key not in ALL_SCRAPERS:
            print(f'  Unknown source: {key}', file=sys.stderr)
            continue
        try:
            results = ALL_SCRAPERS[key]()
            scraped.extend(results)
        except Exception as ex:
            print(f'  ERROR in {key}: {ex}', file=sys.stderr)

    # Deduplicate
    merged, added = dedup(static, scraped)
    print(f'\n✓ Static: {len(static)}  Scraped: {len(scraped)}  '
          f'New (after dedup): {added}  Total: {len(merged)}', file=sys.stderr)

    # Strip past events before writing (no point serving them)
    from datetime import date as _date
    today_str = _date.today().isoformat()
    before = len(merged)
    merged = [e for e in merged if (e.get('end') or e.get('date','')) >= today_str]
    print(f'Stripped {before - len(merged)} past events. Remaining: {len(merged)}', file=sys.stderr)

    # Final safety sweep — guarantee every field is a JS-safe type so a single
    # malformed record (from static data or any scraper) can never crash the
    # frontend's string methods (.toLowerCase, localeCompare, etc.)
    STR_FIELDS = ['id','title','cat','date','region','venue','addr','src']
    fixed_count = 0
    for e in merged:
        for f_ in STR_FIELDS:
            if not isinstance(e.get(f_), str):
                e[f_] = str(e.get(f_) or '')
                fixed_count += 1
        if e.get('time') is not None and not isinstance(e['time'], str):
            e['time'] = None; fixed_count += 1
        if e.get('price') is not None and not isinstance(e['price'], str):
            e['price'] = None; fixed_count += 1
        if not isinstance(e.get('isFree'), bool):
            e['isFree'] = bool(e.get('isFree')); fixed_count += 1
        if not isinstance(e.get('tags'), list):
            e['tags'] = []; fixed_count += 1
        if not isinstance(e.get('url'), str):
            e['url'] = str(e.get('url') or ''); fixed_count += 1
        if e.get('end') is not None and not isinstance(e['end'], str):
            e['end'] = None; fixed_count += 1
    if fixed_count:
        print(f'Safety sweep: coerced {fixed_count} malformed fields', file=sys.stderr)

    # Write output
    with open(args.out, 'w') as f:
        json.dump(merged, f, ensure_ascii=False, separators=(',', ':'))
    print(f'✓ Wrote {len(merged)} events to {args.out}', file=sys.stderr)

if __name__ == '__main__':
    main()
