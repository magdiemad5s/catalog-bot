import os
import aiohttp
from aiohttp import web
import base64
import time
import logging

log = logging.getLogger("web")

START_TIME = time.time()

routes = web.RouteTableDef()

def check_auth(request, config):
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Basic "):
        return False
    
    try:
        decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
        username, password = decoded.split(":", 1)
        if username == "admin" and password == config.web_password:
            return True
    except Exception:
        pass
    return False

@web.middleware
async def auth_middleware(request, handler):
    if not check_auth(request, request.app['bot'].config):
        return web.Response(
            status=401,
            headers={'WWW-Authenticate': 'Basic realm="Catalog Admin Panel"'},
            text="Unauthorized"
        )
    return await handler(request)

@routes.get("/")
async def dashboard(request):
    bot = request.app['bot']
    ai_cog = bot.get_cog("AI")
    rules_cog = bot.get_cog("Rules")
    
    if not ai_cog:
        return web.Response(text="AI Cog not loaded.", status=500)
    
    # Get stats from AI cog
    settings = ai_cog.get_settings()
    stats = ai_cog.get_stats()
    
    rules_text = rules_cog.get_rules_text() if rules_cog else ""
    
    ai_selected = "selected" if settings.get('ai_enabled', True) else ""
    ai_disabled = "" if settings.get('ai_enabled', True) else "selected"
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>S.E.R.A Admin Panel</title>
        <style>
            body {{ font-family: monospace; background: #0f1015; color: #ddd; padding: 20px; }}
            input, textarea, button, select {{ font-family: monospace; background: #2d2d2d; color: #fff; border: 1px solid #444; padding: 8px; width: 100%; box-sizing: border-box; margin-bottom: 10px; border-radius: 4px; }}
            label {{ display: block; margin-bottom: 5px; font-weight: bold; color: #a5b4fc; }}
            .card {{ background: #1e1e24; padding: 20px; margin-bottom: 20px; border-radius: 8px; box-shadow: 0 4px 12px rgba(0,0,0,0.5); border: 1px solid #333; }}
            h1, h2 {{ color: #a5b4fc; }}
            .stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; margin-bottom: 15px; }}
            .stat-box {{ background: #2a2a35; padding: 15px; text-align: center; border-radius: 6px; border-top: 4px solid #4f46e5; }}
            .stat-value {{ font-size: 28px; font-weight: bold; color: #fff; margin-top: 10px; }}
            .leaderboard-list {{ list-style-type: none; padding: 0; margin: 0; }}
            .leaderboard-list li {{ background: #2a2a35; padding: 10px; margin-bottom: 5px; border-radius: 4px; display: flex; justify-content: space-between; }}
            .pill {{ background: #4f46e5; padding: 2px 8px; border-radius: 12px; font-size: 14px; font-weight: bold; }}
            button:hover {{ background: #4338ca !important; }}
        </style>
    </head>
    <body>
        <h1>S.E.R.A. System Administration Console</h1>
        
        <div class="card">
            <h2>Live System Telemetry</h2>
            <div class="stats">
                <div class="stat-box">
                    <div>Connected Servers</div>
                    <div class="stat-value">{len(bot.guilds)}</div>
                </div>
                <div class="stat-box">
                    <div>Gateway Latency</div>
                    <div class="stat-value">{round(bot.latency * 1000)}ms</div>
                </div>
                <div class="stat-box">
                    <div>Process Uptime</div>
                    <div class="stat-value">{round((time.time() - START_TIME) / 3600, 1)}h</div>
                </div>
            </div>
            
            <h2 style="margin-top: 30px;">AI Resource Exhaustion</h2>
            <div class="stats">
                <div class="stat-box" style="border-top-color: #ef4444;">
                    <div>Total Queries Served</div>
                    <div class="stat-value">{stats['total_requests']}</div>
                </div>
                <div class="stat-box" style="border-top-color: #ef4444;">
                    <div>Rate Limit Triggers</div>
                    <div class="stat-value">{stats['total_rate_limits']}</div>
                </div>
                <div class="stat-box" style="border-top-color: #ef4444;">
                    <div>Active Throttled Users</div>
                    <div class="stat-value">{stats['active_throttled']}</div>
                </div>
            </div>
        </div>
        
        <div class="card">
            <h2>Governance Configuration</h2>
            <form action="/update" method="post">
                <label>AI Enabled (Global Killswitch)</label>
                <select name="ai_enabled" style="background: #2d2d2d; color: #fff; border: 1px solid #444; padding: 8px; width: 100%; box-sizing: border-box; margin-bottom: 10px;">
                    <option value="true" {ai_selected}>Enabled</option>
                    <option value="false" {ai_disabled}>Disabled</option>
                </select>
            
                <label>Max Queries (per window)</label>
                <input type="number" name="rate_limit_count" value="{settings['rate_limit_count']}">
                
                <label>Window Duration (seconds)</label>
                <input type="number" name="rate_limit_window" value="{settings['rate_limit_window']}">
                
                <label>System Prompt (Catalog Directives)</label>
                <textarea name="system_prompt" rows="18">{settings['system_prompt']}</textarea>
                
                <label>Reaction Response Chance (%)</label>
                <input type="number" name="reaction_chance" value="{settings.get('reaction_chance', 100)}" min="0" max="100">
                
                <label>Default Random Interception Chance (%)</label>
                <input type="number" name="interception_chance" value="{settings.get('interception_chance', 5)}" min="0" max="100">

                <label>Interception Setup (Format: keyword:chance. e.g. anime:5, lore:10, quiet:-50)</label>
                <input type="text" name="interception_keywords" value="{settings.get('interception_keywords', 'anime:5, library:10, web novel:15, lore:5')}">
                
                <button type="submit" style="background: #4f46e5; color: white; cursor: pointer; font-size: 16px; padding: 12px; margin-top: 15px; font-weight: bold;">Commit Instructions</button>
            </form>
        </div>
        
        <div class="card">
            <h2>Room Rules / Governance</h2>
            <form action="/update_rules" method="post">
                <label>Rules Document (Posts live to S.E.R.A.'s discord channel)</label>
                <textarea name="rules_text" rows="18">{rules_text}</textarea>
                
                <button type="submit" style="background: #4f46e5; color: white; cursor: pointer; font-size: 16px; padding: 12px; margin-top: 15px; font-weight: bold;">Publish Rules</button>
            </form>
        </div>
    </body>
    </html>
    """
    return web.Response(text=html, content_type="text/html")

@routes.post("/update")
async def update_settings(request):
    data = await request.post()
    bot = request.app['bot']
    ai_cog = bot.get_cog("AI")
    
    if ai_cog:
        try:
            count = int(data.get("rate_limit_count", 5))
            window = int(data.get("rate_limit_window", 60))
            prompt = data.get("system_prompt", "")
            ai_enabled_str = data.get("ai_enabled", "true")
            ai_enabled = str(ai_enabled_str).lower() == "true"
            reaction_chance = int(data.get("reaction_chance", 100))
            interception_chance = int(data.get("interception_chance", 5))
            interception_keywords = data.get("interception_keywords", "")
            
            ai_cog.update_settings(count, window, prompt, ai_enabled, reaction_chance, interception_chance, interception_keywords)
            log.info("Governance Configuration updated via Web Admin")
        except ValueError:
            log.warning("Invalid configuration numbers provided in web panel.")
            
    # Redirect back to home
    raise web.HTTPFound('/')

@routes.post("/update_rules")
async def update_rules_endpoint(request):
    data = await request.post()
    bot = request.app['bot']
    rules_cog = bot.get_cog("Rules")
    
    if rules_cog:
        rules_text = data.get("rules_text", "")
        # Run safely in background
        bot.loop.create_task(rules_cog.update_rules_text(rules_text))
        log.info("Governance Rules updated via Web Admin")
        
    raise web.HTTPFound('/')

def create_app(bot):
    app = web.Application(middlewares=[auth_middleware])
    app['bot'] = bot
    app.add_routes(routes)
    return app
