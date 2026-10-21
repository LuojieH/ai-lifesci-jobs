# AI × Life Science Jobs (US)

A GitHub Pages site listing AI/ML job openings at US drug-discovery, genomics, pathology,
medical-imaging, and pharma companies. A GitHub Action re-scrapes every day.

- `scraper/companies.json` lists the companies that get scraped. Edit it to add or remove companies.
- `scraper/scrape.py` pulls from public Greenhouse, Lever, Ashby, and Workday APIs. It keeps
  AI/ML titles in US locations and writes `docs/jobs.json`. It only uses the Python standard library.
- `docs/index.html` is the site, with search, sector/company filters, date range, and
  Imaging, Internship, Remote, and New tags. Filter state lives in the URL, so filtered views can be shared.
  The **Required experience** filter reads phrases like "PhD + 0–2 years or MS + 4 years" from each
  description and keeps the most lenient path. Hover a job's badge to see the sentence it came from.

## Deploy

1. Create a new GitHub repo, e.g. `ai-lifesci-jobs`, and push this folder:
   ```bash
   git init && git add . && git commit -m "Initial commit"
   git branch -M main
   git remote add origin https://github.com/YOUR_USERNAME/ai-lifesci-jobs.git
   git push -u origin main
   ```
2. In the repo, go to **Settings → Pages → Source: Deploy from a branch → `main` / `/docs`**.
3. Go to **Settings → Actions → General → Workflow permissions** and choose **Read and write**.
4. (Optional) Open **Actions → Scrape jobs → Run workflow** to refresh right away.

The site will be at `https://YOUR_USERNAME.github.io/ai-lifesci-jobs/`.

## Run locally

```bash
python3 scraper/scrape.py          # ~3 min, rewrites docs/jobs.json
python3 -m http.server -d docs     # open http://localhost:8000
```

## Adding a company

Find the company's careers page:

| URL looks like | Entry |
|---|---|
| `boards.greenhouse.io/<slug>` or `?gh_jid=` | `"ats": "greenhouse", "slug": "<slug>"` |
| `jobs.lever.co/<slug>` | `"ats": "lever", "slug": "<slug>"` |
| `jobs.ashbyhq.com/<slug>` | `"ats": "ashby", "slug": "<slug>"` |
| `<tenant>.wd5.myworkdayjobs.com/<Site>` | `"ats": "workday", "slug": "<tenant>/wd5/<Site>"` |

Options:
- `us_default: true` counts vague locations like "Remote" as US. Use it for US-based startups.
- `title_must_match_domain: true` keeps only titles that mention bio, health, or imaging. Use it for general tech companies.
- `queries: [...]` replaces the default Workday search terms.

Title and location filters are regexes at the top of `scrape.py`.
