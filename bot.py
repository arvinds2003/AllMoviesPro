#!/usr/bin/env python3
# AllMoviesPro – Legal Movie/Series Finder Bot (Render-ready)
# Features (legal only):
# - /start, /help
# - /search <q>  → TMDB search (movie/tv) + details page
# - Buttons: Where to Watch (IN), Trailer, Public-Domain Downloads (Internet Archive), Recommendations
# - /trending    → TMDB trending (movies & TV)
# - Admin: /broadcast <msg> (restricted), /stats
# Notes:
# - This bot intentionally DOES NOT fetch pirated links.
# - Internet Archive results are filtered to items exposing a license URL. Always verify license before use.
# - Runs as a long-polling worker on Render Free tier.

import os, html, asyncio
from typing import Dict, Optional, List

import aiohttp
from dotenv import load_dotenv

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.constants import ParseMode, ChatAction
from telegram.ext import (
    Application, ApplicationBuilder, CommandHandler,
    CallbackQueryHandler, MessageHandler, filters, ContextTypes
)

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
TMDB_API_KEY = os.getenv("TMDB_API_KEY", "").strip()
TMDB_BASE = "https://api.themoviedb.org/3"
TMDB_IMG = "https://image.tmdb.org/t/p/w500"
WATCH_REGION = os.getenv("WATCH_REGION", "IN").strip() or "IN"
ADMIN_USER_IDS = {int(x) for x in os.getenv("ADMIN_USER_IDS", "").replace(",", " ").split() if x.isdigit()}

APP_BRAND = os.getenv("APP_BRAND", "AllMoviesPro")
APP_TAGLINE = os.getenv("APP_TAGLINE", "Powered by Empire Movies")

# -------- HTTP helper ---------
class HTTP:
    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None
    async def session(self) -> aiohttp.ClientSession:
        if not self._session or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=25))
        return self._session
    async def get_json(self, url: str, params: Dict[str, str] = None, headers: Dict[str, str] = None):
        s = await self.session()
        async with s.get(url, params=params or {}, headers=headers or {}) as r:
            r.raise_for_status()
            return await r.json()
    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
http = HTTP()

# -------- TMDB helpers ---------
async def tmdb_search(query: str) -> List[Dict]:
    params = {"api_key": TMDB_API_KEY, "query": query, "include_adult": "false", "language": "en-US", "page": 1}
    data = await http.get_json(f"{TMDB_BASE}/search/multi", params)
    results = []
    for item in data.get("results", [])[:10]:
        media_type = item.get("media_type")
        if media_type not in ("movie", "tv"):
            continue
        title = item.get("title") or item.get("name")
        results.append({
            "id": item.get("id"),
            "media_type": media_type,
            "title": title,
            "year": (item.get("release_date") or item.get("first_air_date") or "")[:4],
            "poster": item.get("poster_path"),
        })
    return results

async def tmdb_details(media_type: str, tmdb_id: int) -> Dict:
    params = {"api_key": TMDB_API_KEY, "language": "en-US"}
    return await http.get_json(f"{TMDB_BASE}/{media_type}/{tmdb_id}", params)

async def tmdb_providers(media_type: str, tmdb_id: int, region: str = "IN") -> Dict:
    params = {"api_key": TMDB_API_KEY}
    data = await http.get_json(f"{TMDB_BASE}/{media_type}/{tmdb_id}/watch/providers", params)
    return data.get("results", {}).get(region, {})

async def tmdb_videos(media_type: str, tmdb_id: int) -> Optional[str]:
    params = {"api_key": TMDB_API_KEY}
    data = await http.get_json(f"{TMDB_BASE}/{media_type}/{tmdb_id}/videos", params)
    for v in data.get("results", []):
        if v.get("site") == "YouTube" and v.get("type") in ("Trailer", "Teaser"):
            key = v.get("key")
            return f"https://www.youtube.com/watch?v={key}"
    return None

async def tmdb_similar(media_type: str, tmdb_id: int) -> List[Dict]:
    params = {"api_key": TMDB_API_KEY, "language": "en-US", "page": 1}
    data = await http.get_json(f"{TMDB_BASE}/{media_type}/{tmdb_id}/similar", params)
    out = []
    for item in data.get("results", [])[:10]:
        title = item.get("title") or item.get("name")
        out.append({
            "id": item.get("id"),
            "media_type": media_type,
            "title": title,
            "year": (item.get("release_date") or item.get("first_air_date") or "")[:4],
            "poster": item.get("poster_path"),
        })
    return out

async def tmdb_trending() -> Dict[str, List[Dict]]:
    params = {"api_key": TMDB_API_KEY}
    movies = await http.get_json(f"{TMDB_BASE}/trending/movie/day", params)
    tv = await http.get_json(f"{TMDB_BASE}/trending/tv/day", params)
    def pack(items):
        out = []
        for it in items.get("results", [])[:10]:
            out.append({
                "id": it.get("id"),
                "media_type": "movie" if it.get("title") else "tv",
                "title": it.get("title") or it.get("name"),
                "year": (it.get("release_date") or it.get("first_air_date") or "")[:4],
                "poster": it.get("poster_path"),
            })
        return out
    return {"movies": pack(movies), "tv": pack(tv)}

# -------- Internet Archive (public-domain / CC-licensed) ---------
IA_SEARCH = "https://archive.org/advancedsearch.php"
IA_META = "https://archive.org/metadata/{identifier}"
VIDEO_EXTS = {"mp4", "m4v", "webm", "ogv"}

async def ia_search_public_domain(title: str, limit: int = 5) -> List[Dict]:
    q = f'title:("{title}") AND mediatype:(movies OR video) AND licenseurl:*'
    params = {
        "q": q, "fl[]": ["identifier", "title", "year", "licenseurl"],
        "sort[]": ["downloads desc"], "rows": str(limit), "page": "1", "output": "json",
    }
    data = await http.get_json(IA_SEARCH, params=params)
    docs = data.get("response", {}).get("docs", [])
    results = []
    for d in docs:
        ident = d.get("identifier")
        meta = await http.get_json(IA_META.format(identifier=ident))
        files = meta.get("files", [])
        file_links = []
        for f in files:
            name = f.get("name", "")
            ext = name.split(".")[-1].lower()
            if ext in VIDEO_EXTS and not name.endswith(".thumbs"):
                file_links.append(f"https://archive.org/download/{ident}/{name}")
        if file_links:
            results.append({
                "identifier": ident, "title": d.get("title"), "year": d.get("year"),
                "licenseurl": d.get("licenseurl"), "links": file_links[:5],
            })
    return results

# -------- Telegram Bot Handlers ---------
SPLASH = (
    "<b>{brand}</b>\n"
    "<i>{tag}</i>\n\n"
    "Yeh bot aapko movies/series ki <b>legal</b> information deta hai:\n"
    "• Search + details + poster\n"
    "• {region} me kaha stream ho rahi hai (legal providers)\n"
    "• Public-domain/CC licensed videos ke legal download links (Internet Archive)\n"
    "• Similar recommendations\n"
    "• Aaj ke trending (TMDB)\n\n"
    "Use: /search <movie ya series ka naam>\n"
    "Try: /trending"
)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_chat_action(ChatAction.TYPING)
    await update.message.reply_text(
        SPLASH.format(brand=html.escape(APP_BRAND), tag=html.escape(APP_TAGLINE), region=WATCH_REGION),
        parse_mode=ParseMode.HTML, disable_web_page_preview=True,
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Search ke liye: /search <query> — Example: /search Charlie Chaplin\nTrending: /trending")

async def search_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args).strip()
    if not query:
        await update.message.reply_text("Please type: /search <movie/series name>"); return
    if not TMDB_API_KEY:
        await update.message.reply_text("TMDB_API_KEY missing. Set env var and restart bot."); return
    await update.message.reply_chat_action(ChatAction.TYPING)
    try:
        results = await tmdb_search(query)
    except Exception as e:
        await update.message.reply_text(f"TMDB error: {e}"); return
    if not results:
        await update.message.reply_text("Koi result nahi mila."); return

    keyboard = []
    for r in results:
        title = f"{r['title']} ({r['year']})" if r.get('year') else r['title']
        cbdata = f"pick|{r['media_type']}|{r['id']}"
        keyboard.append([InlineKeyboardButton(title[:60], callback_data=cbdata)])
    await update.message.reply_text("Select one:", reply_markup=InlineKeyboardMarkup(keyboard))

async def on_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    try:
        _, media_type, id_str = q.data.split("|"); tmdb_id = int(id_str)
    except Exception:
        await q.edit_message_text("Invalid selection."); return
    try:
        details = await tmdb_details(media_type, tmdb_id)
    except Exception as e:
        await q.edit_message_text(f"TMDB error: {e}"); return

    title = details.get("title") or details.get("name")
    year = (details.get("release_date") or details.get("first_air_date") or "")[:4]
    overview = details.get("overview") or "No overview available."
    poster = details.get("poster_path")

    text = f"<b>{html.escape(title)}</b> ({html.escape(year)})\n\n{html.escape(overview)}"

    buttons = [
        [InlineKeyboardButton("Where to Watch (IN)", callback_data=f"prov|{media_type}|{tmdb_id}")],
        [InlineKeyboardButton("Trailer", callback_data=f"trailer|{media_type}|{tmdb_id}")],
        [InlineKeyboardButton("Public-Domain Downloads", callback_data=f"pd|{media_type}|{tmdb_id}|{title.replace('|',' ')}")],
        [InlineKeyboardButton("Recommendations", callback_data=f"rec|{media_type}|{tmdb_id}")],
    ]

    if poster:
        photo_url = f"{TMDB_IMG}{poster}"
        try:
            await q.message.reply_photo(photo=photo_url, caption=text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(buttons))
        except Exception:
            await q.edit_message_text(text=text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(buttons))
    else:
        await q.edit_message_text(text=text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(buttons))

async def on_providers(update: Update, context: ContextTypes.DEFAULT_TYPE, media_type: str, tmdb_id: int):
    try:
        prov = await tmdb_providers(media_type, tmdb_id, region=WATCH_REGION)
    except Exception as e:
        await update.callback_query.edit_message_text(f"Providers error: {e}"); return

    lines = [f"<b>Where to Watch ({WATCH_REGION})</b>"]
    def fmt(kind):
        items = prov.get(kind) or []
        names = [p.get("provider_name") for p in items if p.get("provider_name")]
        return ", ".join(sorted(set(names))) if names else "—"
    lines.append(f"Streaming: {html.escape(fmt('flatrate'))}")
    lines.append(f"Rent: {html.escape(fmt('rent'))}")
    lines.append(f"Buy: {html.escape(fmt('buy'))}")
    lines.append("\nNote: Availability can change. Check in your apps.")
    await update.callback_query.edit_message_text("\n".join(lines), parse_mode=ParseMode.HTML)

async def on_trailer(update: Update, context: ContextTypes.DEFAULT_TYPE, media_type: str, tmdb_id: int):
    url = await tmdb_videos(media_type, tmdb_id)
    if url:
        await update.callback_query.edit_message_text(f"Trailer: {url}")
    else:
        await update.callback_query.edit_message_text("Trailer not found.")

async def on_public_domain(update: Update, context: ContextTypes.DEFAULT_TYPE, title: str):
    await update.callback_query.edit_message_text("Searching Internet Archive (public-domain/CC) …")
    try:
        items = await ia_search_public_domain(title)
    except Exception as e:
        await update.callback_query.edit_message_text(f"IA error: {e}"); return
    if not items:
        await update.callback_query.edit_message_text("No public-domain/CC results found for this title."); return
    chunks = []
    for it in items:
        head = f"<b>{html.escape(it['title'] or 'Untitled')}</b> ({html.escape(str(it.get('year') or ''))})"
        lic = it.get("licenseurl") or ""
        link_lines = "\n".join(it["links"])
        chunks.append(f"{head}\nLicense: {html.escape(lic)}\n{link_lines}")
    msg = "\n\n".join(chunks) + "\n\nOnly share/use content permitted by the license."
    await update.callback_query.edit_message_text(msg, disable_web_page_preview=True, parse_mode=ParseMode.HTML)

async def on_recommend(update: Update, context: ContextTypes.DEFAULT_TYPE, media_type: str, tmdb_id: int):
    try:
        sims = await tmdb_similar(media_type, tmdb_id)
    except Exception as e:
        await update.callback_query.edit_message_text(f"Recommendation error: {e}"); return
    if not sims:
        await update.callback_query.edit_message_text("No similar titles found."); return
    lines = ["<b>Similar titles:</b>"]
    for s in sims[:10]:
        t = f"{s['title']} ({s.get('year','')})" if s.get("year") else s["title"]
        lines.append(f"• {html.escape(t)}")
    await update.callback_query.edit_message_text("\n".join(lines), parse_mode=ParseMode.HTML)

async def cmd_trending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not TMDB_API_KEY:
        await update.message.reply_text("TMDB_API_KEY missing. Set env var and restart bot."); return
    await update.message.reply_chat_action(ChatAction.TYPING)
    data = await tmdb_trending()
    def fmt(lst: List[Dict]) -> str:
        items = [f"• {html.escape((i['title']))} ({html.escape(i.get('year',''))})" for i in lst[:10]]
        return "\n".join(items) if items else "—"
    msg = "<b>Trending Now</b>\n\n<b>Movies:</b>\n" + fmt(data["movies"]) + "\n\n<b>TV:</b>\n" + fmt(data["tv"])
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

# --- Admin tools ---
def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_USER_IDS

async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_admin(user.id):
        await update.message.reply_text("Not authorized."); return
    msg = " ".join(context.args).strip()
    if not msg:
        await update.message.reply_text("Usage: /broadcast <message>"); return
    chats = context.bot_data.get("recent_chats", set())
    sent = 0
    for chat_id in list(chats):
        try:
            await context.bot.send_message(chat_id=chat_id, text=msg)
            sent += 1
        except Exception:
            pass
    await update.message.reply_text(f"Broadcast sent to ~{sent} chats.")

async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chats = context.bot_data.get("recent_chats", set())
    await update.message.reply_text(f"Known chats: {len(chats)} | Admins: {len(ADMIN_USER_IDS)}")

async def on_text_fallback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chats = context.bot_data.setdefault("recent_chats", set())
    if update.effective_chat: chats.add(update.effective_chat.id)
    q = (update.message.text or "").strip()
    if not q: return
    update.message.text = f"/search {q}"
    await search_cmd(update, context)

async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q or not q.data: return
    try:
        parts = q.data.split("|")
        tag = parts[0]
        if tag == "pick":
            await on_pick(update, context)
        elif tag == "prov":
            _, media_type, id_str = parts
            await on_providers(update, context, media_type, int(id_str))
        elif tag == "trailer":
            _, media_type, id_str = parts
            await on_trailer(update, context, media_type, int(id_str))
        elif tag == "pd":
            title = "|".join(parts[3:])
            await on_public_domain(update, context, title)
        elif tag == "rec":
            _, media_type, id_str = parts
            await on_recommend(update, context, media_type, int(id_str))
        else:
            await q.answer()
    except Exception as e:
        await q.edit_message_text(f"Error: {e}")

async def main():
    if not BOT_TOKEN or not TMDB_API_KEY:
        raise SystemExit("Please set BOT_TOKEN and TMDB_API_KEY environment variables. See README.md")
    app: Application = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("search", search_cmd))
    app.add_handler(CommandHandler("trending", cmd_trending))
    app.add_handler(CommandHandler("broadcast", cmd_broadcast))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CallbackQueryHandler(callback_router))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), on_text_fallback))
    try:
        await app.initialize()
        await app.start()
        print("Bot is running. Press Ctrl+C to stop.")
        await app.updater.start_polling()
        await asyncio.Event().wait()
    finally:
        await app.updater.stop()
        await app.stop()
        await http.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
