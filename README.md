# Summer 2027 EE Internship Radar

A resume-weighted tracker for **Summer 2027 ONLY**: EE/hardware, high-speed SI/PI, optical, analog/mixed-signal, power, FPGA/ASIC, embedded, hardware validation, RF, motor control, and relevant adjacent technical roles.

## Files to open immediately
- `docs/index.html` — portable, filterable dashboard; works even without a server.
- `docs/internships.csv` — spreadsheet-compatible current snapshot.
- The separate `Summer2027_EE_Internships.xlsx` supplied with this package is a polished Excel snapshot.

> This package has a **verified starting snapshot**, not a live background service yet. To make it continuous, enable the GitHub Action below. A scan cannot run while this chat is closed unless a scheduler is configured.

## Activate monitoring and alerts (about 5 setup steps)
1. Create a **new GitHub repository** (private or public), then upload **all files** from this folder, **including `.github/workflows/scan.yml`**, preserving the directory structure. You can use *Add file → Upload files* in GitHub or `git push`.
2. Open your repo's **Actions** tab and enable workflows if prompted. Choose **Scan Summer 2027 EE internships → Run workflow** for the first refresh.
3. In **Settings → Actions → General**, ensure workflow permissions allow writing repository contents. The workflow also explicitly requests `contents: write`.
4. Optional push alerts: create a private Discord server/channel → **Edit Channel → Integrations → Webhooks → New Webhook → Copy Webhook URL**. In the GitHub repo, **Settings → Secrets and variables → Actions → New repository secret**, name it `DISCORD_WEBHOOK_URL` and paste the URL. Keep it secret. **First scheduled scan initializes the baseline without flooding alerts**; subsequent scans alert on newly discovered matches.
5. Optional shareable dashboard: in **Settings → Pages**, publish from branch `main` and folder `/docs`. Then bookmark your `https://USERNAME.github.io/REPO/` dashboard. Private GitHub Pages availability depends on plan; the dashboard file also works locally.

The workflow scans **twice per hour** on GitHub's best-effort schedule (it may run later than the cron minute). Notifications only occur if a Discord webhook is configured and the scan actually runs. No LinkedIn scraping or login is used.

## Run locally
Requires Python 3.10+. No third-party Python packages are needed.
```bash
python3 tracker.py
python3 -m unittest discover -s tests -v
```

## How it works
- Direct, published read-only company career-board feeds from **Greenhouse** and **Ashby** (add **Lever** companies as you discover valid board slugs).
- Three public, community-maintained hardware/summer internship lists help catch roles at Workday and other employers not present in the company APIs.
- Checks the **title AND description** for an explicit summer 2027 term. Strictly rejects spring, winter and fall internships; if a term is ambiguous it is omitted rather than quietly including non-summer positions.
- Scores across all your resume areas, including analog/mixed-signal even when not the strongest skill. Does NOT suppress older jobs merely because their posting date is old.
- Tracks `posted_at` only when the source provides a posting date. Otherwise shows a separately labeled `first_seen` date. `New` means *first discovered by the tracker*, **not necessarily newly published**.
- Marks jobs with explicit citizenship or export-control restrictions for review rather than assuming eligibility. A definite restriction may mean a role is not available to you on an F-1 visa; check each posting.
- Saves `data/jobs.json`, `docs/internships.csv`, and a self-contained `docs/index.html`. State lives in `data/state.json`.
- Keeps jobs that disappear from scans flagged `Check opening` rather than asserting that they are closed. Source outages never trigger removal.
- The dashboard's application statuses are saved in your own browser's localStorage, not to the repo. Use the **Export applications** button for backup. Changing browsers/devices does not sync those annotations.

## Customize
Edit `data/sources.json` with verified career-board slugs and curated GitHub README URLs. Examples:
```json
"greenhouse": {"Company Name": "slug-from-job-boards.greenhouse.io/slug"},
"lever": {"Company Name": "slug-from-jobs.lever.co/slug"},
"ashby": {"Company Name": "slug-from-jobs.ashbyhq.com/slug"}
```
For employers with Workday-only careers pages, add a curated feed or implement their published endpoints as an additional provider; there is no universal company API. This is **not a comprehensive scan of LinkedIn**. For LinkedIn, turn on LinkedIn native job alerts for saved queries such as `hardware engineering intern summer 2027`, `analog design intern summer 2027`, `FPGA intern summer 2027`, and `power electronics intern summer 2027` and use those as an additional channel.

## Practical cautions
- Feed freshness, source coverage and company job-board behavior change; the dashboard shows per-source errors and success. A 200 response does not necessarily mean the company has all its roles on that board.
- GitHub scheduled workflows can be delayed and can be disabled after 60 days without repository activity on some public repos. Check **Actions** occasionally.
- GitHub repositories storing jobs may have additional terms/licenses; only normalized job facts and links are used, not their long original descriptions.
- This tracker makes **no applications automatically**. Open the official employer listing and confirm location, term, school year and work authorization.

## New in v2 — light theme, work-authorization signals, startup discovery
- The dashboard and generated CSV now contain separate **U.S. citizenship/U.S.-person**, **ITAR/EAR export rules**, **security clearance**, and **visa sponsorship** fields. ITAR is not a security clearance. These are *textual signals*, not legal determinations; **Not stated** does not mean eligible.
- A **Startup watch** filter highlights curated emerging companies. The monitored boards now include Antares, Northwood Space, Muon Space, Pacific Fusion, Revel, Etched, Hermeus, Shield AI, Xcimer, and others, in addition to existing larger employers. The community feeds can surface additional companies without manually adding their boards.
- It is impossible to monitor companies that have not yet published jobs or a public career page. New startups that become publicly listed through monitored job feeds can appear automatically; complete stealth-company discovery is not promised.
- Startup labels are a curated radar category, not verified company-size data.
- Compliance wording may change; always open the original posting. An export authorization path may exist but is not guaranteed or automatically granted. Work authorization as an international student is a separate issue.
