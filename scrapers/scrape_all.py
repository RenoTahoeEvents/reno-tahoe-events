#!/usr/bin/env python3
"""
Reno / Tahoe Events — Master Scraper
Runs hourly via GitHub Actions. Pulls from all available sources,
merges with the static seed events, deduplicates, and writes events.json.

Sources:
  1. Downtown Reno Partnership  (WordPress REST API)
  2. The Reno Scene             (WordPress REST API)
  3. Visit Reno Tahoe           (HTML scrape)
  4. Ask Reno                   (HTML scrape)
  5. Artown                     (HTML scrape)
  6. Crystal Bay Casino         (HTML scrape)
  7. Grand Sierra Resort        (HTML scrape)
  8. Eventbrite                 (Public search API — no key needed)
  9. Resident Advisor           (GraphQL API)
 10. Ticketmaster               (Discovery API — free key needed, see below)
 11. reno.gov special events    (HTML scrape)
 12. Holland Project            (WordPress REST API)
 13. Live at Lakeview           (HTML scrape)
 14. Big Blue Adventure         (HTML scrape)
 15. Sky Tavern                 (HTML scrape)
"""

import json, re, time, sys, os, hashlib, argparse
from datetime import date, timedelta, datetime
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError
from urllib.parse import urlencode, quote
from html.parser import HTMLParser

# ── SHARED CONFIG ─────────────────────────────────────────────────────────────

TODAY     = date.today().isoformat()
UNTIL     = (date.today() + timedelta(days=120)).isoformat()
SEED_FILE = os.path.join(os.path.dirname(__file__), '..', 'events.json')

RENO_KEYWORDS = {
    'reno','sparks','washoe','tahoe','truckee','incline village','crystal bay',
    'stateline','south lake tahoe','kings beach','tahoe city','gerlach',
    'black rock','burning man','playa','pyramid lake','virginia city','carson city',
    'bartley ranch','wingfield','idlewild','rancho san rafael','sky tavern',
    'dead ringer','cargo concert','whitney peak','grand sierra','nugget casino',
    'atlantis casino','peppermill','silver legacy','eldorado','pioneer center',
    'holland project','crystal bay casino','heavenly village','sand harbor',
    'valhalla tahoe','bowers mansion',
}

CAT_MAP = {
    'concert':'concert','music':'concert','show':'concert','performance':'concert',
    'dj':'dj','electronic':'dj','nightlife':'dj','club':'dj','dance':'dj',
    'comedy':'comedy','stand-up':'comedy','improv':'comedy',
    'theater':'theater','theatre':'theater','play':'theater','musical':'theater',
    'art':'art','exhibit':'art','gallery':'art','museum':'art',
    'festival':'festival','fair':'festival',
    'sport':'sports','race':'running','run':'running','marathon':'running',
    'triathlon':'triathlon','bike':'mtb','cycling':'cycling',
    'food':'food','beer':'food','wine':'food','market':'market',
    'family':'family','kids':'family','children':'family',
    'outdoor':'outdoor','hike':'outdoor','fishing':'fishing','hunting':'hunting',
    'fireworks':'fireworks','casino':'casino',
}

GENRE_CAT = {
    'house':'dj','techno':'dj','bass':'dj','electronic':'dj','dnb':'dj',
    'dubstep':'dj','ambient':'dj','trance':'dj','edm':'dj','garage':'dj',
    'hip hop':'concert','hip-hop':'concert','rap':'concert',
    'rock':'concert','indie':'concert','folk':'concert','country':'concert',
    'jazz':'concert','blues':'concert','classical':'concert','metal':'concert',
    'reggae':'concert','funk':'concert','soul':'concert','r&b':'concert',
}

UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124.0 Safari/537.36'

def get(url, headers=None, timeout=15):
    h = {'User-Agent': UA, 'Accept': 'application/json,text/html,*/*'}
    if headers: h.update(headers)
    try:
        req = Request(url, headers=h)
        with urlopen(req, timeout=timeout) as r:
            return r.read().decode('utf-8', errors='replace')
    except Exception as ex:
        print(f'  GET {url[:60]}… ERROR: {ex}', file=sys.stderr)
        return None

def post_json(url, payload, headers=None):
    h = {'User-Agent': UA, 'Content-Type': 'application/json', 'Accept': 'application/json'}
    if headers: h.update(headers)
    try:
        data = json.dumps(payload).encode()
        req = Request(url, data=data, headers=h, method='POST')
        with urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode())
    except Exception as ex:
        print(f'  POST {url[:60]}… ERROR: {ex}', file=sys.stderr)
        return None

def strip_html(s):
    return re.sub(r'<[^>]+>', '', s or '').strip()

def clean(s):
    return re.sub(r'\s+', ' ', strip_html(s)).strip()

def is_local(text):
    t = text.lower()
    return any(k in t for k in RENO_KEYWORDS)

def ev_id(prefix, uid):
    h = hashlib.md5(str(uid).encode()).hexdigest()[:8]
    return f'{prefix}_{h}'

def parse_date(s):
    """Try multiple date formats, return YYYY-MM-DD or None."""
    s = (s or '').strip()
    for fmt in ('%Y-%m-%dT%H:%M:%S', '%Y-%m-%dT%H:%M:%SZ', '%Y-%m-%d',
                '%m/%d/%Y', '%B %d, %Y', '%b %d, %Y', '%d %B %Y'):
        try:
            return datetime.strptime(s[:len(fmt)], fmt).strftime('%Y-%m-%d')
        except: pass
    m = re.search(r'(\d{4}-\d{2}-\d{2})', s)
    return m.group(1) if m else None

def to_12h(s):
    if not s: return None
    m = re.search(r'(\d{1,2}):(\d{2})(?::(\d{2}))?(?:\s*(AM|PM))?', s, re.I)
    if not m: return None
    hh,mm = int(m.group(1)), int(m.group(2))
    ap = m.group(4)
    if ap:
        ap = ap.upper()
    else:
        ap = 'PM' if 12 <= hh < 24 else 'AM'
        hh = hh % 12 or 12
    return f'{hh}:{mm:02d} {ap}'

def guess_cat(title, desc='', tags=None):
    text = (title + ' ' + (desc or '') + ' ' + ' '.join(tags or [])).lower()
    for kw, cat in GENRE_CAT.items():
        if kw in text: return cat
    for kw, cat in CAT_MAP.items():
        if kw in text: return cat
    return 'community'

def make_ev(eid, title, cat, date_str, region, venue, addr, time_str,
            price, is_free, desc, tags, url, src):
    if not date_str or date_str < TODAY or date_str > UNTIL: return None
    if not is_local(f'{title} {venue} {addr} {desc}'):       return None
    return {
        'id': eid, 'title': title[:120], 'cat': cat, 'date': date_str,
        'region': region, 'venue': venue[:80], 'addr': addr,
        'time': time_str, 'price': price, 'isFree': is_free,
        'desc': (desc or '')[:300], 'tags': (tags or [])[:6],
        'url': url, 'src': src,
    }

# ── 1. DOWNTOWN RENO PARTNERSHIP (WordPress REST API) ─────────────────────────
def scrape_downtown_reno():
    print('Scraping Downtown Reno Partnership…', file=sys.stderr)
    events = []
    page = 1
    while page <= 5:
        url = (f'https://downtownreno.org/wp-json/tribe/events/v1/events'
               f'?start_date={TODAY}&per_page=50&page={page}')
        raw = get(url)
        if not raw: break
        try: data = json.loads(raw)
        except: break
        items = data.get('events', [])
        if not items: break
        for item in items:
            d = parse_date(item.get('start_date',''))
            if not d: continue
            title = clean(item.get('title',''))
            venue_d = item.get('venue',{})
            venue = clean(venue_d.get('venue','Downtown Reno'))
            addr  = clean(venue_d.get('address','') + ' ' + venue_d.get('city',''))
            desc  = clean(item.get('description',''))[:300]
            link  = item.get('url','https://downtownreno.org/events/')
            ev = make_ev(ev_id('drp', item.get('id',title+d)),
                title, guess_cat(title,desc), d, 'reno',
                venue, addr, to_12h(item.get('start_date','')),
                None, False, desc, [], link, 'Downtown Reno Partnership')
            if ev: events.append(ev)
        if len(items) < 50: break
        page += 1
        time.sleep(0.5)
    print(f'  Downtown Reno: {len(events)} events', file=sys.stderr)
    return events

# ── 2. THE RENO SCENE (WordPress REST API) ────────────────────────────────────
def scrape_reno_scene():
    print('Scraping The Reno Scene…', file=sys.stderr)
    events = []
    page = 1
    while page <= 8:
        url = (f'https://www.therenoscene.com/wp-json/tribe/events/v1/events'
               f'?start_date={TODAY}&per_page=50&page={page}')
        raw = get(url)
        if not raw: break
        try: data = json.loads(raw)
        except: break
        items = data.get('events', [])
        if not items: break
        for item in items:
            d = parse_date(item.get('start_date',''))
            if not d: continue
            title = clean(item.get('title',''))
            venue_d = item.get('venue',{})
            venue = clean(venue_d.get('venue','Reno'))
            addr  = ', '.join(filter(None,[
                clean(venue_d.get('address','')),
                clean(venue_d.get('city','')),
                clean(venue_d.get('stateprovince','')),
            ]))
            desc  = clean(item.get('description',''))[:300]
            link  = item.get('url','https://www.therenoscene.com/')
            cat   = guess_cat(title, desc)
            ev = make_ev(ev_id('trs', item.get('id',title+d)),
                title, cat, d, 'reno',
                venue, addr, to_12h(item.get('start_date','')),
                None, False, desc, [], link, 'The Reno Scene')
            if ev: events.append(ev)
        if len(items) < 50: break
        page += 1
        time.sleep(0.5)
    print(f'  The Reno Scene: {len(events)} events', file=sys.stderr)
    return events

# ── 3. HOLLAND PROJECT (WordPress REST API) ───────────────────────────────────
def scrape_holland():
    print('Scraping Holland Project…', file=sys.stderr)
    events = []
    for page in range(1, 4):
        url = (f'https://hollandreno.org/wp-json/tribe/events/v1/events'
               f'?start_date={TODAY}&per_page=50&page={page}')
        raw = get(url)
        if not raw: break
        try: data = json.loads(raw)
        except: break
        items = data.get('events', [])
        if not items: break
        for item in items:
            d = parse_date(item.get('start_date',''))
            if not d: continue
            title = clean(item.get('title',''))
            desc  = clean(item.get('description',''))[:300]
            link  = item.get('url','https://hollandreno.org/')
            ev = make_ev(ev_id('hp', item.get('id',title+d)),
                title, guess_cat(title,desc), d, 'reno',
                'The Holland Project', '140 Vesta St, Reno NV',
                to_12h(item.get('start_date','')),
                None, False, desc, ['all ages','indie','DIY'], link, 'Holland Project')
            if ev: events.append(ev)
        if len(items) < 50: break
        time.sleep(0.5)
    print(f'  Holland Project: {len(events)} events', file=sys.stderr)
    return events

# ── 4. EVENTBRITE — Reno/Tahoe public search ─────────────────────────────────
def scrape_eventbrite():
    print('Scraping Eventbrite…', file=sys.stderr)
    events = []
    searches = [
        ('Reno NV', 'reno'),
        ('Lake Tahoe CA', 'tahoe'),
        ('Sparks NV', 'reno'),
        ('Truckee CA', 'tahoe'),
    ]
    for location, region in searches:
        url = (f'https://www.eventbrite.com/api/v3/destination/search/'
               f'?q=&start_date.range_start={TODAY}T00%3A00%3A00'
               f'&start_date.range_end={UNTIL}T23%3A59%3A59'
               f'&location.address={quote(location)}'
               f'&location.within=30mi&expand=venue,organizer'
               f'&page_size=50&page=1')
        raw = get(url, headers={'Accept': 'application/json'})
        if not raw: continue
        try: data = json.loads(raw)
        except: continue
        for ev_data in data.get('events',{}).get('results',[]):
            title = ev_data.get('name','').strip()
            d     = parse_date(ev_data.get('start',{}).get('local',''))
            if not d or not title: continue
            venue_d = ev_data.get('venue') or {}
            venue   = venue_d.get('name','') or ''
            addr    = (venue_d.get('address') or {}).get('localized_address_display','')
            is_free = ev_data.get('is_free', False)
            desc    = clean(ev_data.get('description',{}).get('text',''))[:300]
            link    = ev_data.get('url','https://www.eventbrite.com/')
            if not is_local(f'{title} {venue} {addr}'): continue
            ev = make_ev(ev_id('eb', ev_data.get('id',title+d)),
                title, guess_cat(title,desc), d, region,
                venue or location, addr,
                to_12h(ev_data.get('start',{}).get('local','')),
                'Free' if is_free else None, is_free,
                desc, [], link, 'Eventbrite')
            if ev: events.append(ev)
        time.sleep(1)
    print(f'  Eventbrite: {len(events)} events', file=sys.stderr)
    return events

# ── 5. RESIDENT ADVISOR (GraphQL) ─────────────────────────────────────────────
def scrape_ra():
    print('Scraping Resident Advisor…', file=sys.stderr)
    RA_URL = 'https://ra.co/graphql'
    QUERY = """
    query GetAreaEvents($areaId:ID!,$from:DateTime!,$to:DateTime!,$page:Int!){
      eventListings(filters:{areas:{id:$areaId},listingDate:{gte:$from,lte:$to}}
        pageSize:100 page:$page sort:{listingDate:{order:ASCENDING}}){
        totalResults
        data{id listingDate event{id title startTime isFree content
          genres{name} venue{id name address area{name}}
          artists{name} tickets{salePrice{value}}}}}}"""
    RA_HEADERS = {
        'Content-Type':'application/json','Origin':'https://ra.co',
        'Referer':'https://ra.co/events/us/nevada',
        'ra-content-language':'en','x-ra-platform':'web',
        'User-Agent': UA,
    }
    events = []
    areas  = [('203','reno'),('13','tahoe')]
    from_dt = TODAY + 'T00:00:00'
    to_dt   = UNTIL + 'T23:59:59'
    for area_id, region in areas:
        page = 1
        while True:
            resp = post_json(RA_URL,
                {'query':QUERY,'variables':{'areaId':area_id,'from':from_dt,'to':to_dt,'page':page}},
                RA_HEADERS)
            if not resp: break
            body     = resp.get('data',{}).get('eventListings',{})
            total    = body.get('totalResults',0)
            listings = body.get('data',[])
            for listing in listings:
                ev_d = listing.get('event') or {}
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
                cat = 'dj'
                for g in genres:
                    if g in GENRE_CAT: cat = GENRE_CAT[g]; break
                is_free = ev_d.get('isFree') or False
                tix = ev_d.get('tickets') or []
                prices = [t['salePrice']['value'] for t in tix if t.get('salePrice')]
                price = ('Free' if is_free else
                         (f'${min(prices):.0f}–${max(prices):.0f}' if prices else 'Check RA'))
                desc = re.sub(r'<[^>]+>','',ev_d.get('content') or '').strip()[:300]
                if not desc and artists:
                    desc = f'Featuring {", ".join(artists)}. At {venue}.'
                artist_str = ', '.join(artists)
                display = f'{venue} – {artist_str}' if artist_str else f'{venue} – {title}'
                ra_id = ev_d.get('id','')
                link  = f'https://ra.co/events/{ra_id}' if ra_id else 'https://ra.co/events/us/nevada'
                ev = make_ev(ev_id('ra', listing.get('id','')),
                    display, cat, d, region, venue, addr,
                    to_12h(ev_d.get('startTime','')),
                    price, is_free, desc,
                    artists[:4] + [g.capitalize() for g in genres[:2]] + ['RA'],
                    link, 'Resident Advisor (ra.co)')
                if ev: events.append(ev)
            if len(listings) < 100 or page*100 >= total: break
            page += 1
            time.sleep(1.5)
        time.sleep(2)
    print(f'  Resident Advisor: {len(events)} events', file=sys.stderr)
    return events

# ── 6. TICKETMASTER Discovery API ─────────────────────────────────────────────
# Free API key at developer.ticketmaster.com — store as GitHub secret TM_API_KEY
def scrape_ticketmaster():
    api_key = os.environ.get('TM_API_KEY','')
    if not api_key:
        print('  Ticketmaster: no TM_API_KEY set, skipping', file=sys.stderr)
        return []
    print('Scraping Ticketmaster…', file=sys.stderr)
    events = []
    configs = [
        {'latlong':'39.5296,-119.8138','radius':'50','unit':'miles','name':'Reno area','region':'reno'},
        {'latlong':'38.9399,-119.9772','radius':'30','unit':'miles','name':'Lake Tahoe','region':'tahoe'},
    ]
    for cfg in configs:
        url = (f'https://app.ticketmaster.com/discovery/v2/events.json'
               f'?apikey={api_key}'
               f'&latlong={cfg["latlong"]}&radius={cfg["radius"]}&unit={cfg["unit"]}'
               f'&startDateTime={TODAY}T00:00:00Z&endDateTime={UNTIL}T23:59:59Z'
               f'&size=100&sort=date,asc&locale=en-us')
        raw = get(url)
        if not raw: continue
        try: data = json.loads(raw)
        except: continue
        for item in (data.get('_embedded',{}).get('events') or []):
            title = item.get('name','').strip()
            dates = item.get('dates',{}).get('start',{})
            d     = dates.get('localDate','')
            if not d or not title: continue
            venues   = item.get('_embedded',{}).get('venues',[{}])
            venue_d  = venues[0] if venues else {}
            venue    = venue_d.get('name','')
            city     = (venue_d.get('city') or {}).get('name','')
            state    = (venue_d.get('state') or {}).get('stateCode','')
            addr_st  = (venue_d.get('address') or {}).get('line1','')
            addr     = ', '.join(filter(None,[addr_st, city, state]))
            if not is_local(f'{title} {venue} {city}'): continue
            price_r  = (item.get('priceRanges') or [{}])[0]
            lo, hi   = price_r.get('min'), price_r.get('max')
            price    = (f'${lo:.0f}–${hi:.0f}' if lo and hi
                        else f'${lo:.0f}' if lo else None)
            link     = item.get('url','https://www.ticketmaster.com/')
            segs     = [c.get('name','').lower() for c in item.get('classifications',[])
                        if c.get('segment')]
            cat      = guess_cat(title, ' '.join(segs))
            ev = make_ev(ev_id('tm', item.get('id',title+d)),
                title, cat, d, cfg['region'],
                venue, addr, dates.get('localTime','')[:5] or None,
                price, False, '', [], link, 'Ticketmaster')
            if ev: events.append(ev)
        time.sleep(0.5)
    print(f'  Ticketmaster: {len(events)} events', file=sys.stderr)
    return events

# ── 7. LIVE AT LAKEVIEW (HTML scrape) ─────────────────────────────────────────
def scrape_lakeview():
    print('Scraping Live at Lakeview…', file=sys.stderr)
    raw = get('https://liveatlakeview.com/events/')
    if not raw: return []
    events = []
    # Their events are in divs with class "tribe-events-calendar-list__event"
    blocks = re.findall(r'<article[^>]*tribe-events[^>]*>(.*?)</article>', raw, re.DOTALL)
    for block in blocks:
        title_m = re.search(r'class="tribe-event-url"[^>]*>(.*?)</a>', block, re.DOTALL)
        date_m  = re.search(r'datetime="(\d{4}-\d{2}-\d{2})', block)
        link_m  = re.search(r'href="(https://liveatlakeview\.com/event/[^"]+)"', block)
        if not title_m or not date_m: continue
        title = clean(title_m.group(1))
        d     = date_m.group(1)
        link  = link_m.group(1) if link_m else 'https://liveatlakeview.com/events/'
        ev = make_ev(ev_id('lv', title+d),
            title, 'concert', d, 'tahoe',
            'Lakeview Commons – South Lake Tahoe', 'El Dorado Beach, South Lake Tahoe, CA',
            None, None, False,
            f'Free outdoor concert at Lakeview Commons on El Dorado Beach, South Lake Tahoe.',
            ['free','outdoor','Lake Tahoe','Lakeview Commons'], link, 'Live at Lakeview')
        if ev: events.append(ev)
    print(f'  Live at Lakeview: {len(events)} events', file=sys.stderr)
    return events

# ── 8. ARTOWN (HTML scrape) ───────────────────────────────────────────────────
def scrape_artown():
    print('Scraping Artown…', file=sys.stderr)
    raw = get('https://renoisartown.com/events/')
    if not raw: return []
    events = []
    # Artown uses WordPress/Tribe events
    url = f'https://renoisartown.com/wp-json/tribe/events/v1/events?start_date={TODAY}&per_page=50'
    raw2 = get(url)
    if raw2:
        try: data = json.loads(raw2)
        except: data = {}
        for item in data.get('events',[]):
            d     = parse_date(item.get('start_date',''))
            title = clean(item.get('title',''))
            if not d or not title: continue
            vd    = item.get('venue',{})
            venue = clean(vd.get('venue','Reno'))
            addr  = clean(vd.get('address','') + ' ' + vd.get('city',''))
            desc  = clean(item.get('description',''))[:300]
            link  = item.get('url','https://renoisartown.com/events/')
            ev = make_ev(ev_id('art', item.get('id',title+d)),
                f'Artown – {title}', guess_cat(title,desc), d, 'reno',
                venue, addr, to_12h(item.get('start_date','')),
                None, False, desc, ['Artown','arts','Reno'], link, 'Artown')
            if ev: events.append(ev)
    print(f'  Artown: {len(events)} events', file=sys.stderr)
    return events

# ── 9. CRYSTAL BAY CASINO ─────────────────────────────────────────────────────
def scrape_crystal_bay():
    print('Scraping Crystal Bay Casino…', file=sys.stderr)
    raw = get('https://www.crystalbaycasino.com/entertainment/')
    if not raw: return []
    events = []
    # Events are in show-listing divs
    blocks = re.findall(r'class="show-listing[^"]*"[^>]*>(.*?)(?=class="show-listing|$)',
                        raw, re.DOTALL)
    for block in blocks[:30]:
        title_m = re.search(r'<h[23][^>]*>(.*?)</h[23]>', block, re.DOTALL)
        date_m  = re.search(r'(\w+ \d{1,2},?\s*\d{4})', block)
        link_m  = re.search(r'href="(https?://[^"]+)"', block)
        if not title_m or not date_m: continue
        title = clean(title_m.group(1))
        d     = parse_date(date_m.group(1))
        if not d: continue
        link  = link_m.group(1) if link_m else 'https://www.crystalbaycasino.com/entertainment/'
        ev = make_ev(ev_id('cbc', title+d),
            f'Crystal Bay Casino – {title}', 'concert', d, 'tahoe',
            'Crystal Bay Casino – Crown Room', '14 NV-28, Crystal Bay, NV',
            None, '$20–$50', False,
            f'{title} live at Crystal Bay Casino Crown Room. 21+.',
            ['Crystal Bay','Lake Tahoe','21+'], link, 'Crystal Bay Casino')
        if ev: events.append(ev)
    print(f'  Crystal Bay Casino: {len(events)} events', file=sys.stderr)
    return events

# ── 10. GRAND SIERRA RESORT ───────────────────────────────────────────────────
def scrape_gsr():
    print('Scraping Grand Sierra Resort…', file=sys.stderr)
    raw = get('https://www.grandsierraresort.com/entertainment/concerts-and-shows')
    if not raw: return []
    events = []
    # GSR event blocks
    blocks = re.findall(r'<(?:div|article)[^>]*(?:event|show)[^>]*>(.*?)</(?:div|article)>',
                        raw, re.DOTALL)
    seen = set()
    for block in blocks[:40]:
        title_m = re.search(r'<h[234][^>]*>(.*?)</h[234]>', block, re.DOTALL)
        date_m  = re.search(r'(\w+ \d{1,2},?\s*\d{4})', block)
        link_m  = re.search(r'href="(https?://[^"]+grandsierraresort[^"]+)"', block)
        if not title_m or not date_m: continue
        title = clean(title_m.group(1))
        d     = parse_date(date_m.group(1))
        if not d or title in seen: continue
        seen.add(title)
        link  = link_m.group(1) if link_m else 'https://www.grandsierraresort.com/entertainment'
        ev = make_ev(ev_id('gsr', title+d),
            f'Grand Sierra Resort – {title}', guess_cat(title), d, 'reno',
            'Grand Theatre – Grand Sierra Resort', '2500 E 2nd St, Reno',
            None, '$35–$95', False,
            f'{title} live at Grand Theatre at the Grand Sierra Resort, Reno.',
            ['Grand Sierra','Reno','concert'], link, 'Grand Sierra Resort')
        if ev: events.append(ev)
    print(f'  Grand Sierra Resort: {len(events)} events', file=sys.stderr)
    return events

# ── 11. BIG BLUE ADVENTURE ────────────────────────────────────────────────────
def scrape_bba():
    print('Scraping Big Blue Adventure…', file=sys.stderr)
    raw = get('https://bigblueadventure.com/events/')
    if not raw: return []
    events = []
    blocks = re.findall(r'<(?:div|article)[^>]*event[^>]*>(.*?)</(?:div|article)>',
                        raw, re.DOTALL)
    seen = set()
    for block in blocks[:20]:
        title_m = re.search(r'<h[234][^>]*>(.*?)</h[234]>', block, re.DOTALL)
        date_m  = re.search(r'(\w+ \d{1,2},?\s*\d{4})', block)
        if not title_m or not date_m: continue
        title = clean(title_m.group(1))
        d     = parse_date(date_m.group(1))
        if not d or title in seen: continue
        seen.add(title)
        cat = ('triathlon' if 'tri' in title.lower() else
               'swim' if 'swim' in title.lower() else
               'running' if any(w in title.lower() for w in ['run','marathon','5k']) else
               'mtb' if 'bike' in title.lower() else 'outdoor')
        ev = make_ev(ev_id('bba', title+d),
            title, cat, d, 'tahoe',
            'Lake Tahoe / Truckee', 'North Lake Tahoe, CA',
            None, '$30–$150', False,
            f'Big Blue Adventure event: {title}.',
            ['Big Blue Adventure','Lake Tahoe','outdoor','endurance'],
            'https://bigblueadventure.com/events/', 'Big Blue Adventure')
        if ev: events.append(ev)
    print(f'  Big Blue Adventure: {len(events)} events', file=sys.stderr)
    return events

# ── 12. VISIT LAKE TAHOE ──────────────────────────────────────────────────────
def scrape_visit_tahoe():
    print('Scraping Visit Lake Tahoe…', file=sys.stderr)
    raw = get('https://visitlaketahoe.com/events/')
    if not raw: return []
    events = []
    # Try their events JSON endpoint
    raw2 = get(f'https://visitlaketahoe.com/wp-json/tribe/events/v1/events?start_date={TODAY}&per_page=50')
    if raw2:
        try: data = json.loads(raw2)
        except: data = {}
        for item in data.get('events',[]):
            d     = parse_date(item.get('start_date',''))
            title = clean(item.get('title',''))
            if not d or not title: continue
            vd    = item.get('venue',{})
            venue = clean(vd.get('venue','Lake Tahoe'))
            addr  = clean(vd.get('address','') + ' ' + vd.get('city',''))
            desc  = clean(item.get('description',''))[:300]
            link  = item.get('url','https://visitlaketahoe.com/events/')
            ev = make_ev(ev_id('vlt', item.get('id',title+d)),
                title, guess_cat(title,desc), d, 'tahoe',
                venue, addr, to_12h(item.get('start_date','')),
                None, False, desc, ['Lake Tahoe'], link, 'Visit Lake Tahoe')
            if ev: events.append(ev)
    print(f'  Visit Lake Tahoe: {len(events)} events', file=sys.stderr)
    return events

# ── 13. PIONEER CENTER ────────────────────────────────────────────────────────
def scrape_pioneer():
    print('Scraping Pioneer Center…', file=sys.stderr)
    raw = get('https://www.pioneercenter.com/events/')
    if not raw: return []
    events = []
    # Fallback HTML parse
    blocks = re.findall(r'<(?:div|article)[^>]*event[^>]*>(.*?)</(?:div|article)>',
                        raw, re.DOTALL)
    seen = set()
    for block in blocks[:20]:
        title_m = re.search(r'<h[234][^>]*>(.*?)</h[234]>', block, re.DOTALL)
        date_m  = re.search(r'(\w+ \d{1,2},?\s*\d{4})', block)
        if not title_m or not date_m: continue
        title = clean(title_m.group(1))
        d     = parse_date(date_m.group(1))
        if not d or title in seen: continue
        seen.add(title)
        ev = make_ev(ev_id('pc', title+d),
            title, guess_cat(title), d, 'reno',
            'Pioneer Center for the Performing Arts', '100 S Virginia St, Reno',
            None, '$25–$95', False, f'{title} at Pioneer Center, Reno.',
            ['Pioneer Center','Reno','performing arts'],
            'https://www.pioneercenter.com/', 'Pioneer Center for the Performing Arts')
        if ev: events.append(ev)
    print(f'  Pioneer Center: {len(events)} events', file=sys.stderr)
    return events

# ── MERGE & DEDUPLICATE ───────────────────────────────────────────────────────

def load_seed():
    """Load the existing events.json (static seed)."""
    try:
        with open(SEED_FILE) as f:
            data = json.load(f)
        # Keep only static (non-scraped) events
        static = [e for e in data if not any(
            e['id'].startswith(p) for p in
            ['drp_','trs_','hp_','eb_','ra_','tm_','lv_','art_','cbc_s_',
             'gsr_s_','bba_s_','vlt_','pc_s_'])]
        print(f'Loaded {len(static)} static events from seed', file=sys.stderr)
        return static
    except Exception as ex:
        print(f'Could not load seed: {ex}', file=sys.stderr)
        return []

def dedup(events):
    """Remove duplicate events by ID, then fuzzy-dedup by title+date."""
    seen_ids  = set()
    seen_keys = set()
    out = []
    for ev in events:
        if ev['id'] in seen_ids: continue
        # Fuzzy key: first 40 chars of title + date
        key = ev['title'][:40].lower().strip() + ev['date']
        if key in seen_keys: continue
        seen_ids.add(ev['id'])
        seen_keys.add(key)
        out.append(ev)
    return sorted(out, key=lambda e: e['date'])

# ── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=SEED_FILE, help='Output events.json path')
    ap.add_argument('--sources', nargs='*',
                    help='Only run these sources (drp trs hp eb ra tm lv art cbc gsr bba vlt pc)')
    args = ap.parse_args()

    static = load_seed()
    scraped = []

    runners = {
        'drp': scrape_downtown_reno,
        'trs': scrape_reno_scene,
        'hp':  scrape_holland,
        'eb':  scrape_eventbrite,
        'ra':  scrape_ra,
        'tm':  scrape_ticketmaster,
        'lv':  scrape_lakeview,
        'art': scrape_artown,
        'cbc': scrape_crystal_bay,
        'gsr': scrape_gsr,
        'bba': scrape_bba,
        'vlt': scrape_visit_tahoe,
        'pc':  scrape_pioneer,
    }

    to_run = args.sources or list(runners.keys())
    for key in to_run:
        if key in runners:
            try:
                results = runners[key]()
                scraped.extend(results)
            except Exception as ex:
                print(f'  ERROR in {key}: {ex}', file=sys.stderr)

    print(f'\nScraped: {len(scraped)} new events', file=sys.stderr)
    merged = dedup(static + scraped)
    print(f'After dedup: {len(merged)} total events', file=sys.stderr)

    with open(args.out, 'w') as f:
        json.dump(merged, f, ensure_ascii=False, separators=(',',':'))

    print(f'✓ Wrote {len(merged)} events to {args.out}', file=sys.stderr)
    print(f'  Static: {len(static)}  Scraped: {len(scraped)}  After dedup: {len(merged)}',
          file=sys.stderr)

if __name__ == '__main__':
    main()
