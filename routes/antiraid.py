from aiohttp import web
import aiohttp_jinja2
from db import get_db

antiraid_routes = web.RouteTableDef()

@antiraid_routes.get("/admin/antiraid")
@aiohttp_jinja2.template("antiraid.html")
async def antiraid_page(request):
    bot = request.app['bot']
    db = get_db()
    
    from aiohttp_session import get_session
    session = await get_session(request)
    guild_id = session.get("guild_id", 0)
    
    if guild_id:
        res = db.table("anti_raid_config").select("*").eq("guild_id", guild_id).execute()
        config_data = res.data[0] if res.data else None
    else:
        config_data = None
        
    config = config_data if config_data else {
        "guild_id": guild_id,
        "enabled": False,
        "account_age_min_days": 7,
        "join_rate_count": 5,
        "join_rate_window_seconds": 10,
        "penalty_action": "kick",
        "mute_duration_minutes": 60,
        "alert_channel_id": None,
        "quarantine_role_id": None
    }
    
    return {
        "active_page": "antiraid",
        "config": config,
        "channels": [{"id": c.id, "name": c.name} for g in bot.guilds if g.id == guild_id for c in g.text_channels],
        "roles": [{"id": r.id, "name": r.name} for g in bot.guilds if g.id == guild_id for r in g.roles if not r.is_default()],
        "role": session.get("role", "GUILD_ADMIN")
    }

@antiraid_routes.post("/admin/api/antiraid")
async def update_antiraid(request):
    from aiohttp_session import get_session
    session = await get_session(request)
    guild_id = session.get("guild_id")
    if not guild_id: return web.json_response({"error": "Unauthorized"}, status=401)
    
    data = await request.json()
    db = get_db()
    
    try:
        # Cast types correctly
        payload = {
            "guild_id": guild_id,
            "enabled": bool(data.get("enabled")),
            "account_age_min_days": int(data.get("account_age_min_days", 7)),
            "join_rate_count": int(data.get("join_rate_count", 5)),
            "join_rate_window_seconds": int(data.get("join_rate_window_seconds", 10)),
            "penalty_action": data.get("penalty_action", "kick"),
            "mute_duration_minutes": int(data.get("mute_duration_minutes", 60)),
            "alert_channel_id": int(data.get("alert_channel_id")) if data.get("alert_channel_id") else None,
            "quarantine_role_id": int(data.get("quarantine_role_id")) if data.get("quarantine_role_id") else None
        }
        
        db.table("anti_raid_config").upsert(payload).execute()
        return web.json_response({"success": True})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=400)
