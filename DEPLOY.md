# Deploying this demo (frontend on Vercel, backend on Render)

The backend (`backend/`) is a FastAPI app that keeps state in a local SQLite
file, writes a disk cache, and runs background warm-up threads on startup.
That doesn't fit Vercel's stateless serverless functions, so it's deployed
as a normal persistent web service on Render instead. The frontend only
ever calls relative `/api/...` paths, so Vercel just needs to proxy those
through to the Render service — no frontend code changes needed.

## 1. Backend → Render

1. Go to https://dashboard.render.com → **New** → **Web Service** → connect
   this GitHub repo.
2. Settings (the free `render.yaml` in this repo tries to set these
   automatically as a Render "Blueprint" — if Render doesn't pick it up or
   rejects it, just set these manually):
   - **Root Directory:** `backend`
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `uvicorn pulpriot.api.main:app --host 0.0.0.0 --port $PORT`
   - **Plan:** Free
3. Deploy. First boot will take a bit longer — it reads
   `Pulp_Riot_Synthetic_Dataset.xlsx` (~10s) and warms its caches in a
   background thread. Render's free tier spins the service down when idle,
   so the *first* request after a period of inactivity can be slow
   (10-30s) while it spins back up — worth knowing before a live demo.
4. Once deployed, copy the service URL, e.g.
   `https://pulp-riot-backend-xxxx.onrender.com`.
5. Sanity check: open `https://<that-url>/api/health` — it should return
   `{"ok": true, ...}`.

## 2. Frontend → Vercel

1. Go to https://vercel.com → **Add New** → **Project** → import this repo.
2. Set **Root Directory** to `frontend`. Vercel should auto-detect it as a
   Vite project (build command `npm run build:fast` or `vite build`,
   output `dist`). If it doesn't auto-detect, set:
   - **Build Command:** `npm run build:fast`
   - **Output Directory:** `dist`
3. Before deploying (or right after, then redeploy), edit
   `frontend/vercel.json` and replace the placeholder with your real Render
   URL from step 1.5:
   ```json
   "destination": "https://pulp-riot-backend-xxxx.onrender.com/api/:path*"
   ```
4. Deploy. Vercel will build the React app and serve it; any request to
   `/api/*` on the Vercel domain gets forwarded to Render.

## 3. Verify

Open the Vercel URL, load the Control Tower page, and confirm it pulls
real data (not an error toast). If you see CORS or 502 errors, check the
Render service is awake (`/api/health`) and that the `vercel.json`
destination URL exactly matches the Render URL (no trailing slash
mismatch).

## Notes / things I'm not 100% sure about

- I haven't verified `render.yaml`'s exact key names against Render's
  *current* Blueprint schema — if it's rejected, the manual dashboard
  steps above cover the same settings.
- `npm_install.log` and a few other working files were dropped from the
  repo (gitignored) as they're not needed to build or run the app.
- The `.cache/` folder (precomputed JSON for various dates) was not
  carried over — the backend will recompute and re-cache on first use
  instead, so initial page loads on Render may be a few seconds slower
  than you saw running it locally with a warm cache.
