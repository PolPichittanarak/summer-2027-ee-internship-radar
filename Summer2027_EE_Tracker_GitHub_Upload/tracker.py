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
STRICT_ELIGIBILITY = re.compile(r"(?:must (?:be|hold|have) (?:a )?u\.?s\.? citizen|us citizenship or green card|u\.?s\.? citizen(?:ship)? (?:or|and) (?:lawful )?permanent residen|citizenship(?:\s+is)? required|active (?:security )?clearance required|only us citizens|us persons? only)", re.I)
EXPORT_REVIEW = re.compile(r"\b(itar|export control|u\.?s\.? person|security clearance|citizenship requirements)\b", re.I)


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


def detect_eligibility(desc, existing=""):
    if STRICT_ELIGIBILITY.search(desc):
        return "US citizen/green card required — verify exact wording"
    if EXPORT_REVIEW.search(desc):
        return "Export control — verify"
    return existing or "Not specified — verify"


def normalized_job(job, source, today, first_seen=None):
    title, url = clean(job.get("title")), job.get("url", "").strip()
    desc = clean(job.get("description") or job.get("summary") or "")
    if not title or not url.startswith("https://") or not summer_ok(title, desc, job.get("context", "")):
        return None
    score, category, tags = score_fit(title, desc)
    return {
        "id": stable_id(url), "company": clean(job.get("company") or "Unknown"), "title": title,
        "location": clean(job.get("location") or "Location unspecified"), "url": url_key(url),
        "source": source, "source_key": job.get("source_key", ""),
        "summary": desc[:450], "score": score, "category": category, "tags": tags,
        "eligibility": detect_eligibility(clean(job.get("description")), job.get("eligibility", "")),
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
                if prev.get("eligibility", "").startswith(("US citizen", "Defense", "Export", "Sensitive", "Degree")) and normalized["eligibility"] == "Not specified — verify":
                    normalized["eligibility"] = prev["eligibility"]
                by_id[jobid] = normalized
            else:
                by_id[jobid] = normalized
                new_jobs.append(normalized)
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
    fields = ["score", "company", "title", "location", "category", "tags", "eligibility", "posted_at", "first_seen", "last_seen", "opening", "source", "url"]
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
        requests_list = [(kind, company, slug) for kind, entries in conf.items() for company, slug in entries.items()]
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
            "note": "First seen = first discovered here, not original publication date. Confirm openings and eligibility on employer sites."}
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
