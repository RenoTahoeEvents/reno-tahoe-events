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
    return re.sub(r'\s+', ' ', strip_html(html.unescape(str(s or '')))).strip()

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
    return {
        'id':     eid,
        'title':  html.unescape(clean(title))[:120],
        'cat':    cat,
        'date':   date_str,
        'region': region,
        'venue':  html.unescape(clean(venue))[:80],
        'addr':   html.unescape(clean(addr or '')),
        'time':   time_str,
        'price':  price,
        'isFree': bool(is_free),
        'desc':   html.unescape(clean(desc or ''))[:300],
        'tags':   (tags or [])[:6],
        'url':    url or '',
        'src':    src,
    }

# ── WORDPRESS TRIBE EVENTS SCRAPER (used by many venues) ─────────────────────

def scrape_tribe(base_url, src_name, region, default_venue='', default_addr='',
                 title_prefix='', extra_tags=None, max_pages=5):
    """Generic scraper for any site using The Events Calendar (Tribe) WordPress plugin."""
    events = []
    prefix = re.sub(r'[^a-z]', '', src_name.lower())[:6]
    for page in range(1, max_pages + 1):
        url = (f'{base_url}/wp-json/tribe/events/v1/events'
               f'?start_date={TODAY}&per_page=50&page={page}')
        raw = get(url)
        if not raw: break
        try: data = json.loads(raw)
        except: break
        items = data.get('events', [])
        if not items: break
        for item in items:
            d     = parse_date(item.get('start_date', ''))
            title = clean(item.get('title', ''))
            if not d or not title: continue
            vd    = item.get('venue') or {}
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
        if len(items) < 50: break
        time.sleep(0.5)
    return events

# ── SCRAPERS ──────────────────────────────────────────────────────────────────

def scrape_downtown_reno():
    print('  Downtown Reno Partnership…', file=sys.stderr)
    evts = scrape_tribe('https://downtownreno.org', 'Downtown Reno Partnership',
                        'reno', 'Downtown Reno', 'Downtown Reno, NV')
    print(f'    → {len(evts)}', file=sys.stderr)
    return evts

def scrape_reno_scene():
    print('  The Reno Scene…', file=sys.stderr)
    evts = scrape_tribe('https://www.therenoscene.com', 'The Reno Scene',
                        'reno', 'Reno', 'Reno, NV', max_pages=8)
    print(f'    → {len(evts)}', file=sys.stderr)
    return evts

def scrape_holland():
    print('  Holland Project…', file=sys.stderr)
    evts = scrape_tribe('https://hollandreno.org', 'Holland Project',
                        'reno', 'The Holland Project', '140 Vesta St, Reno NV',
                        extra_tags=['all ages', 'indie', 'DIY'], max_pages=3)
    print(f'    → {len(evts)}', file=sys.stderr)
    return evts

def scrape_artown():
    print('  Artown…', file=sys.stderr)
    evts = scrape_tribe('https://renoisartown.com', 'Artown',
                        'reno', 'Reno', 'Reno, NV',
                        title_prefix='Artown – ',
                        extra_tags=['Artown', 'arts', 'Reno'])
    print(f'    → {len(evts)}', file=sys.stderr)
    return evts

def scrape_visit_tahoe():
    print('  Visit Lake Tahoe…', file=sys.stderr)
    evts = scrape_tribe('https://visitlaketahoe.com', 'Visit Lake Tahoe',
                        'tahoe', 'Lake Tahoe', 'Lake Tahoe, CA/NV',
                        extra_tags=['Lake Tahoe'])
    print(f'    → {len(evts)}', file=sys.stderr)
    return evts

def scrape_thisisreno():
    print('  This Is Reno…', file=sys.stderr)
    evts = scrape_tribe('https://thisisreno.com', 'This Is Reno',
                        'reno', 'Reno', 'Reno, NV')
    print(f'    → {len(evts)}', file=sys.stderr)
    return evts

def scrape_gotahoenorth():
    print('  Go Tahoe North…', file=sys.stderr)
    evts = scrape_tribe('https://www.gotahoenorth.com', 'Go Tahoe North',
                        'tahoe', 'North Lake Tahoe', 'North Lake Tahoe, CA',
                        extra_tags=['North Tahoe'])
    print(f'    → {len(evts)}', file=sys.stderr)
    return evts

def scrape_southtahoenow():
    print('  South Tahoe Now…', file=sys.stderr)
    evts = scrape_tribe('https://www.southtahoenow.com', 'South Tahoe Now',
                        'tahoe', 'South Lake Tahoe', 'South Lake Tahoe, CA',
                        extra_tags=['South Lake Tahoe'])
    print(f'    → {len(evts)}', file=sys.stderr)
    return evts

def scrape_askreno():
    print('  Ask Reno…', file=sys.stderr)
    evts = scrape_tribe('https://ask-reno.com', 'Ask Reno',
                        'reno', 'Reno', 'Reno, NV')
    print(f'    → {len(evts)}', file=sys.stderr)
    return evts

def scrape_bruka():
    print('  Brüka Theatre…', file=sys.stderr)
    evts = scrape_tribe('https://www.bruka.org', 'Brüka Theatre',
                        'reno', 'Brüka Theatre', '99 N Virginia St, Reno NV',
                        extra_tags=['theater', 'Brüka', 'downtown Reno'])
    print(f'    → {len(evts)}', file=sys.stderr)
    return evts

def scrape_valhalla():
    print('  Valhalla Tahoe…', file=sys.stderr)
    evts = scrape_tribe('https://www.valhallatahoe.com', 'Valhalla Tahoe',
                        'tahoe', 'Valhalla Tahoe – Heller Estate',
                        '1 Valhalla Rd, South Lake Tahoe, CA',
                        extra_tags=['Valhalla', 'West Shore', 'outdoor'])
    print(f'    → {len(evts)}', file=sys.stderr)
    return evts

def scrape_skytavern():
    print('  Sky Tavern…', file=sys.stderr)
    evts = scrape_tribe('https://www.skytavern.org', 'Sky Tavern Bike Park',
                        'reno', 'Sky Tavern Bike Park', '2800 Mt Rose Hwy, Reno NV',
                        extra_tags=['Sky Tavern', 'MTB', '8000ft'])
    print(f'    → {len(evts)}', file=sys.stderr)
    return evts

def scrape_live_lakeview():
    print('  Live at Lakeview…', file=sys.stderr)
    evts = scrape_tribe('https://liveatlakeview.com', 'Live at Lakeview',
                        'tahoe', 'Lakeview Commons – South Lake Tahoe',
                        'El Dorado Beach, South Lake Tahoe, CA',
                        extra_tags=['free', 'outdoor', 'Lake Tahoe', 'Lakeview Commons'])
    print(f'    → {len(evts)}', file=sys.stderr)
    return evts

# ── HTML SCRAPERS for venues without Tribe/WordPress APIs ─────────────────────

def scrape_html_events(url, src_name, region, venue, addr,
                       title_pattern, date_pattern, link_pattern=None,
                       extra_tags=None, cat_override=None):
    """Generic HTML scraper using regex patterns."""
    raw = get(url)
    if not raw: return []
    events = []
    prefix = re.sub(r'[^a-z]', '', src_name.lower())[:6]
    # Find all blocks containing both title and date
    # Split on likely event boundaries
    blocks = re.split(r'(?=<(?:article|div|li)[^>]*(?:event|show|listing)[^>]*>)', raw)
    seen = set()
    for block in blocks[:60]:
        t_m = re.search(title_pattern, block, re.DOTALL | re.I)
        d_m = re.search(date_pattern,  block, re.DOTALL | re.I)
        if not t_m or not d_m: continue
        title = clean(t_m.group(1))
        d     = parse_date(d_m.group(1))
        if not title or not d: continue
        key = title[:40] + d
        if key in seen: continue
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
    return events

def scrape_cargo():
    print('  Cargo Concert Hall…', file=sys.stderr)
    raw = get('https://cargoconcerthall.com/events/')
    if not raw:
        print('    → 0', file=sys.stderr)
        return []
    events = []
    # Cargo uses a standard events page — try Tribe API first
    evts = scrape_tribe('https://cargoconcerthall.com', 'Cargo Concert Hall',
                        'reno', 'Cargo Concert Hall – Whitney Peak Hotel',
                        '255 N Virginia St, Reno',
                        extra_tags=['Cargo', 'Whitney Peak', 'Downtown Reno'])
    if not evts:
        # Fall back to HTML
        blocks = re.findall(r'<(?:div|article)[^>]*event[^>]*>(.*?)</(?:div|article)>',
                            raw, re.DOTALL)
        seen = set()
        for block in blocks[:30]:
            t_m = re.search(r'<h[2-4][^>]*>(.*?)</h[2-4]>', block, re.DOTALL)
            d_m = re.search(r'(\w+ \d{1,2},?\s*\d{4}|\d{4}-\d{2}-\d{2})', block)
            if not t_m or not d_m: continue
            title = clean(t_m.group(1))
            d     = parse_date(d_m.group(1))
            if not d or title in seen: continue
            seen.add(title)
            ev = make_ev(scrape_id('cargo', title+d), title,
                         guess_cat(title), d, 'reno',
                         'Cargo Concert Hall – Whitney Peak Hotel',
                         '255 N Virginia St, Reno',
                         None, None, False, f'{title} at Cargo Concert Hall.',
                         ['Cargo', 'Whitney Peak'], 'https://cargoconcerthall.com/',
                         'Cargo Concert Hall')
            if ev: evts.append(ev)
    print(f'    → {len(evts)}', file=sys.stderr)
    return evts

def scrape_alpine():
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
    print('  Nugget Casino…', file=sys.stderr)
    raw = get('https://www.cnty.com/nugget/entertainment')
    if not raw:
        print('    → 0', file=sys.stderr)
        return []
    events = []
    seen = set()
    blocks = re.findall(r'<(?:div|article|li)[^>]*(?:event|show|entertainment)[^>]*>(.*?)</(?:div|article|li)>',
                        raw, re.DOTALL)
    for block in blocks[:40]:
        t_m = re.search(r'<h[2-4][^>]*>(.*?)</h[2-4]>', block, re.DOTALL)
        d_m = re.search(r'(\w+ \d{1,2},?\s*\d{4}|\d{4}-\d{2}-\d{2})', block)
        if not t_m or not d_m: continue
        title = clean(t_m.group(1))
        d     = parse_date(d_m.group(1))
        if not d or title in seen: continue
        seen.add(title)
        l_m = re.search(r'href="(https?://[^"]+)"', block)
        link = l_m.group(1) if l_m else 'https://www.cnty.com/nugget/entertainment'
        ev = make_ev(scrape_id('nug2', title+d), title,
                     guess_cat(title), d, 'reno',
                     'Nugget Casino Resort', '1100 Nugget Ave, Sparks NV',
                     None, None, False, f'{title} at the Nugget Casino Resort.',
                     ['Nugget Casino', 'Sparks'], link, 'Nugget Casino Resort')
        if ev: events.append(ev)
    print(f'    → {len(events)}', file=sys.stderr)
    return events

def scrape_atlantis():
    print('  Atlantis Casino…', file=sys.stderr)
    raw = get('https://atlantiscasino.com/more/events/')
    if not raw:
        print('    → 0', file=sys.stderr)
        return []
    events = []
    seen = set()
    blocks = re.findall(r'<(?:div|article)[^>]*event[^>]*>(.*?)</(?:div|article)>',
                        raw, re.DOTALL)
    for block in blocks[:30]:
        t_m = re.search(r'<h[2-4][^>]*>(.*?)</h[2-4]>', block, re.DOTALL)
        d_m = re.search(r'(\w+ \d{1,2},?\s*\d{4}|\d{4}-\d{2}-\d{2})', block)
        if not t_m or not d_m: continue
        title = clean(t_m.group(1))
        d     = parse_date(d_m.group(1))
        if not d or title in seen: continue
        seen.add(title)
        ev = make_ev(scrape_id('atl2', title+d), title,
                     guess_cat(title), d, 'reno',
                     'Atlantis Casino Resort', '3800 S Virginia St, Reno',
                     None, None, False, f'{title} at Atlantis Casino Resort.',
                     ['Atlantis', 'Reno', 'casino'],
                     'https://atlantiscasino.com/more/events/', 'Atlantis Casino')
        if ev: events.append(ev)
    print(f'    → {len(events)}', file=sys.stderr)
    return events

def scrape_peppermill():
    print('  Peppermill…', file=sys.stderr)
    raw = get('https://www.peppermillreno.com/entertainment/')
    if not raw:
        print('    → 0', file=sys.stderr)
        return []
    events = []
    seen = set()
    blocks = re.findall(r'<(?:div|article)[^>]*(?:event|show|entertainment)[^>]*>(.*?)</(?:div|article)>',
                        raw, re.DOTALL)
    for block in blocks[:30]:
        t_m = re.search(r'<h[2-4][^>]*>(.*?)</h[2-4]>', block, re.DOTALL)
        d_m = re.search(r'(\w+ \d{1,2},?\s*\d{4}|\d{4}-\d{2}-\d{2})', block)
        if not t_m or not d_m: continue
        title = clean(t_m.group(1))
        d     = parse_date(d_m.group(1))
        if not d or title in seen: continue
        seen.add(title)
        ev = make_ev(scrape_id('pepp', title+d), title,
                     guess_cat(title), d, 'reno',
                     'Peppermill Resort Casino', '2707 S Virginia St, Reno',
                     None, None, False, f'{title} at Peppermill Resort Casino.',
                     ['Peppermill', 'Reno', 'casino'],
                     'https://www.peppermillreno.com/entertainment/', 'Peppermill Resort')
        if ev: events.append(ev)
    print(f'    → {len(events)}', file=sys.stderr)
    return events

def scrape_gsr():
    print('  Grand Sierra Resort…', file=sys.stderr)
    raw = get('https://www.grandsierraresort.com/entertainment/concerts-and-shows')
    if not raw:
        print('    → 0', file=sys.stderr)
        return []
    events = []
    seen = set()
    blocks = re.findall(r'<(?:div|article)[^>]*(?:event|show|concert)[^>]*>(.*?)</(?:div|article)>',
                        raw, re.DOTALL)
    for block in blocks[:40]:
        t_m = re.search(r'<h[2-4][^>]*>(.*?)</h[2-4]>', block, re.DOTALL)
        d_m = re.search(r'(\w+ \d{1,2},?\s*\d{4}|\d{4}-\d{2}-\d{2})', block)
        if not t_m or not d_m: continue
        title = clean(t_m.group(1))
        d     = parse_date(d_m.group(1))
        if not d or title in seen: continue
        seen.add(title)
        l_m = re.search(r'href="(https?://[^"]+grandsierraresort[^"]+)"', block)
        link = l_m.group(1) if l_m else 'https://www.grandsierraresort.com/entertainment'
        ev = make_ev(scrape_id('gsr2', title+d), title,
                     guess_cat(title), d, 'reno',
                     'Grand Theatre – Grand Sierra Resort',
                     '2500 E 2nd St, Reno',
                     None, '$35–$95', False,
                     f'{title} live at Grand Sierra Resort.',
                     ['Grand Sierra', 'Reno'], link, 'Grand Sierra Resort')
        if ev: events.append(ev)
    print(f'    → {len(events)}', file=sys.stderr)
    return events

def scrape_pioneer():
    print('  Pioneer Center…', file=sys.stderr)
    raw = get('https://www.pioneercenter.com/events/')
    if not raw:
        print('    → 0', file=sys.stderr)
        return []
    evts = scrape_tribe('https://www.pioneercenter.com', 'Pioneer Center',
                        'reno', 'Pioneer Center for the Performing Arts',
                        '100 S Virginia St, Reno',
                        extra_tags=['Pioneer Center', 'performing arts'])
    if not evts:
        events = []
        seen = set()
        blocks = re.findall(r'<(?:div|article)[^>]*event[^>]*>(.*?)</(?:div|article)>',
                            raw, re.DOTALL)
        for block in blocks[:20]:
            t_m = re.search(r'<h[2-4][^>]*>(.*?)</h[2-4]>', block, re.DOTALL)
            d_m = re.search(r'(\w+ \d{1,2},?\s*\d{4}|\d{4}-\d{2}-\d{2})', block)
            if not t_m or not d_m: continue
            title = clean(t_m.group(1))
            d     = parse_date(d_m.group(1))
            if not d or title in seen: continue
            seen.add(title)
            ev = make_ev(scrape_id('pioc', title+d), title,
                         guess_cat(title), d, 'reno',
                         'Pioneer Center for the Performing Arts',
                         '100 S Virginia St, Reno',
                         None, '$25–$95', False,
                         f'{title} at Pioneer Center.',
                         ['Pioneer Center', 'Reno'],
                         'https://www.pioneercenter.com/', 'Pioneer Center')
            if ev: events.append(ev)
        evts = events
    print(f'    → {len(evts)}', file=sys.stderr)
    return evts

def scrape_crystal_bay():
    print('  Crystal Bay Casino…', file=sys.stderr)
    raw = get('https://www.crystalbaycasino.com/entertainment/')
    if not raw:
        print('    → 0', file=sys.stderr)
        return []
    events = []
    seen = set()
    # Try tixr embeds first
    tixr_ids = re.findall(r'tixr\.com/[^"\']*?(\d{4,})', raw)
    # Parse HTML blocks
    blocks = re.split(r'(?=<(?:div|article)[^>]*(?:show|event|listing)[^>]*>)', raw)
    for block in blocks[:40]:
        t_m = re.search(r'<h[2-4][^>]*>(.*?)</h[2-4]>', block, re.DOTALL)
        d_m = re.search(r'(\w+ \d{1,2},?\s*\d{4}|\d{4}-\d{2}-\d{2})', block)
        if not t_m or not d_m: continue
        title = clean(t_m.group(1))
        d     = parse_date(d_m.group(1))
        if not d or title in seen: continue
        seen.add(title)
        l_m = re.search(r'href="(https?://[^"]+)"', block)
        link = l_m.group(1) if l_m else 'https://www.crystalbaycasino.com/entertainment/'
        ev = make_ev(scrape_id('cbc2', title+d), title,
                     guess_cat(title), d, 'tahoe',
                     'Crystal Bay Casino – Crown Room',
                     '14 NV-28, Crystal Bay, NV',
                     None, '$20–$50', False,
                     f'{title} at Crystal Bay Casino Crown Room. 21+.',
                     ['Crystal Bay', 'Lake Tahoe', '21+'],
                     link, 'Crystal Bay Casino')
        if ev: events.append(ev)
    print(f'    → {len(events)}', file=sys.stderr)
    return events

def scrape_bba():
    print('  Big Blue Adventure…', file=sys.stderr)
    raw = get('https://bigblueadventure.com/events/')
    if not raw:
        print('    → 0', file=sys.stderr)
        return []
    events = []
    seen = set()
    blocks = re.findall(r'<(?:div|article)[^>]*event[^>]*>(.*?)</(?:div|article)>',
                        raw, re.DOTALL)
    for block in blocks[:20]:
        t_m = re.search(r'<h[2-4][^>]*>(.*?)</h[2-4]>', block, re.DOTALL)
        d_m = re.search(r'(\w+ \d{1,2},?\s*\d{4}|\d{4}-\d{2}-\d{2})', block)
        if not t_m or not d_m: continue
        title = clean(t_m.group(1))
        d     = parse_date(d_m.group(1))
        if not d or title in seen: continue
        seen.add(title)
        cat = ('triathlon' if 'tri' in title.lower() else
               'swim' if 'swim' in title.lower() else
               'running' if any(w in title.lower() for w in ['run','marathon','5k']) else
               'mtb' if 'bike' in title.lower() else 'outdoor')
        ev = make_ev(scrape_id('bba2', title+d), title, cat, d, 'tahoe',
                     'Lake Tahoe / Truckee', 'North Lake Tahoe, CA',
                     None, '$30–$150', False, f'Big Blue Adventure: {title}.',
                     ['Big Blue Adventure', 'Lake Tahoe', 'outdoor', 'endurance'],
                     'https://bigblueadventure.com/events/', 'Big Blue Adventure')
        if ev: events.append(ev)
    print(f'    → {len(events)}', file=sys.stderr)
    return events

def scrape_bartley_ranch():
    print('  Bartley Ranch (Washoe County)…', file=sys.stderr)
    raw = get('https://www.washoecounty.gov/parks/facilities/bartley_ranch.php')
    if not raw:
        print('    → 0', file=sys.stderr)
        return []
    evts = scrape_tribe('https://www.washoecounty.gov', 'Washoe County Parks',
                        'reno', 'Bartley Ranch – Robert Z. Hawkins Amphitheater',
                        '6000 Bartley Ranch Rd, Reno NV',
                        extra_tags=['Bartley Ranch', 'outdoor', 'amphitheater'])
    print(f'    → {len(evts)}', file=sys.stderr)
    return evts

def scrape_reno_aces():
    print('  Reno Aces (MiLB)…', file=sys.stderr)
    # MiLB has a schedule API
    raw = get('https://www.milb.com/reno/schedule/full-schedule')
    if not raw:
        print('    → 0', file=sys.stderr)
        return []
    events = []
    # Look for game dates in the page
    games = re.findall(
        r'"date"\s*:\s*"(\d{4}-\d{2}-\d{2})".*?"opponent"\s*:\s*"([^"]+)"',
        raw, re.DOTALL)
    seen = set()
    for d, opp in games[:50]:
        if d < TODAY or d > UNTIL: continue
        if d in seen: continue
        seen.add(d)
        title = f'Reno Aces vs {opp}'
        ev = make_ev(scrape_id('aces2', d + opp), title,
                     'sports', d, 'reno',
                     'Greater Nevada Field', '250 Evans Ave, Reno',
                     '6:35 PM', '$9–$38', False,
                     f'Reno Aces AAA baseball vs {opp} at Greater Nevada Field.',
                     ['baseball', 'AAA', 'Reno Aces', 'family'],
                     'https://www.milb.com/reno/schedule', 'Reno Aces / MiLB')
        if ev: events.append(ev)
    print(f'    → {len(events)}', file=sys.stderr)
    return events

# ── RESIDENT ADVISOR (GraphQL) ────────────────────────────────────────────────

def scrape_ra():
    print('  Resident Advisor…', file=sys.stderr)
    QUERY = """
    query GetAreaEvents($areaId:ID!,$from:DateTime!,$to:DateTime!,$page:Int!){
      eventListings(filters:{areas:{id:$areaId},listingDate:{gte:$from,lte:$to}}
        pageSize:100 page:$page sort:{listingDate:{order:ASCENDING}}){
        totalResults
        data{id listingDate event{id title startTime isFree content
          genres{name} venue{id name address area{name}}
          artists{name} tickets{salePrice{value}}}}}}"""
    RA_HDR = {
        'Content-Type': 'application/json',
        'Origin': 'https://ra.co',
        'Referer': 'https://ra.co/events/us/nevada',
        'ra-content-language': 'en',
        'x-ra-platform': 'web',
        'User-Agent': UA,
    }
    events = []
    from_dt = TODAY + 'T00:00:00'
    to_dt   = UNTIL + 'T23:59:59'
    for area_id, region in [('203','reno'), ('13','tahoe')]:
        page = 1
        while True:
            resp = post_json('https://ra.co/graphql',
                {'query': QUERY, 'variables': {
                    'areaId': area_id, 'from': from_dt,
                    'to': to_dt, 'page': page}}, RA_HDR)
            if not resp: break
            body     = resp.get('data',{}).get('eventListings',{})
            total    = body.get('totalResults', 0)
            listings = body.get('data', [])
            for listing in listings:
                ev_d  = listing.get('event') or {}
                title = (ev_d.get('title') or '').strip()
                if not title: continue
                d = parse_date(listing.get('listingDate') or ev_d.get('startTime',''))
                if not d: continue
                vd    = ev_d.get('venue') or {}
                venue = (vd.get('name') or '').strip()
                addr  = (vd.get('address') or '').strip()
                area  = ((vd.get('area') or {}).get('name') or '').strip()
                if not is_local(f'{title} {venue} {addr} {area}'): continue
                artists = [a['name'] for a in (ev_d.get('artists') or []) if a.get('name')]
                genres  = [g['name'].lower() for g in (ev_d.get('genres') or [])]
                cat     = 'dj'
                for g in genres:
                    for kws, c in CAT_KEYWORDS:
                        if any(kw in g for kw in c):
                            cat = kws; break
                is_free = ev_d.get('isFree') or False
                tix     = ev_d.get('tickets') or []
                prices  = [t['salePrice']['value'] for t in tix if t.get('salePrice')]
                price   = ('Free' if is_free else
                           f'${min(prices):.0f}–${max(prices):.0f}' if prices else 'Check RA')
                desc    = clean(ev_d.get('content') or '')[:300]
                if not desc and artists:
                    desc = f'Featuring {", ".join(artists)}. At {venue}.'
                artist_str = ', '.join(artists)
                display    = f'{venue} – {artist_str}' if artist_str else f'{venue} – {title}'
                ra_id      = ev_d.get('id','')
                link       = f'https://ra.co/events/{ra_id}' if ra_id else 'https://ra.co/events/us/nevada'
                ev = make_ev(scrape_id('ra', listing.get('id','')),
                    display, cat, d, region, venue, addr,
                    to_12h(ev_d.get('startTime','')),
                    price, is_free, desc,
                    artists[:3] + ['RA'] + (['Dead Ringer'] if 'dead ringer' in venue.lower() else []),
                    link, 'Resident Advisor (ra.co)')
                if ev: events.append(ev)
            if len(listings) < 100 or page*100 >= total: break
            page += 1
            time.sleep(1.5)
        time.sleep(2)
    print(f'    → {len(events)}', file=sys.stderr)
    return events

# ── EVENTBRITE (public search) ────────────────────────────────────────────────

def scrape_eventbrite():
    print('  Eventbrite…', file=sys.stderr)
    events = []
    searches = [
        ('Reno NV',          'reno'),
        ('Sparks NV',        'reno'),
        ('Lake Tahoe CA',    'tahoe'),
        ('Truckee CA',       'tahoe'),
        ('South Lake Tahoe', 'tahoe'),
    ]
    for location, region in searches:
        url = (f'https://www.eventbrite.com/api/v3/destination/search/'
               f'?start_date.range_start={TODAY}T00%3A00%3A00'
               f'&start_date.range_end={UNTIL}T23%3A59%3A59'
               f'&location.address={quote(location)}'
               f'&location.within=25mi&expand=venue&page_size=50')
        raw = get(url)
        if not raw: continue
        try: data = json.loads(raw)
        except: continue
        for item in data.get('events',{}).get('results',[]):
            title = (item.get('name') or '').strip()
            d     = parse_date((item.get('start') or {}).get('local',''))
            if not title or not d: continue
            vd    = item.get('venue') or {}
            venue = (vd.get('name') or location).strip()
            addr  = ((vd.get('address') or {}).get('localized_address_display') or '')
            if not is_local(f'{title} {venue} {addr}'): continue
            is_free = item.get('is_free', False)
            desc    = clean((item.get('description') or {}).get('text',''))[:300]
            ev = make_ev(scrape_id('eb', item.get('id', title+d)),
                title, guess_cat(title, desc), d, region,
                venue, addr, to_12h((item.get('start') or {}).get('local','')),
                'Free' if is_free else None, is_free, desc, [],
                item.get('url','https://www.eventbrite.com/'), 'Eventbrite')
            if ev: events.append(ev)
        time.sleep(1)
    print(f'    → {len(events)}', file=sys.stderr)
    return events

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
    for latlong, radius, region in configs:
        url = (f'https://app.ticketmaster.com/discovery/v2/events.json'
               f'?apikey={api_key}&latlong={latlong}&radius={radius}&unit=miles'
               f'&startDateTime={TODAY}T00:00:00Z&endDateTime={UNTIL}T23:59:59Z'
               f'&size=100&sort=date,asc&locale=en-us')
        raw = get(url)
        if not raw: continue
        try: data = json.loads(raw)
        except: continue
        for item in (data.get('_embedded',{}).get('events') or []):
            title = (item.get('name') or '').strip()
            d     = (item.get('dates',{}).get('start',{}).get('localDate',''))
            if not title or not d: continue
            venues  = (item.get('_embedded',{}).get('venues') or [{}])
            vd      = venues[0]
            venue   = (vd.get('name') or '').strip()
            city    = ((vd.get('city') or {}).get('name',''))
            state   = ((vd.get('state') or {}).get('stateCode',''))
            addr_st = ((vd.get('address') or {}).get('line1',''))
            addr    = ', '.join(filter(None,[addr_st, city, state]))
            if not is_local(f'{title} {venue} {city}'): continue
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
        time.sleep(0.5)
    print(f'    → {len(events)}', file=sys.stderr)
    return events

# ── DEDUPLICATION ─────────────────────────────────────────────────────────────

def dedup(static_events, scraped_events):
    """
    Merge static + scraped with strict deduplication.
    Static events always win. Scraped events are only added
    if they don't match any static event by title+date.
    """
    # Index static events by (normalized_title, date)
    static_keys = set()
    for ev in static_events:
        key = (re.sub(r'[^a-z0-9]','', ev['title'].lower())[:30], ev['date'])
        static_keys.add(key)

    # Also index by ID
    static_ids = {ev['id'] for ev in static_events}

    # Filter scraped: skip anything that matches a static event
    unique_scraped = []
    seen_scraped   = set()
    for ev in scraped_events:
        if ev['id'] in static_ids: continue
        key = (re.sub(r'[^a-z0-9]','', ev['title'].lower())[:30], ev['date'])
        if key in static_keys: continue      # static already covers this
        if key in seen_scraped: continue     # duplicate scraped
        seen_scraped.add(key)
        # Also check scraped ID uniqueness
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

    # Write output
    with open(args.out, 'w') as f:
        json.dump(merged, f, ensure_ascii=False, separators=(',', ':'))
    print(f'✓ Wrote {len(merged)} events to {args.out}', file=sys.stderr)

if __name__ == '__main__':
    main()
