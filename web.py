import os
import time
import logging
import base64
import asyncio
import bcrypt
import discord
import aiohttp_jinja2
import jinja2
from aiohttp import web
from aiohttp_session import setup, get_session, session_middleware
from aiohttp_session.cookie_storage import EncryptedCookieStorage
from cryptography import fernet

from utils.settings_manager import load_settings, save_settings, get_presets, save_preset, delete_preset, rename_preset
from routes.xp import xp_routes
from routes.antiraid import antiraid_routes
from routes.filter import filter_routes
from routes.games import games_routes
from routes.mafia import mafia_routes
from db.client import get_db

log = logging.getLogger("web")
START_TIME = time.time()

# --- Helpers ---

async def auth_middleware(app, handler):
    async def middleware(request):
        if request.path == '/login' or request.path.startswith('/static/') or request.path.startswith('/mafia/'):
            return await handler(request)
        
        session = await get_session(request)
        if not session.get('logged_in'):
            if request.path.startswith('/admin/api/'):
                return web.json_response({"error": "Unauthorized"}, status=401)
            raise web.HTTPFound('/login')
        
        return await handler(request)
    return middleware

# --- Public Routes ---

public_routes = web.RouteTableDef()

@public_routes.get("/login")
@aiohttp_jinja2.template("login.html")
async def login_page(request):
    session = await get_session(request)
    if session.get('logged_in'):
        raise web.HTTPFound('/admin')
    return {}

@public_routes.post("/login")
@aiohttp_jinja2.template("login.html")
async def login_handler(request):
    data = await request.post()
    username = data.get("username")
    password = data.get("password")
    
    try:
        db = get_db()
        # Query admin users table
        res = await asyncio.to_thread(
            lambda: db.table("admin_users").select("*").eq("username", username).execute()
        )
        
        if res.data:
            admin = res.data[0]
            stored_hash = admin["password_hash"]
            
            # Verify bcrypt hash
            is_valid = await asyncio.to_thread(
                lambda: bcrypt.checkpw(password.encode('utf-8'), stored_hash.encode('utf-8'))
            )
            
            if is_valid:
                session = await get_session(request)
                session['logged_in'] = True
                session['user'] = username
                log.info(f"Admin '{username}' logged in successfully.")
                raise web.HTTPFound('/admin')
    except web.HTTPException:
        raise
    except Exception as e:
        log.error(f"Login error: {e}")
        return {"error": "A system error occurred. Please check logs."}
    
    return {"error": "Invalid credentials. Please try again."}

@public_routes.get("/logout")
async def logout_handler(request):
    session = await get_session(request)
    session.invalidate()
    raise web.HTTPFound('/login')

# --- Admin Routes ---

admin_routes = web.RouteTableDef()

@admin_routes.get("/admin")
@aiohttp_jinja2.template("dashboard.html")
async def dashboard(request):
    bot = request.app['bot']
    ai_cog = bot.get_cog("AI")
    rules_cog = bot.get_cog("Rules")
    
    settings = load_settings()
    stats = ai_cog.get_stats() if ai_cog else {
        'total_requests': 0, 'total_rate_limits': 0, 'active_throttled': 0
    }
    rules_text = rules_cog.get_rules_text() if rules_cog else ""
    
    telemetry = {
        "guild_count": len(bot.guilds),
        "latency": round(bot.latency * 1000),
        "uptime": round((time.time() - START_TIME) / 3600, 1),
        "total_requests": stats.get('total_requests', 0),
        "total_rate_limits": stats.get('total_rate_limits', 0),
        "active_throttled": stats.get('active_throttled', 0)
    }
    
    return {
        "active_page": "dashboard",
        "stats": telemetry,
        "settings": settings,
        "presets": get_presets(),
        "rules_text": rules_text
    }

@admin_routes.get("/admin/mafia")
@aiohttp_jinja2.template("admin_mafia.html")
async def admin_mafia(request):
    from db.local_db import get_config
    bot = request.app['bot']
    from cogs.mafia_cog import _sessions, _web_sessions
    
    # Pack active games for the table
    games = []
    for session_id, guild_id in _web_sessions.items():
        session = _sessions.get(guild_id)
        if session:
            guild = bot.get_guild(guild_id)
            games.append({
                "session_id": session_id,
                "guild_name": guild.name if guild else "Unknown",
                "player_count": len(session.players),
                "phase": session.phase,
                "round": session.round
            })

    return {
        "active_page": "mafia",
        "games": games,
        "base_url": get_config("base_url"),
        "max_players": get_config("max_players"),
        "night_duration": get_config("night_duration"),
        "day_duration": get_config("day_duration"),
        "jester_enabled": get_config("jester_enabled") == "true",
        "framer_enabled": get_config("framer_enabled") == "true",
        "allowed_guilds": get_config("allowed_guilds")
    }

# --- Admin API Routes ---

@admin_routes.post("/admin/api/settings")
async def update_settings_api(request):
    data = await request.json()
    bot = request.app['bot']
    
    settings = load_settings()
    settings.update(data)
    save_settings(settings)
    
    # Live update cogs
    ai_cog = bot.get_cog("AI")
    if ai_cog:
        ai_cog.update_settings(
            settings.get('rate_limit_count', 5), 
            settings.get('rate_limit_window', 60), 
            settings.get('system_prompt', ""), 
            settings.get('ai_enabled', True), 
            settings.get('reaction_chance', 100), 
            settings.get('interception_chance', 5), 
            settings.get('interception_keywords', "")
        )
            
    welcome_cog = bot.get_cog("Welcome")
    if welcome_cog:
        welcome_cog.welcoming_enabled = settings.get('welcome_enabled', True)
        
    return web.json_response({"success": True})

@admin_routes.post("/admin/api/rules")
async def update_rules_api(request):
    data = await request.json()
    bot = request.app['bot']
    rules_cog = bot.get_cog("Rules")
    
    if rules_cog:
        rules_text = data.get("rules_text", "")
        bot.loop.create_task(rules_cog.update_rules_text(rules_text))
        return web.json_response({"success": True})
    
    return web.json_response({"error": "Rules cog not found"}, status=500)

@admin_routes.post("/admin/api/presets")
async def save_preset_api(request):
    data = await request.json()
    name = data.get("name")
    prompt = data.get("prompt")
    if name and prompt:
        save_preset(name, prompt)
        return web.json_response({"success": True})
    return web.json_response({"error": "Missing name or prompt"}, status=400)

@admin_routes.post("/admin/api/announce_live")
async def announce_live_api(request):
    data = await request.json()
    bot = request.app['bot']
    
    msg_out = data.get("message_content", "").strip()
    streamer_name = data.get("streamer_name", "Live Stream!")
    game_name = data.get("game", "")
    title = data.get("title", "🚨 WE ARE LIVE! 🚨")
    link = data.get("link", "")
    image_url = data.get("image_url", "").strip()
    avatar_url = data.get("avatar_url", "").strip()
    ping_type = data.get("ping_type", "none")
    role_id = data.get("role_id", "").strip()
    
    target_channel_id = 1482736373130727536
    channel = bot.get_channel(target_channel_id)
    
    if channel:
        content = ""
        if ping_type == "everyone":
            content += "@everyone\n"
        elif ping_type == "role" and role_id.isdigit():
            content += f"<@&{role_id}>\n"
        if msg_out:
            content += f"\n{msg_out}"
            
        embed = discord.Embed(title=title, url=link, color=0x6441a5)
        icon = avatar_url if avatar_url else (bot.user.avatar.url if bot.user.avatar else None)
        embed.set_author(name=streamer_name, icon_url=icon)
        if game_name: embed.add_field(name="Game", value=game_name)
        if image_url: embed.set_image(url=image_url)
        embed.set_footer(text="streamcord.io • Admin Action")
        
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="Watch Stream", url=link))
        
        bot.loop.create_task(channel.send(content=content.strip(), embed=embed, view=view))
        return web.json_response({"success": True})
    
    return web.json_response({"error": "Channel not found"}, status=500)

@admin_routes.post("/admin/api/improve_text")
async def improve_text_api(request):
    data = await request.json()
    text = data.get("text", "")
    bot = request.app['bot']
    ai_cog = bot.get_cog("AI")
    
    if not ai_cog: return web.json_response({"error": "AI cog not loaded"}, status=500)
    client = ai_cog.get_client()
    if not client: return web.json_response({"error": "No API keys"}, status=500)
         
    prompt = f"Rewrite to be hype/engaging for Discord. Output ONLY the improved text. Text: {text}"
    
    import asyncio
    from google.genai import types
    config = types.GenerateContentConfig(temperature=0.8)
    try:
        response = await asyncio.to_thread(
            client.models.generate_content,
            model='gemini-3.1-flash-lite-preview',
            contents=prompt,
            config=config
        )
        return web.json_response({"improved_text": response.text})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

@admin_routes.post("/admin/api/mafia/settings")
async def update_mafia_settings(request):
    # Check session (strict)
    session = await get_session(request)
    if not session.get('logged_in'):
         return web.json_response({"error": "Unauthorized"}, status=401)

    data = await request.json()
    from db.local_db import set_config
    for k, v in data.items():
        set_config(k, str(v))
    
    return web.json_response({"success": True})

@admin_routes.post("/admin/api/mafia/force_end/{session_id}")
async def force_end_mafia(request):
    session_id = request.match_info['session_id']
    from cogs.mafia_cog import _web_sessions
    guild_id = _web_sessions.get(session_id)
    if guild_id:
        bot = request.app['bot']
        cog = bot.get_cog("MafiaCog")
        if cog:
            await cog._force_end_session(guild_id)
            return web.json_response({"success": True})
    
    return web.json_response({"error": "Session not found"}, status=404)

# --- App Factory ---

def create_app(bot):
    app = web.Application()
    app['bot'] = bot
    
    # Setup Session
    secret_key = os.getenv("WEB_SECRET_KEY")
    if secret_key:
        if len(secret_key) < 32:
            secret_key = secret_key.ljust(32, '0')
        fernet_key = secret_key[:32].encode()
    else:
        # Secure generation if key is missing
        log.warning("WEB_SECRET_KEY missing in .env! Generating a volatile random key for this session.")
        fernet_key = os.urandom(32)
        
    setup(app, EncryptedCookieStorage(fernet_key))
    
    # Setup Jinja2 with autoescape enabled for XSS protection
    aiohttp_jinja2.setup(
        app, 
        loader=jinja2.FileSystemLoader('templates'),
        autoescape=jinja2.select_autoescape(['html', 'xml'])
    )
    
    # Auth Middleware
    app.middlewares.append(auth_middleware)
    
    # Static files
    app.router.add_static('/static/', path='static', name='static')
    
    # Add routes
    app.add_routes(public_routes)
    app.add_routes(admin_routes)
    app.add_routes(xp_routes)
    app.add_routes(antiraid_routes)
    app.add_routes(filter_routes)
    app.add_routes(games_routes)
    app.add_routes(mafia_routes)
    
    return app
