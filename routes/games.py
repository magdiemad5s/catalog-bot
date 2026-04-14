from aiohttp import web
import aiohttp_jinja2
from db import get_db

games_routes = web.RouteTableDef()

@games_routes.get("/admin/games")
@aiohttp_jinja2.template("games.html")
async def games_page(request):
    db = get_db()
    
    # Minesweeper Stats
    ms_res = db.table("minesweeper_sessions").select("difficulty, won, score").execute()
    ms_data = ms_res.data if ms_res.data else []
    
    ms_stats = {
        "total_games": len(ms_data),
        "wins": len([g for g in ms_data if g['won']]),
        "avg_score": sum([g['score'] for g in ms_data]) / len(ms_data) if ms_data else 0
    }
    
    # Roulette Stats
    rr_res = db.table("roulette_sessions").select("xp_pool").execute()
    rr_data = rr_res.data if rr_res.data else []
    
    rr_stats = {
        "total_games": len(rr_data),
        "total_xp_circulated": sum([g['xp_pool'] for g in rr_data])
    }
    
    # Recent Activities
    recent_ms = db.table("minesweeper_sessions").select("*").order("completed_at", desc=True).limit(10).execute()
    recent_rr = db.table("roulette_sessions").select("*").order("ended_at", desc=True).limit(10).execute()
    
    return {
        "active_page": "games",
        "ms_stats": ms_stats,
        "rr_stats": rr_stats,
        "recent_ms": recent_ms.data if recent_ms.data else [],
        "recent_rr": recent_rr.data if recent_rr.data else []
    }
