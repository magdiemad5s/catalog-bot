from aiohttp import web
import aiohttp_jinja2
from db import get_db

filter_routes = web.RouteTableDef()

@filter_routes.get("/admin/filter")
@aiohttp_jinja2.template("filter.html")
async def filter_page(request):
    bot = request.app['bot']
    db = get_db()
    from aiohttp_session import get_session
    session = await get_session(request)
    guild_id = session.get("guild_id", 0)
    # Get config
    res_config = db.table("filter_config").select("*").eq("guild_id", guild_id).execute()
    config = res_config.data[0] if res_config.data else {
        "guild_id": guild_id, "enabled": False, "active_profile_id": None, "threshold": 3, "mod_channel_id": None
    }
    
    # Get profiles
    res_profiles = db.table("filter_profiles").select("*").eq("guild_id", guild_id).execute()
    profiles = res_profiles.data if res_profiles.data else []
    
    # Get logs (top 50)
    res_logs = db.table("filter_log").select("*").eq("guild_id", guild_id).order("created_at", desc=True).limit(50).execute()
    logs = res_logs.data if res_logs.data else []
    
    return {
        "active_page": "filter",
        "config": config,
        "profiles": profiles,
        "logs": logs,
        "channels": [{"id": c.id, "name": c.name} for g in bot.guilds if g.id == guild_id for c in g.text_channels]
    }

@filter_routes.post("/admin/api/filter/config")
async def update_filter_config(request):
    from aiohttp_session import get_session
    session = await get_session(request)
    guild_id = session.get("guild_id")
    if not guild_id: return web.json_response({"error": "Unauthorized"}, status=401)
    
    data = await request.json()
    db = get_db()
    try:
        payload = {
            "guild_id": guild_id,
            "enabled": bool(data.get("enabled")),
            "threshold": int(data.get("threshold", 3)),
            "active_profile_id": int(data.get("active_profile_id")) if data.get("active_profile_id") else None,
            "mod_channel_id": int(data.get("mod_channel_id")) if data.get("mod_channel_id") else None
        }
        db.table("filter_config").upsert(payload).execute()
        return web.json_response({"success": True})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=400)

@filter_routes.post("/admin/api/filter/profiles")
async def create_profile(request):
    from aiohttp_session import get_session
    session = await get_session(request)
    guild_id = session.get("guild_id")
    if not guild_id: return web.json_response({"error": "Unauthorized"}, status=401)
    
    data = await request.json()
    db = get_db()
    try:
        payload = {
            "guild_id": guild_id,
            "name": data.get("name"),
            "word_list": data.get("word_list", [])
        }
        db.table("filter_profiles").insert(payload).execute()
        return web.json_response({"success": True})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=400)

@filter_routes.put("/admin/api/filter/profiles/{id}")
async def update_profile(request):
    profile_id = request.match_info['id']
    data = await request.json()
    db = get_db()
    try:
        db.table("filter_profiles").update({
            "name": data.get("name"),
            "word_list": data.get("word_list")
        }).eq("id", int(profile_id)).execute()
        return web.json_response({"success": True})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=400)

@filter_routes.delete("/admin/api/filter/profiles/{id}")
async def delete_profile(request):
    profile_id = request.match_info['id']
    db = get_db()
    try:
        db.table("filter_profiles").delete().eq("id", int(profile_id)).execute()
        return web.json_response({"success": True})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=400)
