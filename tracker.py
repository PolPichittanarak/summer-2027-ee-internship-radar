#!/usr/bin/env python3
"""EE internship radar: no authentication, no scraping of LinkedIn, Python stdlib only."""
from __future__ import annotations

import argparse
import csv
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from io import StringIO
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
from urllib.parse import quote, urlsplit, urlunsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DOCS = ROOT / "docs"
CURRENT_YEAR = 2027
BOOTSTRAP_DATE = "2026-11-16"  # actual date of manually checked starter URLs
USER_AGENT = "EE-Intern-Radar/1.0 (personal educational job discovery; respect robots/terms)"

# Broad by design: even a relatively minor resume skill (e.g. analog) is eligible.
TOPICS = {
    "High-speed / SI / PI": (18, [r"signal integrit", r"power integrit", r"serdes", r"transceiver", r"pam.?4", r"s.parameter", r"tdr", r"vna", r"hfss", r"high.speed", r"ethernet", r"pcie", r"optical interconnect"]),
    "Photonics / RF": (15, [r"photonic", r"optical", r"optoelectr", r"laser", r"rf\b", r"rfic", r"microwave", r"radio frequency", r"antenna", r"tia\b", r"fiber"]),
    "Power / EV / Controls": (17, [r"power electronic", r"power system", r"dc.?dc", r"buck", r"boost converter", r"bms\b", r"battery", r"inverter", r"motor control", r"gate driver", r"switching", r"automotive", r"ev power", r"control system", r"instrumentation"]),
    "FPGA / ASIC / Digital": (18, [r"fpga", r"asic", r"rtl", r"verilog", r"soc\b", r"digital design", r"silicon", r"vlsi", r"design verification", r"microarch", r"logic design", r"timing closure"]),
    "Analog / Mixed-signal": (14, [r"analog", r"mixed.signal", r"amplifier", r"op.?amp", r"adc", r"dac\b", r"pll", r"oscillator", r"cadence", r"virtuoso", r"spice", r"circuit design"]),
    "PCB / Hardware": (17, [r"pcb", r"pcba", r"board design", r"hardware design", r"electronic", r"electrical engineering", r"schematic", r"layout", r"board bring.?up", r"altium", r"kicad", r"avionic"]),
    "Embedded / Firmware": (13, [r"embedded", r"firmware", r"stm32", r"microcontroller", r"linux", r"i2c", r"spi\b", r"can.?bus", r"c\+\+", r"system.?verilog"]),
    "Test / Product / Reliability": (13, [r"validation", r"verification", r"reliability", r"product engineer", r"test engineer", r"characterization", r"hardware test", r"lab equipment", r"manufacturing test", r"debug", r"electrical integration", r"failure analysis"]),
}
FORBIDDEN_TERM = re.compile(r"\b(winter|spring|autumn|fall)\b", re.I)
INTERN = re.compile(r"\b(intern(ship)?|co[ -]?op)\b", re.I)
SUMMER_2027 = re.compile(r"\b(?:summer[\s/\-]*2027|2027[\s/\-]*summer|summer\s*['’]?27)\b", re.I)
SUMMER_DATES = re.compile(r"\b(?:may|june|july|august|sept(?:ember)?)\s*(?:/|to|-|–|and)?\s*(?:may|june|july|august|sept(?:ember)?)?\s*2027\b", re.I)
EXCLUDED_ROLES = re.compile(r"\b(new grad|graduate engineer|senior|staff|principal|director|marketing|finance|sales|recruiting|legal|civil engineering|structural engineering|front.?end|backend developer)\b", re.I)
EE_RELEVANCE = re.compile(r"electrical|electronic|hardware|power|circuit|signal|semiconductor|silicon|analog|mixed.signal|rf\b|photon|optical|fpga|asic|rtl|embedded|firmware|avionic|validation|verification|instrumentation|controls|autonom|robot|sensor|test engineer|reliability|manufacturing engineer|design engineering|systems engineering|battery|motor|i&c", re.I)
# Conservative, independent signals: ITAR is an export regulation, NOT a security clearance.
# Never interpret missing language as permission to work on an F-1 visa.
EXPORT_REGULATION = re.compile(r"\b(?:ITAR|EAR|export[- ]control(?:led|s)?|export regulations?)\b", re.I)
CLEARANCE_WORD = re.compile(r"\b(?:security clearance|[QTS] clearance|secret clearance|top secret|TS/SCI)\b", re.I)
US_PERSON_WORD = re.compile(r"\b(?:U\.?S\.?[ -]?person|lawful permanent resident|green card|protected individual|refugee|asylee)\b", re.I)
CITIZEN_WORD = re.compile(r"\b(?:U\.?S\.? citizenship|U\.?S\.? citizen(?:ship|s)?|United States citizen(?:ship|s)?)\b", re.I)
LICENSE_PATH = re.compile(r"(?:or|otherwise|alternatively|\(C\)).{0,145}(?:export (?:authorization|licen[sc]e)|required authorizations? from (?:the )?U\.?S\.?|eligible to (?:obtain|access) (?:the )?export[- ]controlled)",re.I)
MANDATORY = re.compile(r"\b(?:must|require[ds]?|mandatory|only|eligible to|eligibility to|shall)\b",re.I)
NO_SPONSORSHIP = re.compile(r"(?:do(?:es)? not|will not|cannot|unable to|no) (?:provide|offer|support|require)?\s*(?:visa|employment|immigration)?\s*sponsor(?:ship)?|sponsorship (?:is )?not available",re.I)
YES_SPONSORSHIP = re.compile(r"(?:visa|employment) sponsorship (?:is )?(?:available|provided|supported)|(?:can|will) sponsor (?:qualified |eligible )?(?:applicants|candidates)",re.I)
NOT_REQUIRED_CLEARANCE = re.compile(r"(?:security )?clearance (?:is )?not required|no (?:security )?clearance required|does not require (?:a )?(?:security )?clearance",re.I)


def clean(s):
    s = unescape(str(s or ""))
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def get_url(url, timeout=14, tries=2):
    last = None
    for attempt in range(tries):
        try:
            req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json,text/plain,text/html,*/*"})
            with urlopen(req, timeout=timeout) as res:
                return res.read().decode("utf-8", "replace")
        except Exception as exc:
            last = exc
            if attempt + 1 < tries:
                time.sleep(0.8 * (attempt + 1))
    raise RuntimeError(f"{type(last).__name__}: {str(last)[:130]}")


def url_key(url):
    p = urlsplit(url.strip())
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/"), "", ""))


def stable_id(url):
    return hashlib.sha1(url_key(url).encode()).hexdigest()[:16]


def as_date(value):
    if not value:
        return ""
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000 if value > 100000000000 else value, timezone.utc).date().isoformat()
    s = str(value)
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return s[:10] if re.fullmatch(r"\d{4}-\d{2}-\d{2}.*", s) else ""


def summer_ok(title, desc="", context=""):
    """Strict: excludes off-cycle (even Spring/Summer mixed postings)."""
    if not INTERN.search(title):
        return False
    if FORBIDDEN_TERM.search(title) or EXCLUDED_ROLES.search(title):
        return False
    if re.search(r"\bsoftware (?:engineer|development)\b", title, re.I) and not re.search(r"embedded|firmware|hardware|fpga|asic|rtl|device driver|low.level|digital design|motor control|control systems?", title + " " + desc[:1200], re.I):
        return False
    if re.search(r"\b(?:2026|2028|2029)\b", title) and not SUMMER_2027.search(title):
        return False
    # If seasonal conflict is explicitly in the program description, don't pretend summer-only.
    evidence = bool(SUMMER_2027.search(title) or SUMMER_2027.search(desc) or SUMMER_DATES.search(desc) or SUMMER_2027.search(context))
    if not evidence:
        return False
    # Reject stale non-summer program descriptions, but not general references to the four seasons.
    if not EE_RELEVANCE.search(title + " " + desc[:900]):
        return False
    return True


def score_fit(title, desc):
    title_l = clean(title).lower()
    body = clean(desc).lower()[:7500]
    groups = []
    values = []
    for category, (weight, patterns) in TOPICS.items():
        in_title = sum(bool(re.search(p, title_l, re.I)) for p in patterns)
        in_body = sum(bool(re.search(p, body, re.I)) for p in patterns)
        if in_title or in_body:
            points = min(weight + 8, (weight * 0.55 if in_title else weight * 0.25) + min(in_title, 3) * 6 + min(in_body, 4) * 2)
            values.append((points, category))
            groups.append(category)
    if not values and EE_RELEVANCE.search(title):
        values = [(12, "Broad EE")]
        groups = ["Broad EE"]
    values.sort(reverse=True)
    score = round(min(99, 30 + sum(v for v, _ in values[:3]) + min(9, len(values) * 2)))
    if re.search(r"electrical|hardware|power|signal|rf|fpga|asic|analog|embedded|photonic", title_l):
        score = min(99, score + 6)
    primary = values[0][1] if values else "Broad EE"
    return score, primary, [c for _, c in values[:4]]


def detect_restrictions(description, legacy=""):
    """Extract signals, not a legal eligibility determination.

    Look for requirements near the actual citizenship phrase to reduce matches to
    boilerplate EEO language. Unknown means NOT STATED, never automatically eligible.
    """
    desc = clean(description)
    controls = bool(EXPORT_REGULATION.search(desc))
    person_matches = list(CITIZEN_WORD.finditer(desc)) + list(US_PERSON_WORD.finditer(desc))
    # Demand an actual requirement in the vicinity of citizenship text, not a broad
    # 'may require export-controlled access' elsewhere in the description.
    required = False
    for m in person_matches:
        before = desc[max(0,m.start()-110):m.start()]
        after = desc[m.end():min(len(desc),m.end()+105)]
        if re.search(r"\b(?:must (?:(?:therefore )?(?:either )?be|hold|have)|only|requires? (?:that )?(?:applicants?|candidates?|employees?)? ?(?:to )?be|applicants? must be|candidates? must be)\b",before,re.I):
            required = True
        if re.search(r"\b(?:is|are|status)? ?required\b|\b(?:only|mandatory)\b",after,re.I):
            required = True
        if re.search(r"\b(?:only|must be)\s+U\.?S\.?\s*citizens?\b",desc,re.I):
            required = True
    negated = bool(re.search(r"(?:U\.?S\.? citizenship|U\.?S\.? citizen(?:ship)?)\s+(?:is )?not required|not require(?:d)?\s+U\.?S\.? citizenship",desc,re.I))
    # A question such as 'Are you a US citizen?' is not a restriction.
    # An 'or eligible for license' clause is NOT a US-person-only restriction.
    has_export_alternative = bool(re.search(r"(?:or|otherwise|alternatively|\(C\)).{0,190}(?:export.{0,35}(?:authorization|licen[sc]e)|required authorizations? from|federally issued export|eligible to access the export[- ]controlled)",desc,re.I)) or bool(re.search(r"eligible (?:and reasonably likely )?to (?:obtain|receive) (?:the )?required (?:export )?authorization",desc,re.I))
    citizen = "Not stated"
    if required and person_matches and not negated:
        if has_export_alternative:
            citizen = "U.S. person OR export authorization"
        elif US_PERSON_WORD.search(desc) or bool(re.search(r"citizen(?:ship)?\s+or\s+(?:green card|permanent resident)", desc,re.I)):
            citizen = "U.S.-person / protected status required"
        else:
            citizen = "U.S. citizenship required"
    elif negated:
        citizen = "Citizenship not required (stated)"
    # Explicitly distinguish a citizenship-based clearance condition.
    clearance = "Not stated"
    if NOT_REQUIRED_CLEARANCE.search(desc):
        clearance = "Not required (stated)"
    elif CLEARANCE_WORD.search(desc):
        if re.search(r"(?:may|might|could) require.{0,65}clearance",desc,re.I):
            clearance = "May be required"
        elif re.search(r"(?:requires?|must|eligibility to obtain|ability to obtain).{0,115}(?:security |[QTS] )?clearance",desc,re.I):
            clearance = "Required / must be eligible"
        elif re.search(r"(?:active|current) .{0,30}clearance (?:is )?(?:required|mandatory)",desc,re.I):
            clearance = "Required / must be eligible"
        else:
            clearance = "Mentioned — verify"
    if clearance == "Required / must be eligible" and citizen == "Not stated" and re.search(r"U\.?S\.? citizenship.{0,60}(?:required|must)|(?:requirement|includes).{0,95}U\.?S\.? citizenship",desc,re.I):
        citizen = "U.S. citizenship required"
    export = "ITAR/EAR controls mentioned" if controls and re.search(r"\bITAR\b|\bEAR\b",desc,re.I) else ("Export controls mentioned" if controls else "Not stated")
    sponsorship = "Not stated"
    if NO_SPONSORSHIP.search(desc):
        sponsorship = "No sponsorship"
    elif YES_SPONSORSHIP.search(desc):
        sponsorship = "Sponsorship mentioned as available"
    elif re.search(r"(?:will you|do you|require|need).{0,55}sponsorship",desc,re.I):
        sponsorship = "Application asks — verify"
    # Old manually seeded labels are unverified snapshots of the specific role.
    # Show a review prompt rather than upgrading these to confirmed restrictions.
    if not desc and legacy:
        if re.search(r"citizen|green card",legacy,re.I) and not re.search(r"(?:not required|not necessary)", legacy, re.I):
            citizen = "Possible U.S.-person restriction — verify"
        if re.search(r"ITAR|export|defense",legacy,re.I):
            export = "Review export restrictions"
        if re.search(r"clearance",legacy,re.I):
            clearance = "Mentioned — verify"
    # Evidence extracts are deliberately short and attach to a link to the source.
    signals = re.compile(r"\b(?:ITAR|EAR|export control|citizenship|citizen|U\.?S\.? person|permanent resident|security clearance|sponsorship)\b",re.I)
    snippets = []
    for m in signals.finditer(desc):
        snippet = desc[max(0,m.start()-65):min(len(desc),m.end()+115)]
        snippet = clean(snippet)
        if snippet and snippet not in snippets:
            snippets.append(snippet)
        if len(snippets)>=2:break
    return {"citizenship_requirement":citizen, "export_control":export,
            "security_clearance":clearance, "visa_sponsorship":sponsorship,
            "eligibility_evidence":" … ".join(snippets)[:345] if snippets else ("Starter snapshot flag only; check employer posting" if legacy else "No explicit requirement found in available description")}


def detect_eligibility(description, existing=""):
    """Legacy API retained for older callers; richer fields are in detect_restrictions."""
    flags=detect_restrictions(description,existing)
    return flags["citizenship_requirement"] if flags["citizenship_requirement"] != "Not stated" else flags["export_control"]


def is_startup_watch(company):
    """Curated emerging-company watchlist; not a claim about employer headcount."""
    conf=read_json(DATA / "sources.json", {})
    return clean(company).casefold() in {clean(x).casefold() for x in conf.get("startup_watchlist", [])}


def normalized_job(job, source, today, first_seen=None):
    title, url = clean(job.get("title")), job.get("url", "").strip()
    desc = clean(job.get("description") or job.get("summary") or "")
    if not title or not url.startswith("https://") or not summer_ok(title, desc, job.get("context", "")):
        return None
    score, category, tags = score_fit(title, desc)
    flags = detect_restrictions(job.get("description") or "", job.get("eligibility", ""))
    return {
        "id": stable_id(url), "company": clean(job.get("company") or "Unknown"), "title": title,
        "location": clean(job.get("location") or "Location unspecified"), "url": url_key(url),
        "source": source, "source_key": job.get("source_key", ""),
        "summary": desc[:450], "score": score, "category": category, "tags": tags,
        "eligibility": job.get("eligibility", "") or "See restriction columns",
        **flags,
        "company_type": "Startup watch" if is_startup_watch(job.get("company", "")) else "Other / discovered",
        "posted_at": as_date(job.get("posted_at")), "first_seen": first_seen or today,
        "last_seen": today, "opening": "Listed", "misses": 0,
    }


def parse_greenhouse(company, slug, raw):
    jobs = json.loads(raw).get("jobs", [])
    for j in jobs:
        yield {
            "company": company, "title": j.get("title"), "location": (j.get("location") or {}).get("name", ""),
            "url": j.get("absolute_url") or f"https://job-boards.greenhouse.io/{slug}/jobs/{j['id']}",
            "description": j.get("content", ""),
            # Some boards do not provide first_published; updated_at is NOT a posted date.
            "posted_at": j.get("first_published", ""),
            "source_key": f"greenhouse:{slug}",
        }


def parse_ashby(company, slug, raw):
    for j in json.loads(raw).get("jobs", []):
        if not j.get("isListed", True):
            continue
        yield {
            "company": company, "title": j.get("title"),
            "location": j.get("location") or j.get("locationName") or "",
            "url": j.get("jobUrl") or j.get("applyUrl") or "",
            "description": j.get("descriptionPlain") or j.get("descriptionHtml") or "",
            "posted_at": j.get("publishedAt") or "",
            "source_key": f"ashby:{slug}",
        }


def parse_lever(company, slug, raw):
    jobs = json.loads(raw)
    if not isinstance(jobs, list):
        raise ValueError("Lever response is not an array")
    for j in jobs:
        cat = j.get("categories") or {}
        yield {
            "company": company, "title": j.get("text"), "location": cat.get("location") or "",
            "url": j.get("hostedUrl") or j.get("applyUrl") or "",
            "description": " ".join([clean(j.get("descriptionPlain")), clean(j.get("description")), clean(j.get("additionalPlain")), clean(j.get("lists"))]),
            "posted_at": j.get("createdAt") or "", "source_key": f"lever:{slug}",
        }


class HTMLRows(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows, self.cells, self.cell, self.in_cell, self.hrefs = [], [], "", False, []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "tr":
            self.cells = []
        elif tag in ("td", "th"):
            self.in_cell, self.cell, self.hrefs = True, "", []
        elif tag == "a" and self.in_cell and attrs.get("href", "").startswith("http"):
            self.hrefs.append(attrs["href"])

    def handle_data(self, data):
        if self.in_cell:
            self.cell += data

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.in_cell:
            self.cells.append((clean(self.cell), list(self.hrefs)))
            self.in_cell = False
        elif tag == "tr" and len(self.cells) >= 3:
            self.rows.append(self.cells)


def parse_community(name, raw):
    """Best-effort parser for curated markdown and HTML tables. Never assert these are live ATS results."""
    seen = set()
    current_heading = ""
    tables = []
    for line in raw.splitlines():
        if re.match(r"^#{1,5}\s+", line):
            current_heading = clean(line.lstrip("# "))
        if not line.lstrip().startswith("|") or line.count("|") < 4:
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 3 or all(re.fullmatch(r":?-{2,}:?", c.strip()) for c in cells[:3]):
            continue
        tables.append(([(clean(re.sub(r"!?\[([^\]]+)\]\([^)]+\)", r"\1", c)).replace("**", ""), re.findall(r"https?://[^)\s>\"']+", c)) for c in cells], current_heading))
    html = HTMLRows()
    html.feed(raw)
    tables.extend((row, "") for row in html.rows)
    for cells, heading in tables:
        texts = [c[0] for c in cells]
        # Typical columns: company, position, location, posting/apply/age.
        role_index = next((i for i, t in enumerate(texts[:4]) if INTERN.search(t) and EE_RELEVANCE.search(t)), None)
        if role_index is None:
            continue
        title = texts[role_index]
        company = texts[0] if role_index != 0 else (texts[1] if len(texts) > 1 else name)
        location = texts[role_index + 1] if role_index + 1 < len(texts) else ""
        hrefs = [(i, u) for i, (_text, links) in enumerate(cells) for u in links]
        # Prefer links in Apply/Posting cells, avoid internal GitHub links.
        choices = [(i, u) for i, u in hrefs if not any(x in u.lower() for x in ("github.com", "githubusercontent.com", "simplify.jobs/blog"))]
        if not choices:
            continue
        url = next((u for i, u in reversed(choices) if i >= max(2, role_index)), choices[-1][1])
        key = url_key(url)
        if key in seen:
            continue
        seen.add(key)
        yield {"company": company, "title": title, "location": location,
               "url": url, "description": "", "context": heading,
               "source_key": f"community:{name}"}


def gather_one(kind, company, slug):
    if kind == "greenhouse":
        url = f"https://boards-api.greenhouse.io/v1/boards/{quote(slug)}/jobs?content=true"
        fn = parse_greenhouse
    elif kind == "ashby":
        url = f"https://api.ashbyhq.com/posting-api/job-board/{quote(slug)}"
        fn = parse_ashby
    elif kind == "lever":
        url = f"https://api.lever.co/v0/postings/{quote(slug)}?mode=json"
        fn = parse_lever
    else:
        url, fn = slug, None
    raw = get_url(url)
    candidates = list(fn(company, slug, raw)) if fn else list(parse_community(company, raw))
    return candidates


def read_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return default


def save_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=False) + "\n", encoding="utf-8")


def merge_jobs(old_jobs, incoming, today, successful, online):
    by_id = {j["id"]: j for j in old_jobs}
    new_jobs = []
    observed = set()
    for source, candidates in incoming:
        for j in candidates:
            normalized = normalized_job(j, source, today)
            if normalized is None:
                continue
            jobid = normalized["id"]
            # De-dupe exact canonical posting URLs; prefer direct employer feeds to community.
            observed.add(jobid)
            if jobid in by_id:
                prev = by_id[jobid]
                normalized["first_seen"] = prev.get("first_seen", today)
                if not normalized.get("posted_at"):
                    normalized["posted_at"] = prev.get("posted_at", "")
                if "community:" in normalized.get("source_key", "") and "community:" not in prev.get("source_key", "") and prev.get("source_key") != "seed":
                    normalized["source"] = prev.get("source", "Employer board")
                    normalized["source_key"] = prev.get("source_key")
                    if len(prev.get("summary", "")) > len(normalized.get("summary", "")):
                        normalized["summary"] = prev["summary"]
                        normalized["score"], normalized["category"], normalized["tags"] = score_fit(normalized["title"], prev["summary"])
                # Never overwrite strong employer wording with blank community-feed data.
                if normalized.get("eligibility_evidence", "").startswith("No explicit") and prev.get("eligibility_evidence", "").startswith("No explicit") is False:
                    for field in ("citizenship_requirement", "export_control", "security_clearance", "visa_sponsorship", "eligibility_evidence"):
                        if prev.get(field):
                            normalized[field] = prev[field]
                normalized["company_type"] = "Startup watch" if is_startup_watch(normalized["company"]) else "Other / discovered"
                by_id[jobid] = normalized
            else:
                by_id[jobid] = normalized
                new_jobs.append(normalized)
    # Upgrade stored rows produced by older versions without inventing eligibility.
    for j in by_id.values():
        if "citizenship_requirement" not in j:
            j.update(detect_restrictions("", j.get("eligibility", "")))
        j["company_type"] = "Startup watch" if is_startup_watch(j.get("company", "")) else "Other / discovered"
    if online:
        for j in by_id.values():
            if j["id"] in observed:
                continue
            src = j.get("source_key", "")
            if src in successful:
                j["misses"] = j.get("misses", 0) + 1
                if j["misses"] >= 2:
                    j["opening"] = "Check opening"
            elif src == "seed":
                j["opening"] = "Snapshot — verify"
    result = sorted(by_id.values(), key=lambda j: (-int(j.get("opening", "") == "Listed"), -j.get("score", 0), j.get("company", ""), j.get("title", "")))
    return result, new_jobs


def write_csv(jobs):
    fields = ["score", "company", "company_type", "title", "location", "category", "tags",
              "citizenship_requirement", "export_control", "security_clearance",
              "eligibility_evidence", "posted_at", "first_seen", "last_seen", "opening", "source", "url"]
    with (DOCS / "internships.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(fields)
        for j in jobs:
            w.writerow([", ".join(j.get(k, [])) if k == "tags" else j.get(k, "") for k in fields])


def build_html(jobs, meta):
    template = (ROOT / "templates" / "dashboard.html").read_text(encoding="utf-8")
    safe_jobs = json.dumps(jobs, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    safe_meta = json.dumps(meta, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    (DOCS / "index.html").write_text(template.replace("__JOB_JSON__", safe_jobs).replace("__META_JSON__", safe_meta), encoding="utf-8")


def notify_discord(jobs, webhook):
    if not webhook or not jobs:
        return
    listed = sorted(jobs, key=lambda j: -j["score"])[:14]
    blocks = [f"**{len(jobs)} new Summer 2027 EE internship matches discovered** (newly seen, not necessarily newly published):"]
    for j in listed:
        blocks.append(f"• **{j['score']}/99** {j['company']} — {j['title']} ({j['location']})\n{j['url']}")
    if len(jobs) > len(listed):
        blocks.append(f"+{len(jobs) - len(listed)} more in your dashboard.")
    parts, acc = [], ""
    for block in blocks:
        if len(acc) + len(block) > 1800:
            parts.append(acc)
            acc = ""
        acc += block + "\n"
    if acc:
        parts.append(acc)
    for part in parts:
        payload = json.dumps({"content": part, "allowed_mentions": {"parse": []}}).encode()
        req = Request(webhook, data=payload, headers={"Content-Type": "application/json", "User-Agent": USER_AGENT}, method="POST")
        try:
            with urlopen(req, timeout=15) as response:
                response.read()
        except Exception as err:
            print(f"WARN: Discord alert failed: {type(err).__name__}: {err}", file=sys.stderr)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--offline", action="store_true", help="Build an offline dashboard from verified starting links; don't fetch network")
    args = p.parse_args()
    DOCS.mkdir(exist_ok=True)
    today = datetime.now(timezone.utc).date().isoformat()
    old = read_json(DATA / "jobs.json", [])
    if not old:
        seed = read_json(DATA / "seed.json", [])
        old = [normalized_job({**j, "source_key": "seed"}, j.get("source", "Verified starter listing"), BOOTSTRAP_DATE) for j in seed]
        old = [j for j in old if j]
    state = read_json(DATA / "state.json", {"initialized": False})
    incoming, statuses, successful = [], [], set()
    if not args.offline:
        conf = read_json(DATA / "sources.json", {})
        supported = ("greenhouse", "ashby", "lever", "community")
        requests_list = [(kind, company, slug) for kind, entries in conf.items() if kind in supported for company, slug in entries.items()]
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = {pool.submit(gather_one, kind, company, slug): (kind, company, slug) for kind, company, slug in requests_list}
            for fut in as_completed(futures):
                kind, company, slug = futures[fut]
                key = f"{kind}:{slug if kind != 'community' else company}"
                try:
                    jobs = fut.result()
                    incoming.append(("Employer ATS" if kind != "community" else f"Community: {company}", jobs))
                    successful.add(key)
                    statuses.append({"source": company, "type": kind, "status": "OK", "rows": len(jobs)})
                except Exception as exc:
                    statuses.append({"source": company, "type": kind, "status": f"ERROR: {str(exc)[:110]}", "rows": 0})
        statuses.sort(key=lambda x: (x["type"], x["source"]))
    jobs, newly_found = merge_jobs(old, incoming, today, successful, not args.offline)
    if args.offline:
        # All starter URLs were individually observed through the public web on Nov 16.
        for job in jobs:
            if job["source_key"] == "seed":
                job["opening"] = "Snapshot — verify"
    meta = {"scan_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if not args.offline else "Starter snapshot: November 16, 2026",
            "year": CURRENT_YEAR, "scanned": not args.offline, "source_ok": sum(s["status"] == "OK" for s in statuses),
            "source_total": len(statuses), "listed": sum(j["opening"] == "Listed" for j in jobs), "total": len(jobs),
            "note": "First seen = discovered, not posted. Export controls are not a security clearance. Flags come from employer wording when available; verify work eligibility directly."}
    save_json(DATA / "jobs.json", jobs)
    save_json(DATA / "source_status.json", statuses)
    save_json(DOCS / "meta.json", meta)
    write_csv(jobs)
    build_html(jobs, meta)
    if not args.offline and successful:
        if state.get("initialized"):
            notify_discord(newly_found, os.getenv("DISCORD_WEBHOOK_URL", ""))
        else:
            print("Initial baseline scan: notifications suppressed to avoid alert flood.")
        state["initialized"] = True
        state["last_success_utc"] = meta["scan_utc"]
        save_json(DATA / "state.json", state)
    print(f"{meta['total']} summer 2027 jobs in dashboard | {len(newly_found)} new this scan | sources {meta['source_ok']}/{meta['source_total']} OK")
    print(DOCS / "index.html")


if __name__ == "__main__":
    main()
