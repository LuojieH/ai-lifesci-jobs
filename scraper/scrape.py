#!/usr/bin/env python3
"""Scrape AI / ML job openings at life-science and medical-imaging companies in the US.

Pulls from public ATS JSON endpoints (Greenhouse, Lever, Ashby, Workday), keeps
AI/ML-flavored titles located in the US, and writes docs/jobs.json for the site.
Standard library only, so it runs anywhere (including GitHub Actions) with no installs.
"""
import html
import json
import re
import sys
import concurrent.futures as cf
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMPANIES = ROOT / "scraper" / "companies.json"
OUT = ROOT / "docs" / "jobs.json"
UA = "Mozilla/5.0 (ai-lifesci-jobs scraper; +https://github.com)"

WORKDAY_QUERIES = ["machine learning", "artificial intelligence", "deep learning",
                   "computer vision", "data scientist", "computational biology", "postdoctoral", "postdoc"]
WORKDAY_PAGES = 3  # 20 results per page per query

# --- Title filters -----------------------------------------------------------
AI_TITLE = re.compile(
    r"\b(machine[- ]learning|deep[- ]learning|artificial intelligence|computer vision|"
    r"data scien\w*|computational|bioinformatic\w*|cheminformatic\w*|NLP|natural language|"
    r"LLMs?|generative|foundation models?|neural|perception|reinforcement learning|"
    r"applied scientist|research engineer|algorithms?|image analysis|"
    r"imaging scien\w*|digital pathology|statistical genetic\w*|"
    r"ML ?Ops|data engineer\w*|multimodal|representation learning|in[- ]silico|protein design)\b", re.I)
AI_TITLE_CS = re.compile(r"\b(AI|ML|GenAI|AI/ML|ML/AI)\b")  # case-sensitive acronyms
EXCLUDE_TITLE = re.compile(
    r"\b(recruit\w*|sales|account executive|account manager|marketing|counsel|paralegal|"
    r"talent pool|executive assistant|business partner)\b", re.I)
DOMAIN_TITLE = re.compile(
    r"(bio|health|medic|clinic|drug|genom|protein|patholog|imaging|life scien|chem|"
    r"molecul|\bcell|pharma|BioNeMo|Clara|MONAI|radiolog|surgical)", re.I)
IMAGING_TITLE = re.compile(
    r"(imag|vision|patholog|radiolog|microscop|ultrasound|\bMRI\b|\bCT\b|x-ray|"
    r"segmentation|visual|spatial|endoscop|optical|photonic)", re.I)
POSTDOC_TITLE = re.compile(r"post-?doc", re.I)
# Postdoc titles are often just "Postdoctoral Fellow, X Lab", so for those we judge AI-ness by the description.
AI_DESC = re.compile(r"machine learning|deep learning|artificial intelligence|computer vision|neural net\w*|"
                     r"foundation models?|generative models?|language models?|transformers?\b|"
                     r"representation learning", re.I)
AI_DESC_MIN_HITS = 3
INTERN_TITLE = re.compile(r"\b(intern|internship|co-?op|student|summer|graduate program|placement)\b", re.I)

# --- US location detection ---------------------------------------------------
STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts",
    "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
    "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
    "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont",
    "VA": "Virginia", "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
    "DC": "District of Columbia",
}
US_CITIES = [
    "San Francisco", "South San Francisco", "Bay Area", "Boston", "New York", "NYC", "Seattle",
    "Salt Lake City", "San Diego", "Redwood City", "Palo Alto", "Menlo Park", "Mountain View",
    "Berkeley", "Emeryville", "Brisbane", "Fremont", "Austin", "Chicago", "Philadelphia",
    "Princeton", "Baltimore", "Bethesda", "Durham", "Raleigh", "Research Triangle", "Denver",
    "Boulder", "Los Angeles", "Pittsburgh", "Houston", "Indianapolis", "Tarrytown", "Rahway",
    "Thousand Oaks", "Foster City", "Waltham", "Watertown", "Somerville", "Lexington",
    "Pleasanton", "Hayward", "Alameda", "Oakland", "Santa Clara", "San Jose", "Sunnyvale",
    "Bothell", "Madison", "Minneapolis", "Atlanta", "Nashville", "St. Louis", "Ann Arbor",
    "Marlborough", "Andover", "Milwaukee", "Waukesha", "Groton", "Pearl River", "Collegeville",
    "Titusville", "Spring House", "Raritan", "New Brunswick", "Summit", "Lawrenceville",
    "Gaithersburg", "Rockville", "Ridgefield", "Danbury", "Kenilworth", "Tucson", "Phoenix",
    "Irvine", "Carlsbad", "Miami", "Dallas", "Fort Worth", "Kalamazoo", "Cambridge, MA",
    "Cambridge, Massachusetts", "Kendall Square", "Rensselaer", "Morristown", "Basking Ridge",
    "Bridgewater", "Parsippany", "Sleepy Hollow", "Libertyville", "Oceanside", "Vacaville",
]
US_EXPLICIT = re.compile(r"(United States|\bUSA\b|\bU\.S\.A?\.?|\bUS\b|\bAmericas?\b|North America)", re.I)
US_STATE_ABBR = re.compile(r"[,\-–]\s*(" + "|".join(STATES) + r")\b")
US_STATE_NAME = re.compile(r"\b(" + "|".join(re.escape(s) for s in STATES.values()) + r")\b", re.I)
US_CITY = re.compile(r"\b(" + "|".join(re.escape(c) for c in US_CITIES) + r")\b", re.I)
# Workday codes like "Hyderabad - TS - IN" would otherwise read as Indiana.
NON_US = re.compile(
    r"\b(India|Hyderabad|Bangalore|Bengaluru|Mumbai|Pune|Chennai|China|Shanghai|Beijing|Ireland|Dublin|"
    r"Canada|Toronto|Montreal|Vancouver|United Kingdom|England|London|Germany|Switzerland|Basel|Zug|"
    r"France|Paris|Spain|Madrid|Barcelona|Belgium|Netherlands|Japan|Tokyo|Singapore|Israel|Australia|"
    r"Brazil|Mexico|Poland|Italy|Sweden|Denmark|Iceland)\b|-\s*IN\s*$", re.I)
AMBIGUOUS = re.compile(r"^\s*(remote|hybrid|cambridge|anywhere|flexible|multiple locations|\d+ locations)?\s*$", re.I)


def looks_us(loc: str) -> bool:
    if not loc:
        return False
    # "New South Wales", "Washington" etc. could trip — the explicit/city/state checks are a heuristic.
    return bool(US_EXPLICIT.search(loc) or US_CITY.search(loc) or US_STATE_NAME.search(loc)
                or US_STATE_ABBR.search(loc))


def is_us(locations, countries, us_default):
    """countries: structured country values (preferred when present)."""
    countries = [c for c in countries if c]
    if any(c.strip().upper() in ("US", "USA", "UNITED STATES", "UNITED STATES OF AMERICA") for c in countries):
        return True
    if countries and len(locations) <= 1:  # one location and its country isn't US
        return False
    parts = [p for l in locations for p in re.split(r";|\||\bor\b| / ", l)]  # "Boston, MA; London, UK"
    if any(looks_us(p) and not NON_US.search(p) for p in parts):
        return True
    if countries:  # structured data says non-US
        return False
    return us_default and all(AMBIGUOUS.match(l or "") for l in locations)


# --- HTTP --------------------------------------------------------------------
def fetch(url, body=None, timeout=30):
    req = urllib.request.Request(
        url, data=json.dumps(body).encode() if body is not None else None,
        headers={"User-Agent": UA, "Accept": "application/json", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def fetch_html(url, headers=None, timeout=30):
    # Career sites built for browsers reject the scraper UA, so present as a desktop browser.
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/120.0 Safari/537.36", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore")


def iso(dt):
    if dt is None:
        return None
    if isinstance(dt, (int, float)):  # epoch ms
        return datetime.fromtimestamp(dt / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    return str(dt)[:10]


def html_text(s):
    """Greenhouse double-escapes its HTML; unescape twice, then drop tags."""
    if not s:
        return ""
    s = html.unescape(html.unescape(s))
    s = re.sub(r"<(br|/p|/li|/div|/h\d)[^>]*>", "\n", s, flags=re.I)
    return html.unescape(re.sub(r"<[^>]+>", " ", s))


# --- Required experience -------------------------------------------------------
_NUMS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
         "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15}
_NUM = r"(\d{1,2}|" + "|".join(_NUMS) + r")"
# "3+ years of industry experience", "5-7 years' experience", "two or more years working ..."
EXP_A = re.compile(_NUM + r"\s*(?:\+|plus)?\s*(?:(?:-|–|to)\s*" + _NUM + r"\s*\+?\s*)?(?:or more\s+)?"
                   r"(?:\(\d+\)\s*)?years?['’]?\s*(?=(?P<tail>[^.;\n]{0,90}))", re.I)
# "experience: minimum of 3 years", "at least 2 years of ..."
EXP_B = re.compile(r"(?:at least|minimum(?: of)?|min\.?)\s+" + _NUM + r"\s*\+?\s*years?\s*(?=(?P<tail>[^.;\n]{0,90}))", re.I)
EXP_WORDS = re.compile(r"experience|industry|industrial|working|work\b|post-?doc|relevant|hands-on|professional", re.I)
EXP_NOISE = re.compile(r"\b(old|of age|ago|history|in business|track record of the company|warranty|tenure)\b", re.I)
EXP_NOISE_BEFORE = re.compile(r"(over the (past|last)|for over|more than \w+ decades|founded|since|within|next|up to|every|per)\s*$", re.I)


def _num(x):
    return _NUMS.get(x.lower()) if not x.isdigit() else int(x)


def experience(desc):
    """Smallest stated years-of-experience requirement (degree tracks like 'PhD + 0-2 yrs or
    BS + 6 yrs' resolve to the most lenient path). Returns (years | None, matched phrase)."""
    if not desc:
        return None, ""
    best = None
    for rx in (EXP_A, EXP_B):
        for m in rx.finditer(desc):
            tail = m.group("tail")
            before = desc[max(0, m.start() - 40):m.start()]
            if EXP_NOISE.search(tail[:25]) or EXP_NOISE_BEFORE.search(before):
                continue
            # the experience word must be close: in the tail, or just before ("Experience: 3+ years")
            if not (EXP_WORDS.search(tail[:60]) or re.search(r"experience[^.;\n]{0,30}$", before, re.I)):
                continue
            n = _num(m.group(1))
            if n is None or n > 25:
                continue
            if best is None or n < best[0]:
                start = max(0, m.start() - 30)
                snippet = " ".join(desc[start:m.end() + min(len(tail), 70)].split())
                best = (n, ("…" if start else "") + snippet[:160])
    return best if best else (None, "")


# --- ATS adapters: each yields dicts with title, locations, countries, url, posted ---
def greenhouse(c):
    data = fetch(f"https://boards-api.greenhouse.io/v1/boards/{c['slug']}/jobs?content=true")
    for j in data.get("jobs", []):
        locs = [j.get("location", {}).get("name", "")]
        for m in j.get("metadata") or []:
            if "location" in (m.get("name") or "").lower() and isinstance(m.get("value"), list):
                locs += [str(v) for v in m["value"]]
        yield dict(id=f"gh-{j['id']}", title=j["title"], locations=locs, countries=[],
                   url=j["absolute_url"], posted=iso(j.get("first_published") or j.get("updated_at")),
                   desc=html_text(j.get("content")))


def lever(c):
    for j in fetch(f"https://api.lever.co/v0/postings/{c['slug']}?mode=json"):
        cat = j.get("categories", {})
        locs = cat.get("allLocations") or [cat.get("location", "")]
        yield dict(id=f"lv-{j['id']}", title=j["text"], locations=locs, countries=[j.get("country")],
                   url=j["hostedUrl"], posted=iso(j.get("createdAt")),
                   desc="\n".join([j.get("descriptionPlain") or ""]
                                  + [l.get("text", "") + "\n" + html_text(l.get("content")) for l in j.get("lists") or []]
                                  + [j.get("additionalPlain") or ""]))


def ashby(c):
    data = fetch(f"https://api.ashbyhq.com/posting-api/job-board/{c['slug']}")
    for j in data.get("jobs", []):
        if j.get("isListed") is False:
            continue
        locs = [j.get("location", "")]
        countries = [((j.get("address") or {}).get("postalAddress") or {}).get("addressCountry")]
        for s in j.get("secondaryLocations") or []:
            locs.append(s.get("location", ""))
            countries.append(((s.get("address") or {}).get("postalAddress") or {}).get("addressCountry"))
        yield dict(id=f"ab-{j['id']}", title=j["title"], locations=locs, countries=countries,
                   url=j["jobUrl"], posted=iso(j.get("publishedAt")), desc=j.get("descriptionPlain") or "")


def workday(c):
    tenant, dc, site = c["slug"].split("/")
    base = f"https://{tenant}.{dc}.myworkdayjobs.com"
    api = f"{base}/wday/cxs/{tenant}/{site}"
    seen = {}
    for q in c.get("queries", WORKDAY_QUERIES):
        for page in range(WORKDAY_PAGES):
            try:
                d = fetch(f"{api}/jobs", {"appliedFacets": {}, "limit": 20, "offset": page * 20, "searchText": q})
            except Exception:
                break
            posts = d.get("jobPostings") or []
            for p in posts:
                if "externalPath" in p:
                    seen.setdefault(p["externalPath"], p)
            if len(posts) < 20:
                break
    # Only fetch details for titles that pass the filter (details give country + exact date).
    for path, p in seen.items():
        if not keep_title(p.get("title", ""), c):
            continue
        try:
            info = fetch(api + path)["jobPostingInfo"]
        except Exception:
            continue
        locs = [info.get("location", "")] + list(info.get("additionalLocations") or [])
        countries = [(info.get("country") or {}).get("descriptor")]  # primary only; is_us also checks loc text
        yield dict(id=f"wd-{tenant}-{info.get('jobReqId') or path}", title=info.get("title", p["title"]),
                   locations=locs, countries=countries,
                   url=info.get("externalUrl") or f"{base}/{site}{path}", posted=info.get("startDate"),
                   desc=html_text(info.get("jobDescription")))


SITE_PAGES = 5  # result pages per query for the HTML career-site adapters below


def _phenom_ddo(page):
    """Phenom career sites embed their data as `phApp.ddo = {...}` in the page."""
    i = page.find("phApp.ddo")
    return json.JSONDecoder().raw_decode(page[page.index("{", i):])[0] if i >= 0 else {}


def phenom(c):
    """slug: site root including locale, e.g. https://careers.ucb.com/global/en"""
    base = c["slug"].rstrip("/")
    seen = {}
    # The server-rendered page ignores ?keywords= on some sites, so walk the whole board (10 per page).
    for page in range(c.get("max_pages", 80)):
        try:
            res = _phenom_ddo(fetch_html(f"{base}/search-results?from={page * 10}&s=1")).get("eagerLoadRefineSearch", {})
        except Exception:
            break
        for j in res.get("data", {}).get("jobs", []):
            seen.setdefault(j["jobId"], j)
        if (page + 1) * 10 >= res.get("totalHits", 0):
            break
    for jid, j in seen.items():
        if not keep_title(j.get("title", ""), c):
            continue
        try:
            detail = _phenom_ddo(fetch_html(f"{base}/job/{jid}")).get("jobDetail", {}).get("data", {}).get("job", {})
        except Exception:
            detail = {}
        yield dict(id=f"ph-{jid}", title=j["title"], locations=j.get("multi_location") or [j.get("location", "")],
                   countries=[j.get("country")], url=f"{base}/job/{jid}", posted=iso(j.get("postedDate")),
                   desc=html_text(detail.get("description")) or j.get("descriptionTeaser", ""))


def _job_posting_ld(page):
    for m in re.finditer(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', page, re.S):
        try:
            d = json.loads(m.group(1))
        except ValueError:
            continue
        for x in d if isinstance(d, list) else [d]:
            if isinstance(x, dict) and x.get("@type") == "JobPosting":
                return x
    return {}


def radancy(c):
    """Radancy (TalentBrew) sites, e.g. jobs.takeda.com. slug: site root."""
    base = c["slug"].rstrip("/")
    seen = {}
    for q in c.get("queries", WORKDAY_QUERIES):
        params = urllib.parse.urlencode({
            "ActiveFacetID": 0, "CurrentPage": 1, "RecordsPerPage": 100, "Keywords": q, "Location": "",
            "SearchResultsModuleName": "Search Results", "SearchFiltersModuleName": "Search Filters",
            "SortCriteria": 0, "SortDirection": 0, "SearchType": 5})
        try:
            res = json.loads(fetch_html(f"{base}/search-jobs/results?{params}",
                                        {"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"}))["results"]
        except Exception:
            continue
        for m in re.finditer(r'<a href="(/job/[^"]+)"[^>]*>\s*<h2 class="title">([^<]+)</h2>(.*?)</a>', res, re.S):
            loc = re.search(r'<span class="location">([^<]+)', m.group(3))
            seen.setdefault(m.group(1), (html.unescape(m.group(2)).strip(), loc.group(1).strip() if loc else ""))
    for path, (title, loc) in seen.items():
        if not keep_title(title, c):
            continue
        try:
            ld = _job_posting_ld(fetch_html(base + path))
        except Exception:
            ld = {}
        places = ld.get("jobLocation") or []
        addrs = [(p.get("address") or {}) for p in (places if isinstance(places, list) else [places])]
        locs = [loc] + [", ".join(filter(None, [a.get("addressLocality"), a.get("addressRegion")])) for a in addrs]
        yield dict(id=f"rd-{path.rstrip('/').rsplit('/', 1)[-1]}", title=title, locations=locs,
                   countries=[a.get("addressCountry") for a in addrs], url=base + path,
                   posted=iso(ld.get("datePosted")), desc=html_text(ld.get("description")))


def successfactors(c):
    """SAP SuccessFactors career sites (".../search/?q="), e.g. jobs.bostonscientific.com. slug: site root."""
    base = c["slug"].rstrip("/")
    seen = {}
    for q in c.get("queries", WORKDAY_QUERIES):
        for page in range(SITE_PAGES):
            try:  # quoted, or "machine learning" also matches "Machine Operator"
                s = fetch_html(f"{base}/search/?" + urllib.parse.urlencode({"q": f'"{q}"', "startrow": page * 25}))
            except Exception:
                break
            for m in re.finditer(r'<a[^>]*?(?:href="(/job/[^"]+)"[^>]*class="jobTitle-link"|class="jobTitle-link"[^>]*href="(/job/[^"]+)")'
                                 r'[^>]*>([^<]+)</a>', s):
                seen.setdefault(m.group(1) or m.group(2), html.unescape(m.group(3)).strip())
            total = re.search(r'Results <b>[^<]*</b> of <b>(\d+)', s)
            if not total or (page + 1) * 25 >= int(total.group(1)):
                break
    for path, title in seen.items():
        if not keep_title(title, c):
            continue
        try:
            d = fetch_html(base + path)
        except Exception:
            continue
        loc = re.search(r'class="jobGeoLocation"[^>]*>([^<]+)', d)
        posted = re.search(r'itemprop="datePosted"[^>]*content="([^"]+)"', d)
        try:
            posted = datetime.strptime(posted.group(1), "%a %b %d %H:%M:%S UTC %Y").strftime("%Y-%m-%d") if posted else None
        except ValueError:
            posted = None
        i = d.find('itemprop="description"')
        yield dict(id=f"sf-{path.rstrip('/').rsplit('/', 1)[-1]}", title=title,
                   locations=[" ".join(loc.group(1).split()) if loc else ""], countries=[], url=base + path,
                   posted=posted, desc=html_text(d[i:i + 15000]) if i >= 0 else "")


ADAPTERS = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby, "workday": workday,
            "phenom": phenom, "radancy": radancy, "successfactors": successfactors}


def keep_title(title, c):
    if EXCLUDE_TITLE.search(title):
        return False
    if not (AI_TITLE.search(title) or AI_TITLE_CS.search(title) or POSTDOC_TITLE.search(title)):
        return False
    if c.get("title_must_match_domain") and not DOMAIN_TITLE.search(title):
        return False
    return True


def scrape_company(c):
    out = []
    try:
        for j in ADAPTERS[c["ats"]](c):
            title = " ".join(j["title"].split())
            if not keep_title(title, c):
                continue
            if POSTDOC_TITLE.search(title) and not (AI_TITLE.search(title) or AI_TITLE_CS.search(title)):
                desc = j.get("desc", "")
                if len(AI_DESC.findall(desc)) + len(AI_TITLE_CS.findall(desc)) < AI_DESC_MIN_HITS:
                    continue
            locs = [l for l in j["locations"] if l]
            if not is_us(locs, j["countries"], c.get("us_default", False)):
                continue
            tags = []
            if IMAGING_TITLE.search(title) or "Imaging" in c["domain"] or "Pathology" in c["domain"]:
                tags.append("Imaging")
            if INTERN_TITLE.search(title):
                tags.append("Intern")
            if POSTDOC_TITLE.search(title):
                tags.append("Postdoc")
            if any(re.search(r"remote", l, re.I) for l in locs):
                tags.append("Remote")
            exp_years, exp_text = experience(j.get("desc", ""))
            out.append(dict(id=j["id"], title=title, company=c["name"], domain=c["domain"],
                            location=" · ".join(dict.fromkeys(locs)) or "United States",
                            url=j["url"], posted=j["posted"], tags=tags,
                            exp_years=exp_years, exp_text=exp_text))
        return c["name"], out, None
    except Exception as e:
        return c["name"], out, f"{type(e).__name__}: {e}"


def main():
    companies = json.loads(COMPANIES.read_text())["companies"]
    previous = {}
    if OUT.exists():
        try:
            previous = {j["id"]: j.get("first_seen") for j in json.loads(OUT.read_text()).get("jobs", [])}
        except Exception:
            pass
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    jobs, errors = [], {}
    with cf.ThreadPoolExecutor(12) as ex:
        for name, found, err in ex.map(scrape_company, companies):
            print(f"{name:32s} {len(found):4d}" + (f"  ERROR {err}" if err else ""), file=sys.stderr)
            if err:
                errors[name] = err
            jobs += found

    uniq = {}
    for j in jobs:
        j["first_seen"] = previous.get(j["id"]) or today
        uniq[j["id"]] = j
    jobs = sorted(uniq.values(), key=lambda j: (j["posted"] or j["first_seen"], j["first_seen"]), reverse=True)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "count": len(jobs),
        "companies": sorted({c["name"] for c in companies}),
        "errors": errors,
        "jobs": jobs,
    }, indent=1, ensure_ascii=False))
    print(f"\nWrote {len(jobs)} jobs to {OUT}", file=sys.stderr)


if __name__ == "__main__":
    main()
