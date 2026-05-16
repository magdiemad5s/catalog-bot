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
        
        if (session.get('requires_setup') or session.get('requires_password_change')) and not request.path.startswith('/admin/setup') and not request.path.startswith('/admin/api/setup'):
            if request.path.startswith('/admin/api/'):
                return web.json_response({"error": "Setup required. Please complete onboarding."}, status=403)
            raise web.HTTPFound('/admin/setup')
        
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
                session['guild_id'] = admin.get("guild_id")
                session['role'] = admin.get("role", "GUILD_ADMIN")
                session['requires_setup'] = admin.get("requires_setup", False)
                session['requires_password_change'] = admin.get("requires_password_change", False)
                log.info(f"Admin '{username}' logged in successfully.")
                
                if session['requires_setup'] or session['requires_password_change']:
                    raise web.HTTPFound('/admin/setup')
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

@admin_routes.get("/admin/setup")
@aiohttp_jinja2.template("setup.html")
async def setup_page(request):
    session = await get_session(request)
    if not session.get("requires_setup") and not session.get("requires_password_change"):
        raise web.HTTPFound('/admin')
    
    bot = request.app['bot']
    guild_id = session.get("guild_id")
    guild = bot.get_guild(guild_id) if guild_id else None
    channels = [{"id": c.id, "name": c.name} for c in guild.text_channels] if guild else []
    
    return {
        "requires_setup": session.get("requires_setup", False),
        "requires_password_change": session.get("requires_password_change", False),
        "channels": channels
    }

@admin_routes.get("/admin")
@aiohttp_jinja2.template("dashboard.html")
async def dashboard(request):
    bot = request.app['bot']
    session = await get_session(request)
    guild_id = session.get("guild_id")
    guild = bot.get_guild(guild_id) if guild_id else None
    
    ai_cog = bot.get_cog("AI")
    rules_cog = bot.get_cog("Rules")
    
    db = get_db()
    res_config = db.table("guild_configs").select("*").eq("guild_id", guild_id).execute()
    guild_config = res_config.data[0] if res_config.data else {}
    
    channels = [{"id": c.id, "name": c.name} for c in guild.text_channels] if guild else []
    
    settings = load_settings(guild_id)
    stats = ai_cog.get_stats() if ai_cog else {
        'total_requests': 0, 'total_rate_limits': 0, 'active_throttled': 0
    }
    rules_text = rules_cog.get_rules_text() if rules_cog else ""
    
    telemetry = {
        "guild_count": 1 if guild else 0,
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
        "presets": get_presets(guild_id),
        "rules_text": rules_text,
        "guild_name": guild.name if guild else "Unknown Server",
        "role": session.get("role", "GUILD_ADMIN"),
        "channels": channels,
        "guild_config": guild_config
    }

@admin_routes.get("/admin/announcements")
@aiohttp_jinja2.template("announcements.html")
async def admin_announcements(request):
    return {"active_page": "announcements"}

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

@admin_routes.post("/admin/api/setup")
async def setup_api(request):
    session = await get_session(request)
    guild_id = session.get("guild_id")
    username = session.get("user")
    
    data = await request.json()
    db = get_db()
    
    try:
        if session.get("requires_password_change"):
            new_password = data.get("new_password")
            if not new_password or len(new_password) < 6:
                return web.json_response({"error": "Password must be at least 6 characters."}, status=400)
            
            import bcrypt
            hashed = await asyncio.to_thread(lambda: bcrypt.hashpw(new_password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8'))
            db.table("admin_users").update({"password_hash": hashed, "requires_password_change": False}).eq("username", username).execute()
            session["requires_password_change"] = False
            
        if session.get("requires_setup"):
            payload = {
                "guild_id": guild_id,
                "announcement_channel_id": int(data.get("announcement_channel_id")) if data.get("announcement_channel_id") else None,
                "rules_channel_id": int(data.get("rules_channel_id")) if data.get("rules_channel_id") else None,
                "mod_channel_id": int(data.get("mod_channel_id")) if data.get("mod_channel_id") else None,
                "card_channel_id": int(data.get("card_channel_id")) if data.get("card_channel_id") else None,
                "giveaway_channel_id": int(data.get("giveaway_channel_id")) if data.get("giveaway_channel_id") else None
            }
            db.table("guild_configs").upsert(payload).execute()
            db.table("admin_users").update({"requires_setup": False}).eq("username", username).execute()
            session["requires_setup"] = False
            
        return web.json_response({"success": True})
    except Exception as e:
        log.error(f"Setup error: {e}")
        return web.json_response({"error": str(e)}, status=400)

# --- SuperAdmin Routes ---

@admin_routes.get("/superadmin")
@aiohttp_jinja2.template("superadmin.html")
async def superadmin_page(request):
    session = await get_session(request)
    if session.get("role") != "SUPER_ADMIN":
        raise web.HTTPFound('/admin')
        
    bot = request.app['bot']
    db = get_db()
    
    # Fetch all configs
    res_configs = db.table("guild_configs").select("*").execute()
    configs = {row["guild_id"]: row for row in res_configs.data} if res_configs.data else {}
    
    # Fetch all admins
    res_admins = db.table("admin_users").select("id, username, role, guild_id, requires_setup, requires_password_change").execute()
    admins = res_admins.data if res_admins.data else []
    
    servers = []
    for guild in bot.guilds:
        c = configs.get(guild.id, {})
        server_admins = [a for a in admins if a.get("guild_id") == guild.id]
        servers.append({
            "id": guild.id,
            "name": guild.name,
            "member_count": guild.member_count,
            "is_enabled": c.get("is_enabled", True),
            "admins": server_admins
        })
        
    return {
        "active_page": "superadmin",
        "servers": servers,
        "total_guilds": len(bot.guilds),
        "total_users": sum(g.member_count for g in bot.guilds),
        "role": "SUPER_ADMIN"
    }

@admin_routes.post("/superadmin/api/toggle_guild")
async def toggle_guild_api(request):
    session = await get_session(request)
    if session.get("role") != "SUPER_ADMIN":
        return web.json_response({"error": "Unauthorized"}, status=403)
        
    data = await request.json()
    guild_id = data.get("guild_id")
    is_enabled = data.get("is_enabled")
    
    if not guild_id:
        return web.json_response({"error": "Missing guild_id"}, status=400)
        
    db = get_db()
    try:
        db.table("guild_configs").upsert({"guild_id": int(guild_id), "is_enabled": bool(is_enabled)}).execute()
        return web.json_response({"success": True})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

@admin_routes.post("/superadmin/api/reset_account")
async def reset_account_api(request):
    session = await get_session(request)
    if session.get("role") != "SUPER_ADMIN":
        return web.json_response({"error": "Unauthorized"}, status=403)
        
    data = await request.json()
    username = data.get("username")
    
    if not username:
        return web.json_response({"error": "Missing username"}, status=400)
        
    db = get_db()
    try:
        db.table("admin_users").update({"requires_password_change": True, "requires_setup": True}).eq("username", username).execute()
        return web.json_response({"success": True})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

@admin_routes.post("/superadmin/api/delete_account")
async def delete_account_api(request):
    session = await get_session(request)
    if session.get("role") != "SUPER_ADMIN":
        return web.json_response({"error": "Unauthorized"}, status=403)
        
    data = await request.json()
    username = data.get("username")
    
    if not username:
        return web.json_response({"error": "Missing username"}, status=400)
        
    db = get_db()
    try:
        db.table("admin_users").delete().eq("username", username).execute()
        return web.json_response({"success": True})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

@admin_routes.post("/superadmin/api/update")
async def update_bot_api(request):
    session = await get_session(request)
    if session.get("role") != "SUPER_ADMIN":
        return web.json_response({"error": "Unauthorized"}, status=403)
        
    import subprocess
    try:
        result = subprocess.run(["git", "pull"], capture_output=True, text=True, check=True)
        out = result.stdout.strip()
        
        # If there's an update, the PM2/systemd or nodemon would ideally restart it.
        # But we'll just return the output for now.
        return web.json_response({"success": True, "output": out})
    except subprocess.CalledProcessError as e:
        err = e.stderr.strip() or e.stdout.strip()
        return web.json_response({"error": f"Git pull failed: {err}"}, status=500)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

@admin_routes.post("/admin/api/channels")
async def update_channels_api(request):
    session = await get_session(request)
    guild_id = session.get("guild_id")
    if not guild_id:
        return web.json_response({"error": "No guild context"}, status=400)
        
    data = await request.json()
    db = get_db()
    
    payload = {
        "guild_id": guild_id,
        "announcement_channel_id": int(data.get("announcement_channel_id")) if data.get("announcement_channel_id") else None,
        "rules_channel_id": int(data.get("rules_channel_id")) if data.get("rules_channel_id") else None,
        "mod_channel_id": int(data.get("mod_channel_id")) if data.get("mod_channel_id") else None,
        "card_channel_id": int(data.get("card_channel_id")) if data.get("card_channel_id") else None,
        "giveaway_channel_id": int(data.get("giveaway_channel_id")) if data.get("giveaway_channel_id") else None
    }
    
    try:
        db.table("guild_configs").upsert(payload).execute()
        return web.json_response({"success": True})
    except Exception as e:
        log.error(f"Failed to save channel config: {e}")
        return web.json_response({"error": str(e)}, status=500)

@admin_routes.post("/admin/api/settings")
async def update_settings_api(request):
    session = await get_session(request)
    guild_id = session.get("guild_id")
    if not guild_id: return web.json_response({"error": "Unauthorized"}, status=401)
    
    data = await request.json()
    bot = request.app['bot']
    
    settings = load_settings(guild_id)
    settings.update(data)
    save_settings(settings, guild_id)
    
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
    session = await get_session(request)
    guild_id = session.get("guild_id")
    if not guild_id: return web.json_response({"error": "Unauthorized"}, status=401)
    
    data = await request.json()
    bot = request.app['bot']
    rules_cog = bot.get_cog("Rules")
    
    if rules_cog:
        rules_text = data.get("rules_text", "")
        bot.loop.create_task(rules_cog.update_rules_text(guild_id, rules_text))
        return web.json_response({"success": True})
    
    return web.json_response({"error": "Rules cog not found"}, status=500)

@admin_routes.post("/admin/api/presets")
async def save_preset_api(request):
    session = await get_session(request)
    guild_id = session.get("guild_id")
    data = await request.json()
    name = data.get("name")
    prompt = data.get("prompt")
    if name and prompt and guild_id:
        save_preset(guild_id, name, prompt)
        return web.json_response({"success": True})
    return web.json_response({"error": "Missing name, prompt, or guild context"}, status=400)

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
    
    session = await get_session(request)
    guild_id = session.get("guild_id")
    db = get_db()
    target_channel_id = None
    try:
        res = db.table("guild_configs").select("announcement_channel_id").eq("guild_id", guild_id).execute()
        if res.data and res.data[0].get("announcement_channel_id"):
            target_channel_id = int(res.data[0]["announcement_channel_id"])
    except Exception:
        pass
        
    channel = bot.get_channel(target_channel_id) if target_channel_id else None
    
    
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

@admin_routes.get("/admin/api/emojis")
async def get_emojis_api(request):
    bot = request.app['bot']
    emojis = [{"name": e.name, "id": str(e.id), "url": e.url, "animated": e.animated} for e in bot.emojis]
    return web.json_response(emojis)

@admin_routes.post("/admin/api/post_announcement")
async def post_announcement_api(request):
    # Support multipart/form-data for file uploads
    data = await request.post()
    bot = request.app['bot']
    
    mode = data.get("mode", "embed")
    normal_content = data.get("normal_content", "")
    embed_title = data.get("title", "")
    embed_content = data.get("content", "")
    
    ping_type = data.get("ping_type", "none")
    role_id = data.get("role_id", "").strip()
    color_hex = data.get("color", "#6366f1")
    image_url = data.get("image_url", "").strip()
    
    session = await get_session(request)
    guild_id = session.get("guild_id")
    db = get_db()
    target_channel_id = None
    try:
        res = db.table("guild_configs").select("announcement_channel_id").eq("guild_id", guild_id).execute()
        if res.data and res.data[0].get("announcement_channel_id"):
            target_channel_id = int(res.data[0]["announcement_channel_id"])
    except Exception:
        pass
        
    channel = bot.get_channel(target_channel_id) if target_channel_id else None
    
    if channel:
        import io
        
        ping_text = ""
        if ping_type == "everyone":
            ping_text = "@everyone\n"
        elif ping_type == "role" and role_id.isdigit():
            ping_text = f"<@&{role_id}>\n"
            
        files = []
        attachments = data.getall("attachments", [])
        for attachment in attachments:
            if hasattr(attachment, 'filename') and attachment.filename:
                file_content = attachment.file.read()
                files.append(discord.File(fp=io.BytesIO(file_content), filename=attachment.filename))
                
        embed = None
        message_content = ping_text
        
        if mode in ["embed", "both"]:
            embed = discord.Embed(title=embed_title, description=embed_content, color=int(color_hex.replace("#", ""), 16))
            if image_url:
                embed.set_image(url=image_url)
                
        if mode in ["text", "both"]:
            if normal_content:
                message_content += f"\n{normal_content}"
                
        bot.loop.create_task(channel.send(content=message_content.strip(), embed=embed, files=files))
        return web.json_response({"success": True})
    
    return web.json_response({"error": "Channel not found. Ensure bot has access."}, status=500)

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
