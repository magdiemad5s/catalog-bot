from aiohttp import web
import aiohttp_jinja2
from db import get_db
from aiohttp_session import get_session

xp_routes = web.RouteTableDef()

@xp_routes.get("/admin/xp")
@aiohttp_jinja2.template("xp_boost.html")
async def xp_boost_page(request):
    session = await get_session(request)
    guild_id = session.get("guild_id")
    role = session.get("role", "GUILD_ADMIN")
    
    db = get_db()
    if guild_id:
        res = db.table("xp_role_boosts").select("*").eq("guild_id", guild_id).execute()
        boosts = res.data if res.data else []
    else:
        boosts = []
    
    return {
        "active_page": "xp",
        "boosts": boosts,
        "role": role
    }

@xp_routes.post("/admin/api/xp/boosts")
async def add_boost(request):
    session = await get_session(request)
    guild_id = session.get("guild_id")
    if not guild_id: return web.json_response({"error": "No guild context"}, status=400)
    
    data = await request.json()
    db = get_db()
    
    try:
        role_id = int(data.get("role_id"))
        multiplier = float(data.get("multiplier", 1.1))
        label = data.get("label", "New Boost")
        
        db.table("xp_role_boosts").upsert({
            "guild_id": guild_id,
            "role_id": role_id,
            "multiplier": multiplier,
            "label": label,
            "enabled": True
        }).execute()
        
        return web.json_response({"success": True})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=400)

@xp_routes.delete("/admin/api/xp/boosts/{role_id}")
async def delete_boost(request):
    session = await get_session(request)
    guild_id = session.get("guild_id")
    role_id = request.match_info['role_id']
    db = get_db()
    
    try:
        db.table("xp_role_boosts").delete().eq("role_id", int(role_id)).eq("guild_id", guild_id).execute()
        return web.json_response({"success": True})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=400)

@xp_routes.post("/admin/api/xp/boosts/toggle")
async def toggle_boost(request):
    session = await get_session(request)
    guild_id = session.get("guild_id")
    data = await request.json()
    role_id = data.get("role_id")
    enabled = data.get("enabled")
    db = get_db()
    
    try:
        db.table("xp_role_boosts").update({"enabled": enabled}).eq("role_id", int(role_id)).eq("guild_id", guild_id).execute()
        return web.json_response({"success": True})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=400)
