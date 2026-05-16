from aiohttp import web
import aiohttp_jinja2
from db import get_db
from aiohttp_session import get_session

games_routes = web.RouteTableDef()

@games_routes.get("/admin/games")
@aiohttp_jinja2.template("games.html")
async def games_page(request):
    session = await get_session(request)
    guild_id = session.get("guild_id")
    role = session.get("role", "GUILD_ADMIN")
    db = get_db()
    
    # Minesweeper Stats
    if guild_id:
        ms_res = db.table("minesweeper_sessions").select("difficulty, won, score").eq("guild_id", guild_id).execute()
        ms_data = ms_res.data if ms_res.data else []
    else:
        ms_data = []
    
    ms_stats = {
        "total_games": len(ms_data),
        "wins": len([g for g in ms_data if g['won']]),
        "avg_score": sum([g['score'] for g in ms_data]) / len(ms_data) if ms_data else 0
    }
    
    # Roulette Stats
    if guild_id:
        rr_res = db.table("roulette_sessions").select("xp_pool").eq("guild_id", guild_id).execute()
        rr_data = rr_res.data if rr_res.data else []
    else:
        rr_data = []
    
    rr_stats = {
        "total_games": len(rr_data),
        "total_xp_circulated": sum([g['xp_pool'] for g in rr_data])
    }
    
    # Recent Activities
    if guild_id:
        recent_ms = db.table("minesweeper_sessions").select("*").eq("guild_id", guild_id).order("completed_at", desc=True).limit(10).execute()
        recent_rr = db.table("roulette_sessions").select("*").eq("guild_id", guild_id).order("ended_at", desc=True).limit(10).execute()
        recent_ms_data = recent_ms.data if recent_ms.data else []
        recent_rr_data = recent_rr.data if recent_rr.data else []
    else:
        recent_ms_data = []
        recent_rr_data = []
    
    return {
        "active_page": "games",
        "ms_stats": ms_stats,
        "rr_stats": rr_stats,
        "recent_ms": recent_ms_data,
        "recent_rr": recent_rr_data,
        "role": role
    }
