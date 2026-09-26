# Pipeline Health Dashboard

Static, dependency-free dashboard (HTML/CSS/JS + Chart.js via CDN) for the
transaction data pipeline. No backend, no server — it reads `data.json`
in the same folder.

## Files

- `index.html`, `style.css`, `script.js` — the dashboard itself.
- `data.json` — sample data (30 days, fabricated) so it renders out of the box.
- `generate_sample_data.py` — regenerates that sample data if you want to tweak it.
- `export_data.py` — the **real** export: run this against your actual
  Postgres tables (`pipeline_health`, `transaction_summary`,
  `flagged_transactions`) to produce a real `data.json`. Adjust the SQL to
  match your actual column names if they differ from what's assumed here.

## Local preview

```bash
cd dashboard
python3 -m http.server 8000
# open http://localhost:8000
```//

(`fetch('data.json')` needs an HTTP server, not a `file://` URL.)

## Wiring it into your pipeline

At the end of your pipeline run (wherever you currently write to
`pipeline_health` / `transaction_summary` / `flagged_transactions`), add:

```bash
python3 export_data.py   # writes dashboard/data.json
git add dashboard/data.json
git commit -m "Update dashboard data"
git push
```

That's the whole "refresh" mechanism — new commit, new data.json, site
redeploys automatically (see below), dashboard shows the latest run.

## Deploying (free, no credit card)

**Option A — Vercel (recommended):**
1. Push this `dashboard/` folder into your GitHub repo.
2. Go to vercel.com → sign in with GitHub → "Add New Project" → pick the repo.
3. Set the project's root directory to `dashboard` (since it's a subfolder).
4. Framework preset: "Other" (it's static, no build step needed).
5. Deploy. Every push to `main` auto-redeploys.

**Option B — GitHub Pages:**
1. Push this `dashboard/` folder into your repo.
2. Repo → Settings → Pages → Source: "Deploy from a branch" → pick `main`
   and set the folder to `/dashboard` (or move these files to `/docs` if
   your repo requires that folder name).
3. Your dashboard is live at `https://<username>.github.io/<repo>/`.

Either way: no card, no server, updates by just pushing new commits.

## Extending it

- `data.json`'s shape is documented at the top of `export_data.py` — add a
  new table/metric there and a new panel in `index.html` + `script.js`
  following the existing pattern (one Chart.js instance per canvas).
- Colors and theme tokens are all in the `:root` block at the top of
  `style.css` if you want to adjust the palette.
