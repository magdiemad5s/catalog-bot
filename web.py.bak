import os
import html as html_mod
import aiohttp
from aiohttp import web
import base64
import time
import logging
import discord

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
        safe_name = name.replace('"', '&quot;')
        preset_options += f'<option value="{safe_content}" data-name="{safe_name}">{name}</option>'
    
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
            async function deleteCurrentPreset() {{
                const select = document.getElementById('preset_select');
                const opt = select.options[select.selectedIndex];
                if (!opt || !opt.dataset.name) {{ alert("Please select a saved user preset to delete (Default cannot be deleted)."); return; }}
                
                if (!confirm("Permanently delete preset: " + opt.dataset.name + "?")) return;
                
                const formData = new FormData();
                formData.append('name', opt.dataset.name);
                
                const res = await fetch('/presets/delete', {{ method: 'POST', body: formData }});
                if(res.ok) window.location.reload();
                else alert("Failed to delete preset.");
            }}
            async function renameCurrentPreset() {{
                const select = document.getElementById('preset_select');
                const opt = select.options[select.selectedIndex];
                if (!opt || !opt.dataset.name) {{ alert("Please select a valid user preset to rename."); return; }}
                
                const newName = prompt("Enter new name for preset:", opt.dataset.name);
                if (!newName || newName === opt.dataset.name) return;
                
                const formData = new FormData();
                formData.append('old_name', opt.dataset.name);
                formData.append('new_name', newName);
                
                const res = await fetch('/presets/rename', {{ method: 'POST', body: formData }});
                if(res.ok) window.location.reload();
                else alert("Failed to rename preset.");
            }}

            function togglePingInput(selectObj) {{
                const roleDiv = document.getElementById('ping_role_div');
                if(selectObj.value === 'role') {{
                    roleDiv.style.display = 'block';
                }} else {{
                    roleDiv.style.display = 'none';
                }}
            }}
            
            // --- Live Announcement Enhancements ---
            document.addEventListener('DOMContentLoaded', () => {{
                const form = document.getElementById('live_announce_form');
                if (form) {{
                    form.addEventListener('submit', async (e) => {{
                        e.preventDefault();
                        const btn = document.getElementById('broadcast_btn');
                        btn.disabled = true;
                        btn.innerText = "Broadcasting...";
                        const formData = new FormData(form);
                        try {{
                            const res = await fetch(form.action, {{ method: 'POST', body: formData }});
                            if(res.ok) {{
                                alert('✅ Notification was sent successfully!');
                            }} else {{
                                alert('❌ Failed to send announcement.');
                            }}
                        }} catch(err) {{
                            alert('❌ Error sending announcement: ' + err);
                        }} finally {{
                            btn.disabled = false;
                            btn.innerText = "Broadcast Announcement";
                        }}
                    }});
                }}
                
                updateTemplateDropdown();
                
                const lastDraft = localStorage.getItem('live_announce_draft');
                if(lastDraft && form && !form.dataset.loaded) {{
                    loadFormFromJSON(JSON.parse(lastDraft));
                    form.dataset.loaded = 'true';
                }}
            }});
            
            function saveDraft() {{
                const form = document.getElementById('live_announce_form');
                if(!form) return;
                const fd = new FormData(form);
                const obj = {{}};
                fd.forEach((value, key) => obj[key] = value);
                localStorage.setItem('live_announce_draft', JSON.stringify(obj));
            }}
            
            function saveTemplate() {{
                const name = prompt("Enter a name for this template (e.g. 'Cozy Stream', 'Horror Game'):");
                if(!name) return;
                
                const form = document.getElementById('live_announce_form');
                const fd = new FormData(form);
                const obj = {{}};
                fd.forEach((value, key) => obj[key] = value);
                
                let templates = JSON.parse(localStorage.getItem('live_templates') || '{{}}');
                templates[name] = obj;
                localStorage.setItem('live_templates', JSON.stringify(templates));
                updateTemplateDropdown();
                alert("Template saved!");
            }}
            
            function loadSelectedTemplate(selectObj) {{
                const name = selectObj.value;
                if(!name) return;
                let templates = JSON.parse(localStorage.getItem('live_templates') || '{{}}');
                if(templates[name]) {{
                    loadFormFromJSON(templates[name]);
                    saveDraft();
                }}
                selectObj.value = ""; 
            }}
            
            function loadFormFromJSON(obj) {{
                const form = document.getElementById('live_announce_form');
                for(const key in obj) {{
                    const el = form.querySelector(`[name="${{key}}"]`);
                    if(el) {{
                        el.value = obj[key];
                        if(el.onchange) el.onchange(); 
                    }}
                }}
            }}
            
            function deleteSelectedTemplate() {{
                 const selectObj = document.getElementById('template_select');
                 const name = selectObj.options[selectObj.selectedIndex].value;
                 if(!name) {{ alert("Please select a template to delete."); return; }}
                 
                 let templates = JSON.parse(localStorage.getItem('live_templates') || '{{}}');
                 delete templates[name];
                 localStorage.setItem('live_templates', JSON.stringify(templates));
                 updateTemplateDropdown();
                 alert("Template deleted.");
            }}
            
            function updateTemplateDropdown() {{
                const sel = document.getElementById('template_select');
                if(!sel) return;
                sel.innerHTML = '<option value="">-- Load Template --</option>';
                let templates = JSON.parse(localStorage.getItem('live_templates') || '{{}}');
                for(const name in templates) {{
                    sel.innerHTML += `<option value="${{name.replace(/"/g, '&quot;')}}">${{name}}</option>`;
                }}
            }}
            
            let backupTexts = {{}};
            async function improveText(inputName) {{
                const el = document.querySelector(`[name="${{inputName}}"]`);
                if(!el.value) {{ alert("Please enter some text first before improving it."); return; }}
                
                const originalText = el.value;
                backupTexts[inputName] = originalText;
                
                el.value = "🤖 Generating improvements...";
                el.disabled = true;
                
                try {{
                    const response = await fetch('/api/improve_text', {{
                        method: 'POST',
                        headers: {{ 'Content-Type': 'application/json' }},
                        body: JSON.stringify({{text: originalText}})
                    }});
                    const data = await response.json();
                    
                    if(data.improved_text) {{
                        el.value = data.improved_text;
                    }} else {{
                        throw new Error(data.error || "Unknown error");
                    }}
                }} catch(e) {{
                    alert("Failure: " + e);
                    el.value = originalText;
                }} finally {{
                    el.disabled = false;
                    saveDraft();
                }}
                
                document.getElementById('revert_' + inputName).style.display = 'inline-block';
            }}
            
            function revertText(inputName) {{
                const el = document.querySelector(`[name="${{inputName}}"]`);
                if(backupTexts[inputName]) {{
                    el.value = backupTexts[inputName];
                    document.getElementById('revert_' + inputName).style.display = 'none';
                    saveDraft();
                }}
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
                        <select id="preset_select" onchange="loadPreset(this)" style="flex-grow: 1;">
                            <option value="">-- Select a predefined personality --</option>
                            <option value="You are Catalog, a funny, slightly unhinged Discord librarian who occasionally ragebaits and stirs the pot, but ultimately remains a helpful assistant. Keep your responses concise for Discord chat. Add humor and light sarcasm.">Default (Catalog)</option>
                            {preset_options}
                        </select>
                        <button type="button" onclick="renameCurrentPreset()" style="width: auto; background: #6366f1;">Rename</button>
                        <button type="button" onclick="deleteCurrentPreset()" style="width: auto; background: #ef4444;">Delete</button>
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
                            <label>Global AI Engine <br><small style="color:var(--text-muted);font-weight:normal;">Instantly kills or restores standard AI message processing server-wide.</small></label>
                            <select name="ai_enabled">
                                <option value="true" {ai_selected}>Online (Enabled)</option>
                                <option value="false" {ai_disabled}>Suspended (Disabled)</option>
                            </select>
                        </div>
                        <div>
                            <label>Welcome Sequence module <br><small style="color:var(--text-muted);font-weight:normal;">Enables or disables the DM onboarding interview for new joins.</small></label>
                            <select name="welcome_enabled">
                                <option value="true" {welcome_sel}>Active</option>
                                <option value="false" {welcome_dis}>Inactive</option>
                            </select>
                        </div>
                    </div>
                    
                    <label style="margin-top: 1rem;">System Prompt Array (AI Character Directives) <br><small style="color:var(--text-muted);font-weight:normal;">The core personality that governs the AI bot. Editing this modifies its core behavioral traits.</small></label>
                    <textarea id="sys_prompt_area" name="system_prompt" rows="10">{html_mod.escape(settings.get('system_prompt', 'You are Catalog...'))}</textarea>
                    
                    <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 1rem;">
                        <div>
                            <label>Rate Lmt Count<br><small style="color:var(--text-muted);font-weight:normal;">Max requests</small></label>
                            <input type="number" name="rate_limit_count" value="{settings.get('rate_limit_count', 5)}">
                        </div>
                        <div>
                            <label>Rate Lmt Window (s)<br><small style="color:var(--text-muted);font-weight:normal;">Per X seconds</small></label>
                            <input type="number" name="rate_limit_window" value="{settings.get('rate_limit_window', 60)}">
                        </div>
                        <div>
                            <label>Reaction Chance %<br><small style="color:var(--text-muted);font-weight:normal;">Probability (0-100)</small></label>
                            <input type="number" name="reaction_chance" value="{settings.get('reaction_chance', 100)}" min="0" max="100">
                        </div>
                        <div>
                            <label>Intercept Chance %<br><small style="color:var(--text-muted);font-weight:normal;">Random reply chance</small></label>
                            <input type="number" name="interception_chance" value="{settings.get('interception_chance', 5)}" min="0" max="100">
                        </div>
                    </div>
                    
                    <label>Interception Keywords <br><small style="color:var(--text-muted);font-weight:normal;">Keywords that heavily boost the chance the AI jumps into a conversation. Format: <code>keyword:chance</code> (e.g. <code>anime:25, library:10</code>)</small></label>
                    <input type="text" name="interception_keywords" value="{html_mod.escape(settings.get('interception_keywords', 'anime:5, library:10'))}">
                    
                    <button type="submit" style="margin-top: 0.5rem; box-shadow: 0 4px 15px rgba(99, 102, 241, 0.4);">Synchronize Directives & Save</button>
                </div>
            </form>
            
            <div class="card" style="margin-top: 1.5rem;">
                <h2>Server Rules Governance</h2>
                <form action="/update_rules" method="post">
                    <label>Rules Document (Live on Discord)</label>
                    <textarea name="rules_text" rows="5">{html_mod.escape(rules_text)}</textarea>
                    <button type="submit" style="background: #27272a;">Publish Rules Update</button>
                </form>
            </div>
            
            <div class="card" style="margin-top: 1.5rem; border-color: rgba(239, 68, 68, 0.4);">
                <h2>🔴 Live Stream Announcement</h2>
                
                <div class="preset-controls" style="margin-bottom: 1.25rem; align-items: center;">
                    <select id="template_select" onchange="loadSelectedTemplate(this)" style="flex-grow: 1; margin: 0;">
                        <option value="">-- Load Template --</option>
                    </select>
                    <button type="button" onclick="saveTemplate()" style="width: auto; background: #6366f1;">Save As Template</button>
                    <button type="button" onclick="deleteSelectedTemplate()" style="width: auto; background: #ef4444;">Delete</button>
                </div>
                <hr style="border: 0; border-top: 1px solid var(--surface-border); margin-bottom: 1.25rem;">
                
                <form id="live_announce_form" action="/announce_live" method="post" oninput="saveDraft()">
                    <div style="display: flex; justify-content: space-between; align-items: flex-end;">
                        <label style="margin-bottom: 0;">Main Message Text (Outside Embed)</label>
                        <div>
                            <button type="button" id="revert_message_content" onclick="revertText('message_content')" style="display: none; padding: 0.2rem 0.5rem; font-size: 0.8rem; background: #52525b; width: auto; margin-right: 0.25rem;">↩️ Revert</button>
                            <button type="button" onclick="improveText('message_content')" style="padding: 0.2rem 0.5rem; font-size: 0.8rem; background: #8b5cf6; width: auto;">✨ Improve Text with AI</button>
                        </div>
                    </div>
                    <textarea name="message_content" rows="4" placeholder="e.g. 🚨 MAIN CHARACTER MELTDOWN IN PROGRESS 🚨\n\nLuzBrillante502 is LIVE..." style="margin-top: 0.4rem;"></textarea>
                    
                    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; margin-bottom: 1.25rem;">
                        <div>
                            <label>Streamer Name (Author)</label>
                            <input type="text" name="streamer_name" placeholder="e.g. Luzbrillante502 is now live on Twitch!" required style="margin-bottom: 0;">
                        </div>
                        <div>
                            <label>Game Category</label>
                            <input type="text" name="game" placeholder="e.g. Unknown" style="margin-bottom: 0;">
                        </div>
                    </div>
                
                    <div style="display: flex; justify-content: space-between; align-items: flex-end;">
                        <label style="margin-bottom: 0;">Stream Title (Embed Title)</label>
                        <div>
                            <button type="button" id="revert_title" onclick="revertText('title')" style="display: none; padding: 0.2rem 0.5rem; font-size: 0.8rem; background: #52525b; width: auto; margin-right: 0.25rem;">↩️ Revert</button>
                            <button type="button" onclick="improveText('title')" style="padding: 0.2rem 0.5rem; font-size: 0.8rem; background: #8b5cf6; width: auto;">✨ Improve Title with AI</button>
                        </div>
                    </div>
                    <input type="text" name="title" placeholder="e.g. 🌙 Whatever Wednesday 🎮 | Cozy Chaos & Good Vibes 💜" required style="margin-top: 0.4rem;">
                    
                    <label>Stream Link (URL)</label>
                    <input type="url" name="link" placeholder="https://twitch.tv/..." required>
                    
                    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 1rem;">
                        <div>
                            <label>Custom Image URL (Embed Image)</label>
                            <input type="url" name="image_url" placeholder="https://..." style="margin-bottom: 1.25rem;">
                        </div>
                        <div>
                            <label>Streamer Avatar URL (Author Icon)</label>
                            <input type="url" name="avatar_url" placeholder="https://...">
                        </div>
                    </div>
                    
                    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 1rem;">
                        <div>
                            <label>Ping Notification</label>
                            <select name="ping_type" onchange="togglePingInput(this)">
                                <option value="none">No Ping</option>
                                <option value="everyone">@everyone</option>
                                <option value="role">Specific Role Ping</option>
                            </select>
                        </div>
                        <div id="ping_role_div" style="display: none;">
                            <label>Role ID Configuration</label>
                            <input type="text" name="role_id" placeholder="e.g. 123456789012345678">
                        </div>
                    </div>
                    
                    <button type="submit" id="broadcast_btn" style="margin-top: 1rem; background: #ef4444; box-shadow: 0 4px 15px rgba(239, 68, 68, 0.4);">Broadcast Announcement</button>
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

from utils.settings_manager import delete_preset, rename_preset

@routes.post("/presets/delete")
async def delete_preset_endpoint(request):
    data = await request.post()
    name = data.get("name")
    if name:
        delete_preset(name)
        return web.Response(status=200, text="OK")
    return web.Response(status=400, text="Bad Request")

@routes.post("/presets/rename")
async def rename_preset_endpoint(request):
    data = await request.post()
    old_name = data.get("old_name")
    new_name = data.get("new_name")
    if old_name and new_name:
        rename_preset(old_name, new_name)
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

@routes.post("/announce_live")
async def announce_live_endpoint(request):
    data = await request.post()
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
            
        embed = discord.Embed(
            title=title,
            url=link,
            color=0x6441a5 # Twitch Purple
        )
        
        # User Avatar fallback to Bot Avatar
        icon = avatar_url if avatar_url else (bot.user.avatar.url if hasattr(bot.user, 'avatar') and bot.user.avatar else None)
        embed.set_author(name=streamer_name, icon_url=icon)
        
        if game_name:
            embed.add_field(name="Game", value=game_name)
            
        if image_url:
            embed.set_image(url=image_url)
            
        embed.set_footer(text="streamcord.io • Admin Action")
        
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="Watch Stream", url=link))
        
        bot.loop.create_task(channel.send(content=content.strip(), embed=embed, view=view))
        log.info(f"Live Announcement dispatched to {channel.id}")
        return web.json_response({"success": True})
    else:
        log.error(f"Failed to find channel {target_channel_id} for live announcement.")
        return web.json_response({"error": "Failed to find channel."}, status=500)
        
@routes.post("/api/improve_text")
async def improve_text_endpoint(request):
    data = await request.json()
    text = data.get("text", "")
    bot = request.app['bot']
    ai_cog = bot.get_cog("AI")
    
    if not ai_cog:
         return web.json_response({"error": "AI cog not loaded"}, status=500)
    
    client = ai_cog.get_client()
    if not client:
         return web.json_response({"error": "No Gemini API keys configured"}, status=500)
         
    prompt = f"You are an automated text improvement script. Rewrite the following stream announcement text to be extremely engaging, hype, and exciting, adding emojis where appropriate. Ensure it flows well for Discord, keeping the hype energy. IMPORTANT: Output ONLY the improved text. NO conversational filler, NO list of options, NO commentary, NO markdown formatting. Just output the final single string. Here is the original text to rewrite:\n\n{text}"
    
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
        log.error(f"Error improving text via API: {e}")
        return web.json_response({"error": str(e)}, status=500)

def create_app(bot):
    app = web.Application(middlewares=[auth_middleware])
    app['bot'] = bot
    app.add_routes(routes)
    return app
