# AllMoviesPro – Telegram Bot (Render-ready)

Legal movie/series finder with TMDB + Internet Archive. Indian region defaults.

## Features
- /search <query> (movie/tv) with poster + details
- Buttons: Where to Watch (IN), Trailer, Public-Domain Downloads (Internet Archive), Recommendations
- /trending (TMDB trending movies + TV)
- Admin: /broadcast <msg>, /stats
- Legal only. No pirated sources.

## Quick Start (Local)
1. Python 3.10+
2. `pip install -r requirements.txt`
3. Set env vars (create `.env` from sample below)
4. `python bot.py`

### .env sample
```
BOT_TOKEN=123456:abc-your-telegram-bot-token
TMDB_API_KEY=your_tmdb_api_key
ADMIN_USER_IDS=7284469492,6377251819
WATCH_REGION=IN
APP_BRAND=AllMoviesPro
APP_TAGLINE=Powered by Empire Movies
```

## Deploy to Render (Free)
1. Create a new **GitHub repo** and upload all files in this folder.
2. Go to **Render Dashboard → New → Blueprint**.
3. Paste your repo URL. Render reads `render.yaml` and creates a Worker.
4. On first deploy, set **Environment Variables**: `BOT_TOKEN`, `TMDB_API_KEY`.
5. Click **Apply** → **Deploy**. The worker starts and your bot goes live 24×7.

## Commands
- `/start` → Welcome + instructions
- `/search Inception`
- `/trending`
- `/broadcast Hello users!` (admins only)
- `/stats`

## Notes
- Internet Archive links are limited to items exposing a `licenseurl`. Verify rights before use.
- For provider availability, TMDB data may change by region and time.
