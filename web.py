import os
import aiohttp
from aiohttp import web
import base64
import time
import logging

from utils.settings_manager import load_settings, save_settings, get_presets, save_preset

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
    
    settings = load_settings()
    stats = ai_cog.get_stats()
    rules_text = rules_cog.get_rules_text() if rules_cog else ""
    
    # Load settings with fallbacks
    ai_enabled = settings.get('ai_enabled', True)
    welcome_enabled = settings.get('welcome_enabled', True)
    
    ai_selected = "selected" if ai_enabled else ""
    ai_disabled = "" if ai_enabled else "selected"
    
    welcome_sel = "selected" if welcome_enabled else ""
    welcome_dis = "" if welcome_enabled else "selected"
    
    presets = get_presets()
    preset_options = ""
    for name, content in presets.items():
        # Encode for HTML attribute
        safe_content = content.replace('"', '&quot;').replace("\\", "\\\\").replace("\n", "\\n")
        preset_options += f'<option value="{safe_content}">{name}</option>'
    
    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>S.E.R.A Admin Panel</title>
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
        <style>
            :root {{
                --bg: #09090b; --surface: #18181b; --surface-border: #27272a;
                --primary: #6366f1; --primary-hover: #4f46e5;
                --text: #f4f4f5; --text-muted: #a1a1aa;
                --danger: #ef4444;
            }}
            body {{
                font-family: 'Inter', sans-serif; background: var(--bg); color: var(--text);
                margin: 0; padding: 2rem; display: flex; justify-content: center;
            }}
            .container {{ max-width: 1000px; width: 100%; }}
            .header {{
                text-align: left; margin-bottom: 2rem; padding-bottom: 1rem; border-bottom: 1px solid var(--surface-border);
            }}
            h1 {{ font-size: 1.8rem; font-weight: 700; margin: 0; color: #fff; display: flex; align-items: center; gap: 10px; }}
            h1 span {{ color: var(--primary); }}
            .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 1.5rem; }}
            .card {{
                background: var(--surface); border: 1px solid var(--surface-border); border-radius: 12px;
                padding: 1.5rem; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
            }}
            h2 {{ font-size: 1.25rem; font-weight: 600; color: #fff; margin-top: 0; margin-bottom: 1.5rem; border-bottom: 1px solid var(--surface-border); padding-bottom: 0.5rem; }}
            
            .stats-grid {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 1rem; }}
            .stat-box {{ background: rgba(99, 102, 241, 0.1); border: 1px solid rgba(99, 102, 241, 0.2); padding: 1rem; border-radius: 8px; }}
            .stat-box.danger {{ background: rgba(239, 68, 68, 0.1); border-color: rgba(239, 68, 68, 0.2); }}
            .stat-label {{ font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-muted); font-weight: 600; }}
            .stat-value {{ font-size: 1.8rem; font-weight: 700; margin-top: 0.5rem; color: #fff; }}
            
            label {{ display: block; font-size: 0.9rem; font-weight: 500; margin-bottom: 0.4rem; color: #e4e4e7; }}
            input, select, textarea {{
                width: 100%; padding: 0.75rem; border-radius: 6px; border: 1px solid var(--surface-border);
                background: #09090b; color: var(--text); font-family: inherit; font-size: 0.95rem;
                margin-bottom: 1.25rem; box-sizing: border-box; transition: border-color 0.2s;
            }}
            input:focus, select:focus, textarera:focus {{ outline: none; border-color: var(--primary); }}
            
            button {{
                background: var(--primary); color: white; border: none; padding: 0.75rem 1.5rem;
                border-radius: 6px; font-weight: 600; cursor: pointer; transition: background 0.2s; width: 100%;
                font-family: inherit; font-size: 1rem;
            }}
            button:hover {{ background: var(--primary-hover); }}
            
            .preset-controls {{ display: flex; gap: 0.5rem; margin-bottom: 1.25rem; }}
            .preset-controls select {{ margin-bottom: 0; }}
            .preset-controls button {{ width: auto; white-space: nowrap; background: #27272a; border: 1px solid #3f3f46; }}
            .preset-controls button:hover {{ background: #3f3f46; }}
            
            .save-preset-row {{ display: flex; gap: 0.5rem; align-items: flex-end; margin-bottom: 1.25rem; background: rgba(255,255,255,0.03); padding: 1rem; border-radius: 8px; border: 1px dashed var(--surface-border); }}
            .save-preset-row div {{ flex-grow: 1; }}
            .save-preset-row input {{ margin-bottom: 0; }}
            .save-preset-row button {{ width: auto; background: var(--primary); }}
        </style>
        <script>
            function loadPreset(selectObj) {{
                const val = selectObj.value;
                if(val) {{
                    document.getElementById('sys_prompt_area').value = val.replace(/\\\\n/g, '\\n');
                }}
            }}
            async function saveNewPreset() {{
                const name = document.getElementById('new_preset_name').value;
                const prompt = document.getElementById('sys_prompt_area').value;
                if(!name || !prompt) {{ alert("Please provide a name and prompt content."); return; }}
                
                const formData = new FormData();
                formData.append('name', name);
                formData.append('prompt', prompt);
                
                const res = await fetch('/presets/save', {{ method: 'POST', body: formData }});
                if(res.ok) window.location.reload();
                else alert("Failed to save preset.");
            }}
        </script>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1><span>S.E.R.A.</span> Control Hub</h1>
                <div style="color: var(--text-muted); font-size: 0.9rem; margin-top: 0.5rem;">Global Administration & Intelligence Tuning</div>
            </div>
            
            <div class="grid">
                <div class="card">
                    <h2>Live Telemetry</h2>
                    <div class="stats-grid">
                        <div class="stat-box">
                            <div class="stat-label">Servers</div>
                            <div class="stat-value">{len(bot.guilds)}</div>
                        </div>
                        <div class="stat-box">
                            <div class="stat-label">Latency</div>
                            <div class="stat-value">{round(bot.latency * 1000)}ms</div>
                        </div>
                        <div class="stat-box">
                            <div class="stat-label">Uptime</div>
                            <div class="stat-value">{round((time.time() - START_TIME) / 3600, 1)}h</div>
                        </div>
                        <div class="stat-box">
                            <div class="stat-label">Queries</div>
                            <div class="stat-value">{stats['total_requests']}</div>
                        </div>
                        <div class="stat-box danger">
                            <div class="stat-label">Rate Limits</div>
                            <div class="stat-value">{stats['total_rate_limits']}</div>
                        </div>
                        <div class="stat-box danger">
                            <div class="stat-label">Throttled Users</div>
                            <div class="stat-value">{stats['active_throttled']}</div>
                        </div>
                    </div>
                </div>
                
                <div class="card">
                    <h2>Character & Prompt Presets</h2>
                    <div class="preset-controls">
                        <select onchange="loadPreset(this)">
                            <option value="">-- Select a predefined personality --</option>
                            <option value="You are Catalog, a funny, slightly unhinged Discord librarian who occasionally ragebaits and stirs the pot, but ultimately remains a helpful assistant. Keep your responses concise for Discord chat. Add humor and light sarcasm.">Default (Catalog)</option>
                            {preset_options}
                        </select>
                    </div>
                    
                    <div class="save-preset-row">
                        <div>
                            <label>Save current prompt as Preset</label>
                            <input type="text" id="new_preset_name" placeholder="E.g. Angry Librarian">
                        </div>
                        <button type="button" onclick="saveNewPreset()">Save Preset</button>
                    </div>
                </div>
            </div>
            
            <form action="/update" method="post" style="margin-top: 1.5rem;">
                <div class="card">
                    <h2>System Configuration</h2>
                    
                    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 1rem;">
                        <div>
                            <label>Global AI Engine</label>
                            <select name="ai_enabled">
                                <option value="true" {ai_selected}>Online (Enabled)</option>
                                <option value="false" {ai_disabled}>Suspended (Disabled)</option>
                            </select>
                        </div>
                        <div>
                            <label>Welcome Sequence module</label>
                            <select name="welcome_enabled">
                                <option value="true" {welcome_sel}>Active</option>
                                <option value="false" {welcome_dis}>Inactive</option>
                            </select>
                        </div>
                    </div>
                    
                    <label style="margin-top: 1rem;">System Prompt Array (AI Character Directives)</label>
                    <textarea id="sys_prompt_area" name="system_prompt" rows="10">{settings.get('system_prompt', "You are Catalog...")}</textarea>
                    
                    <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 1rem;">
                        <div>
                            <label>Rate Lmt Count</label>
                            <input type="number" name="rate_limit_count" value="{settings.get('rate_limit_count', 5)}">
                        </div>
                        <div>
                            <label>Rate Lmt Window (s)</label>
                            <input type="number" name="rate_limit_window" value="{settings.get('rate_limit_window', 60)}">
                        </div>
                        <div>
                            <label>Reaction Chance %</label>
                            <input type="number" name="reaction_chance" value="{settings.get('reaction_chance', 100)}" min="0" max="100">
                        </div>
                        <div>
                            <label>Intercept Chance %</label>
                            <input type="number" name="interception_chance" value="{settings.get('interception_chance', 5)}" min="0" max="100">
                        </div>
                    </div>
                    
                    <label>Interception Keywords (Format: keyword:chance. e.g. anime:5, lore:10)</label>
                    <input type="text" name="interception_keywords" value="{settings.get('interception_keywords', 'anime:5, library:10')}">
                    
                    <button type="submit" style="margin-top: 0.5rem; box-shadow: 0 4px 15px rgba(99, 102, 241, 0.4);">Synchronize Directives & Save</button>
                </div>
            </form>
            
            <div class="card" style="margin-top: 1.5rem;">
                <h2>Server Rules Governance</h2>
                <form action="/update_rules" method="post">
                    <label>Rules Document (Live on Discord)</label>
                    <textarea name="rules_text" rows="5">{rules_text}</textarea>
                    <button type="submit" style="background: #27272a;">Publish Rules Update</button>
                </form>
            </div>
            
        </div>
    </body>
    </html>
    """
    return web.Response(text=html, content_type="text/html")

@routes.post("/presets/save")
async def save_preset_endpoint(request):
    data = await request.post()
    name = data.get("name")
    prompt = data.get("prompt")
    if name and prompt:
        save_preset(name, prompt)
        return web.Response(status=200, text="OK")
    return web.Response(status=400, text="Bad Request")

@routes.post("/update")
async def update_settings_endpoint(request):
    data = await request.post()
    bot = request.app['bot']
    
    # Save to JSON
    settings = load_settings()
    settings['rate_limit_count'] = int(data.get("rate_limit_count", 5))
    settings['rate_limit_window'] = int(data.get("rate_limit_window", 60))
    settings['system_prompt'] = data.get("system_prompt", "")
    settings['ai_enabled'] = str(data.get("ai_enabled", "true")).lower() == "true"
    settings['welcome_enabled'] = str(data.get("welcome_enabled", "true")).lower() == "true"
    settings['reaction_chance'] = int(data.get("reaction_chance", 100))
    settings['interception_chance'] = int(data.get("interception_chance", 5))
    settings['interception_keywords'] = data.get("interception_keywords", "")
    
    save_settings(settings)
    log.info("Settings written to data/settings.json")
    
    # Push live to loaded cogs
    ai_cog = bot.get_cog("AI")
    if ai_cog:
        ai_cog.update_settings(
            settings['rate_limit_count'], settings['rate_limit_window'], 
            settings['system_prompt'], settings['ai_enabled'], 
            settings['reaction_chance'], settings['interception_chance'], 
            settings['interception_keywords']
        )
            
    welcome_cog = bot.get_cog("Welcome")
    if welcome_cog:
        welcome_cog.welcoming_enabled = settings['welcome_enabled']
            
    raise web.HTTPFound('/')

@routes.post("/update_rules")
async def update_rules_endpoint(request):
    data = await request.post()
    bot = request.app['bot']
    rules_cog = bot.get_cog("Rules")
    
    if rules_cog:
        rules_text = data.get("rules_text", "")
        bot.loop.create_task(rules_cog.update_rules_text(rules_text))
        log.info("Governance Rules updated via Web Admin")
        
    raise web.HTTPFound('/')

def create_app(bot):
    app = web.Application(middlewares=[auth_middleware])
    app['bot'] = bot
    app.add_routes(routes)
    return app
