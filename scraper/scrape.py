#!/usr/bin/env python3
"""Scrape AI / ML job openings at life-science and medical-imaging companies in the US.

Pulls from public ATS JSON endpoints (Greenhouse, Lever, Ashby, Workday), keeps
AI/ML-flavored titles located in the US, and writes docs/jobs.json for the site.
Standard library only, so it runs anywhere (including GitHub Actions) with no installs.
"""
import json
import re
import sys
import concurrent.futures as cf
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMPANIES = ROOT / "scraper" / "companies.json"
OUT = ROOT / "docs" / "jobs.json"
UA = "Mozilla/5.0 (ai-lifesci-jobs scraper; +https://github.com)"

WORKDAY_QUERIES = ["machine learning", "artificial intelligence", "deep learning",
                   "computer vision", "data scientist", "computational biology"]
WORKDAY_PAGES = 3  # 20 results per page per query

# --- Title filters -----------------------------------------------------------
AI_TITLE = re.compile(
    r"\b(machine[- ]learning|deep[- ]learning|artificial intelligence|computer vision|"
    r"data scien\w*|computational|bioinformatic\w*|cheminformatic\w*|NLP|natural language|"
    r"LLMs?|generative|foundation models?|neural|perception|reinforcement learning|"
    r"applied scientist|research engineer|algorithms?|image analysis|"
    r"imaging scien\w*|digital pathology|statistical genetic\w*|"
    r"ML ?Ops|data engineer\w*)\b", re.I)
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
    if any(looks_us(l) for l in locations):
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


def iso(dt):
    if dt is None:
        return None
    if isinstance(dt, (int, float)):  # epoch ms
        return datetime.fromtimestamp(dt / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    return str(dt)[:10]


# --- ATS adapters: each yields dicts with title, locations, countries, url, posted ---
def greenhouse(c):
    data = fetch(f"https://boards-api.greenhouse.io/v1/boards/{c['slug']}/jobs")
    for j in data.get("jobs", []):
        locs = [j.get("location", {}).get("name", "")]
        for m in j.get("metadata") or []:
            if "location" in (m.get("name") or "").lower() and isinstance(m.get("value"), list):
                locs += [str(v) for v in m["value"]]
        yield dict(id=f"gh-{j['id']}", title=j["title"], locations=locs, countries=[],
                   url=j["absolute_url"], posted=iso(j.get("first_published") or j.get("updated_at")))


def lever(c):
    for j in fetch(f"https://api.lever.co/v0/postings/{c['slug']}?mode=json"):
        cat = j.get("categories", {})
        locs = cat.get("allLocations") or [cat.get("location", "")]
        yield dict(id=f"lv-{j['id']}", title=j["text"], locations=locs, countries=[j.get("country")],
                   url=j["hostedUrl"], posted=iso(j.get("createdAt")))


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
                   url=j["jobUrl"], posted=iso(j.get("publishedAt")))


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
                   url=info.get("externalUrl") or f"{base}/{site}{path}", posted=info.get("startDate"))


ADAPTERS = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby, "workday": workday}


def keep_title(title, c):
    if EXCLUDE_TITLE.search(title):
        return False
    if not (AI_TITLE.search(title) or AI_TITLE_CS.search(title)):
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
            locs = [l for l in j["locations"] if l]
            if not is_us(locs, j["countries"], c.get("us_default", False)):
                continue
            tags = []
            if IMAGING_TITLE.search(title) or "Imaging" in c["domain"] or "Pathology" in c["domain"]:
                tags.append("Imaging")
            if INTERN_TITLE.search(title):
                tags.append("Intern")
            if any(re.search(r"remote", l, re.I) for l in locs):
                tags.append("Remote")
            out.append(dict(id=j["id"], title=title, company=c["name"], domain=c["domain"],
                            location=" · ".join(dict.fromkeys(locs)) or "United States",
                            url=j["url"], posted=j["posted"], tags=tags))
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
