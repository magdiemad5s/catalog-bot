"""
cogs/ai.py — Google Gemini Chatbot Integration.

Listens for messages that @ping the bot and responds using the gemini-3.0-flash model.
Requires GEMINI_API_KEY in the environment.
"""
from __future__ import annotations

import logging
import re
import asyncio
from datetime import datetime, timezone

import discord
from discord.ext import commands
import json
import time
from google import genai
from google.genai import types
from ddgs import DDGS

from db.client import get_db
from utils.settings_manager import load_settings

log = logging.getLogger(__name__)

class AI(commands.Cog, name="AI"):
    """🤖 Intelligent conversational assistant."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.primary_clients = []
        self.fallback_client = None
        self.current_client_idx = 0
        
        self.auto_web_guilds = set()
        self.auto_web_users = set()
        
        # --- Web Panel & Rate Limiter State ---
        self.stats_total_requests = 0
        self.stats_rate_limits = 0
        self.user_usage = {} # {user_id: [timestamps]}
        
        # Debounce dictionary for reactions: (message_id, user_id) -> list[emoji_name]
        self.pending_reactions = {}
        self._reaction_locks = {}
        
        # Initialize Gemini Clients if keys are available
        key1 = getattr(self.bot.config, "gemini_api_key", None)
        key2 = getattr(self.bot.config, "gemini_api_key_2", None)
        key3 = getattr(self.bot.config, "gemini_api_key_3", None)
        
        try:
            if key1: self.primary_clients.append(genai.Client(api_key=key1))
            if key2: self.primary_clients.append(genai.Client(api_key=key2))
            if key3: self.fallback_client = genai.Client(api_key=key3)
            
            # Load persistent settings
            settings = load_settings()
            
            self.system_instruction = settings.get('system_prompt', "You are Catalog, a funny, slightly unhinged Discord librarian who occasionally ragebaits and stirs the pot, but ultimately remains a helpful assistant. Keep your responses concise for Discord chat. Add humor and light sarcasm.")
            self.ai_enabled = settings.get('ai_enabled', True)
            self.rate_limit_count = settings.get('rate_limit_count', 5)
            self.rate_limit_window = settings.get('rate_limit_window', 60)
            self.reaction_chance = settings.get('reaction_chance', 100)
            self.interception_chance = settings.get('interception_chance', 5)
            self.interception_keywords = settings.get('interception_keywords', 'anime:5, library:10')
            
            if self.primary_clients or self.fallback_client:
                log.info(f"Gemini AI clients initialized. Primary pool: {len(self.primary_clients)}, Emergency fallback: {1 if self.fallback_client else 0}")
                self._load_web_mode()
            else:
                log.warning("AI Cog loaded, but GEMINI_API_KEY is missing. Chatbot will not respond.")
        except Exception as e:
            log.error(f"Failed to initialize Gemini AI clients: {e}")

    def get_client(self):
        """Round-robins the primary keys, or yields the fallback if no primary is left."""
        if not self.primary_clients:
            return self.fallback_client
        client = self.primary_clients[self.current_client_idx]
        self.current_client_idx = (self.current_client_idx + 1) % len(self.primary_clients)
        return client

    def get_settings(self):
        return {
            'ai_enabled': getattr(self, 'ai_enabled', True),
            'rate_limit_count': getattr(self, 'rate_limit_count', 5),
            'rate_limit_window': getattr(self, 'rate_limit_window', 60),
            'system_prompt': getattr(self, 'system_instruction', "You are Catalog..."),
            'reaction_chance': getattr(self, 'reaction_chance', 100),
            'interception_chance': getattr(self, 'interception_chance', 5),
            'interception_keywords': getattr(self, 'interception_keywords', 'anime:5, library:10')
        }
        
    def get_stats(self):
        return {
            'total_requests': getattr(self, 'stats_total_requests', 0),
            'total_rate_limits': getattr(self, 'stats_rate_limits', 0),
            'active_throttled': getattr(self, 'active_throttled', 0)
        }
        
    def update_settings(self, count, window, prompt, ai_enabled, reaction_chance, interception_chance, interception_keywords):
        self.rate_limit_count = count
        self.rate_limit_window = window
        self.system_instruction = prompt
        self.ai_enabled = ai_enabled
        self.reaction_chance = reaction_chance
        self.interception_chance = interception_chance
        self.interception_keywords = interception_keywords

    def _is_rate_limited(self, user_id: int) -> bool:
        now = time.time()
        
        if user_id in self.user_usage:
            self.user_usage[user_id] = [t for t in self.user_usage[user_id] if now - t <= self.rate_limit_window]
        else:
            self.user_usage[user_id] = []
            
        if len(self.user_usage[user_id]) >= self.rate_limit_count:
            self.stats_rate_limits += 1
            return True
            
        self.user_usage[user_id].append(now)
        self.stats_total_requests += 1
        return False

    def _load_web_mode(self):
        try:
            db = get_db()
            res_guilds = db.table("auto_web_guilds").select("guild_id").execute()
            if res_guilds.data:
                self.auto_web_guilds = {g["guild_id"] for g in res_guilds.data}
                
            res_users = db.table("auto_web_users").select("user_id").execute()
            if res_users.data:
                self.auto_web_users = {u["user_id"] for u in res_users.data}
        except RuntimeError:
            pass # DB Not init
        except Exception as e:
            log.error(f"Failed to load auto_web settings from Supabase: {e}")

    async def _generate_with_fallback(self, contents, config=None, is_main_chat=False):
        """Multi-tier model fallback with key rotation and 503 backoff.

        Normal operation:
          - Chat (is_main_chat=True):  gemini-2.5-flash-lite-preview
          - Search / other:            gemini-2.5-flash-preview

        Per-key strategy (chat path only):
          1. Try primary (lite-preview) with 503 backoff.
          2. If primary fails (429 or 503 exhausted), do ONE quick attempt on
             secondary (flash-preview) on the same key — exits immediately on a
             new 429, no extra retries, so no additional rate-limit pressure.
          3. Rotate to the next key.

        When all keys are exhausted, walk the fallback model chain:
          gemini-2.5-flash → gemini-2.5-flash-lite →
          gemma-4-31b-it (config stripped) → gemma-4-26b-a4b-it (config stripped)
        """
        # --- Model configuration ---
        primary_model   = 'gemini-2.5-flash-lite-preview' if is_main_chat else 'gemini-2.5-flash-preview'
        secondary_model = 'gemini-2.5-flash-preview'       if is_main_chat else None  # chat-path only

        FALLBACK_CHAIN = [
            'gemini-2.5-flash',
            'gemini-2.5-flash-lite',
            'gemma-4-31b-it',
            'gemma-4-26b-a4b-it',
        ]

        # 503 backoff delays in seconds (between retries on the same key)
        backoff_delays = [5, 15, 30]

        # --- Build client pool ---
        clients = self.primary_clients + ([self.fallback_client] if self.fallback_client else [])
        if not clients:
            log.error("No Gemini clients available for generation.")
            return None

        # ------------------------------------------------------------------
        # Inner helper: one model call with 503 exponential backoff.
        # Returns (result, exc):
        #   - (result, None)  on success
        #   - (None,   exc)   on failure — exc tells the caller WHY it failed
        # Exits immediately on 429/quota (no point retrying with same key).
        # Exits immediately on unknown errors so they propagate up.
        # ------------------------------------------------------------------
        async def _attempt(client, model, cfg):
            last_exc = None
            for i, delay in enumerate([0] + backoff_delays):
                if delay > 0:
                    log.warning(f"503 on '{model}' (retry {i}/{len(backoff_delays)}). Waiting {delay}s...")
                    await asyncio.sleep(delay)
                try:
                    result = await asyncio.to_thread(
                        client.models.generate_content,
                        model=model, contents=contents, config=cfg
                    )
                    return result, None
                except Exception as e:
                    last_exc = e
                    err_str = str(e).lower()
                    if "503" in err_str or "unavailable" in err_str:
                        continue          # backoff and retry
                    return None, e        # 429 or hard error — stop immediately
            return None, last_exc         # 503 exhausted all retries

        def _is_retryable(exc) -> bool:
            """True for quota / overload errors. False for hard API errors."""
            s = str(exc).lower()
            return any(k in s for k in ("429", "exhausted", "quota", "503", "unavailable"))

        # ------------------------------------------------------------------
        # Main key-rotation loop
        # ------------------------------------------------------------------
        last_exc = None
        max_key_attempts = len(clients)

        for attempt in range(max_key_attempts):
            idx    = self.current_client_idx % len(clients)
            client = clients[idx]

            # Step 1 — try primary model
            result, exc = await _attempt(client, primary_model, config)
            if result:
                return result

            last_exc = exc
            if not _is_retryable(exc):
                raise exc   # hard API error — surface to caller immediately

            log.warning(f"Key {idx+1}: '{primary_model}' failed ({exc.__class__.__name__}). "
                        f"{'Trying secondary...' if secondary_model else 'Rotating key...'}")

            # Step 2 (chat only) — one quick shot at secondary on the same key.
            # _attempt returns immediately on a new 429, so no extra delay is added.
            if secondary_model:
                result, exc2 = await _attempt(client, secondary_model, config)
                if result:
                    return result
                last_exc = exc2
                if not _is_retryable(exc2):
                    raise exc2
                log.warning(f"Key {idx+1}: '{secondary_model}' also failed ({exc2.__class__.__name__}). Rotating key...")

            # Step 3 — rotate to the next key
            if attempt < max_key_attempts - 1:
                self.current_client_idx = (idx + 1) % len(clients)

        # ------------------------------------------------------------------
        # All keys exhausted — walk the fallback model chain
        # ------------------------------------------------------------------
        log.warning(f"All {max_key_attempts} API key(s) exhausted. Walking fallback model chain...")
        # Use the last client in the pool
        last_client = clients[self.current_client_idx % len(clients)]

        for fb_model in FALLBACK_CHAIN:
            log.warning(f"  → Trying '{fb_model}'")
            result, exc = await _attempt(last_client, fb_model, config)
            if result:
                return result
            last_exc = exc
            log.warning(f"  → '{fb_model}' failed: {exc}")

        log.error("All models and fallback chain exhausted.")
        raise last_exc

    def _save_web_mode(self, entity_id: int, entity_type: str, enable: bool):
        try:
            db = get_db()
            table = "auto_web_guilds" if entity_type == "guild" else "auto_web_users"
            column = "guild_id" if entity_type == "guild" else "user_id"
            
            if enable:
                db.table(table).upsert({column: entity_id}).execute()
            else:
                db.table(table).delete().eq(column, entity_id).execute()
        except RuntimeError:
            pass
        except Exception as e:
            log.error(f"Failed to save auto_web settings to Supabase: {e}")

    def _log_interaction(self, guild_id: int, user_id: int):
        try:
            db = get_db()
            res = db.table("ai_interactions").select("interaction_count").eq("guild_id", guild_id).eq("user_id", user_id).execute()
            if res.data:
                count = res.data[0]["interaction_count"] + 1
                db.table("ai_interactions").update({"interaction_count": count}).eq("guild_id", guild_id).eq("user_id", user_id).execute()
            else:
                db.table("ai_interactions").insert({"guild_id": guild_id, "user_id": user_id, "interaction_count": 1}).execute()
        except RuntimeError:
            pass # DB not initialized
        except Exception as e:
            log.error(f"Failed to log AI interaction: {e}")

    @discord.app_commands.command(name="enable_web", description="Turn ON automatic routing to web search.")
    @discord.app_commands.describe(personal_only="Set to True to only apply this to yourself (Admins only)")
    async def enable_web(self, interaction: discord.Interaction, personal_only: bool = False):
        is_admin = interaction.permissions.administrator or interaction.permissions.manage_guild
        
        if is_admin and not personal_only and interaction.guild_id:
            self.auto_web_guilds.add(interaction.guild_id)
            self._save_web_mode(interaction.guild_id, "guild", True)
            await interaction.response.send_message("✅ S.E.R.A. Automatic Web Search is now **ON globally** in this server.")
        else:
            self.auto_web_users.add(interaction.user.id)
            self._save_web_mode(interaction.user.id, "user", True)
            await interaction.response.send_message("✅ S.E.R.A. Automatic Web Search is now **ON** for your personal messages.")

    @discord.app_commands.command(name="disable_web", description="Turn OFF automatic routing to web search.")
    @discord.app_commands.describe(personal_only="Set to True to only apply this to yourself (Admins only)")
    async def disable_web(self, interaction: discord.Interaction, personal_only: bool = False):
        is_admin = interaction.permissions.administrator or interaction.permissions.manage_guild
        
        if is_admin and not personal_only and interaction.guild_id:
            if interaction.guild_id in self.auto_web_guilds:
                self.auto_web_guilds.remove(interaction.guild_id)
            self._save_web_mode(interaction.guild_id, "guild", False)
            await interaction.response.send_message("❌ S.E.R.A. Automatic Web Search is now **OFF globally** in this server.")
        else:
            if interaction.user.id in self.auto_web_users:
                self.auto_web_users.remove(interaction.user.id)
            self._save_web_mode(interaction.user.id, "user", False)
            await interaction.response.send_message("❌ S.E.R.A. Automatic Web Search is now **OFF** for your personal messages.")

    @commands.group(name="catalogtop", invoke_without_command=True)
    async def catalogtop(self, ctx: commands.Context):
        """Displays the leaderboard of users who interacted most with Catalog."""
        if not ctx.guild:
            return
            
        try:
            db = get_db()
            res = db.table("ai_interactions").select("*").eq("guild_id", ctx.guild.id).order("interaction_count", desc=True).limit(10).execute()
            
            if not res.data:
                await ctx.reply("No one has interacted with me in this archive yet.")
                return
                
            embed = discord.Embed(title="S.E.R.A. Interaction Leaderboard", color=discord.Color.blue())
            desc = ""
            for idx, row in enumerate(res.data, start=1):
                user = self.bot.get_user(row["user_id"])
                username = user.display_name if user else f"Scholar #{row['user_id']}"
                desc += f"**{idx}.** {username} — {row['interaction_count']} points\n"
                
            embed.description = desc
            await ctx.reply(embed=embed)
        except Exception as e:
            await ctx.reply(f"Failed to fetch archive records: {e}")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Unified entry point for AI chat processing."""
        if message.author.bot or not self.ai_enabled:
            return
        if not self.primary_clients and not self.fallback_client:
            return

        # Check for Welcome onboarding skip
        welcome_cog = self.bot.get_cog("Welcome")
        if welcome_cog and message.author.id in welcome_cog.active_onboarding:
            return

        # Context-based trigger requirements
        if message.guild and not self.bot.user.mentioned_in(message):
            return

        # Blocked DMs for registered users
        if isinstance(message.channel, discord.DMChannel):
            try:
                db = get_db()
                res = await asyncio.to_thread(lambda: db.table("user_profiles").select("has_library_card").eq("user_id", message.author.id).execute())
                if res.data and res.data[0].get("has_library_card"):
                    await message.reply("You're officially registered now! I don't chat in DMs anymore—please come ping me in the server at **☕〃the-main-hall** to talk.")
                    return
            except Exception as e:
                log.warning(f"Failed to check DB for DM block: {e}")

        # Command check
        prefix = getattr(self.bot, "command_prefix", "!")
        if message.content.startswith(prefix):
            return

        # Prepare prompt
        ping_pattern = f"<@!?{self.bot.user.id}>"
        clean_prompt = re.sub(ping_pattern, "", message.content).strip() or "Hello!"

        try:
            async with message.channel.typing():
                # 1. Gather context & Dossier
                dynamic_system_instruction = await self._get_user_context(message.author)
                
                # 2. Build history & attachments
                contents = await self._prepare_chat_contents(message, clean_prompt)
                
                # 3. Optional Web Search Pre-pass
                web_context = await self._handle_web_search(message, clean_prompt, contents)
                if web_context:
                    dynamic_system_instruction += web_context

                # 4. Configuration
                config = types.GenerateContentConfig(
                    system_instruction=dynamic_system_instruction,
                    tools=[
                        types.FunctionDeclaration(
                            name="search_discord_history",
                            description="Searches the recent Discord channel history for specific keywords.",
                            parameters={"type": "OBJECT", "properties": {"query": {"type": "STRING"}}, "required": ["query"]}
                        ),
                        types.FunctionDeclaration(
                            name="update_user_profile",
                            description="Saves a permanent fact or detail about the user to their profile dossier.",
                            parameters={"type": "OBJECT", "properties": {"fact": {"type": "STRING"}}, "required": ["fact"]}
                        )
                    ],
                    automatic_function_calling={"disable": True},
                    temperature=0.7,
                )

                # 5. Core Model Execution
                reply_text = await self._execute_model_flow(message, contents, config)
                
                # 6. Response & Post-processing
                if reply_text:
                    await message.reply(reply_text[:2000])
                    if message.guild:
                        self._log_interaction(message.guild.id, message.author.id)

        except Exception as e:
            log.error(f"Error in on_message AI flow: {e}", exc_info=True)
            await message.reply("*(I seem to be having trouble accessing my library archives right now. Please try again later!)*")

    async def _get_user_context(self, author: discord.Member | discord.User) -> str:
        """Fetch dossier & flags and return formatted system instruction prefix."""
        dossier_text = ""
        flags_text = ""
        try:
            db = get_db()
            res = await asyncio.to_thread(lambda: db.table("user_profiles").select("dossier, recent_flags").eq("user_id", author.id).execute())
            if res.data:
                dossier = res.data[0].get("dossier", "").strip()
                flags = res.data[0].get("recent_flags", "").strip()
                if dossier: dossier_text = f"\nWhat you know about this user:\n{dossier}"
                if flags: flags_text = f"\nRecent updates about this user:\n{flags}"
        except Exception as e:
            log.warning(f"Failed to fetch user context: {e}")

        return (
            f"{self.system_instruction}\n\n"
            f"You are currently talking to: {author.display_name}."
            f"{dossier_text}{flags_text}"
        )

    async def _prepare_chat_contents(self, message: discord.Message, clean_prompt: str) -> list:
        """Coalesce history, attachments, and current message into Gemini contents list."""
        contents = []
        prefix = getattr(self.bot, "command_prefix", "!")
        ping_pattern = f"<@!?{self.bot.user.id}>"

        # 1. Fetch History
        history = [m async for m in message.channel.history(limit=5, before=message)]
        history.reverse()

        # 2. Check for Reference
        referenced_msg = None
        if message.reference:
            try:
                referenced_msg = message.reference.resolved if isinstance(message.reference.resolved, discord.Message) else \
                                 await message.channel.fetch_message(message.reference.message_id)
            except: pass

        for m in history:
            if m.content.startswith(prefix) or (referenced_msg and m.id == referenced_msg.id):
                continue
            role = "model" if m.author.id == self.bot.user.id else "user"
            text = re.sub(ping_pattern, "", m.content).strip()
            if text:
                prefix_name = f"{m.author.display_name}: " if role == "user" else ""
                contents.append(types.Content(role=role, parts=[types.Part.from_text(text=f"{prefix_name}{text}")]))

        # 3. Add Reference context
        if referenced_msg:
            ref_parts = [types.Part.from_text(text=f"[Replying to {referenced_msg.author.display_name}: \"{referenced_msg.content or '(no text)'}\"]")]
            for attachment in referenced_msg.attachments:
                if attachment.content_type and attachment.content_type.startswith("image/"):
                    try:
                        data = await attachment.read()
                        ref_parts.append(types.Part.from_bytes(data=data, mime_type=attachment.content_type))
                    except: pass
            contents.append(types.Content(role="user", parts=ref_parts))

        # 4. Add Current Message
        current_parts = [types.Part.from_text(text=clean_prompt)]
        for attachment in message.attachments:
            if attachment.content_type and attachment.content_type.startswith("image/"):
                try:
                    data = await attachment.read()
                    current_parts.append(types.Part.from_bytes(data=data, mime_type=attachment.content_type))
                except: pass
        contents.append(types.Content(role="user", parts=current_parts))
        
        return contents

    async def _handle_web_search(self, message: discord.Message, clean_prompt: str, contents: list) -> str:
        """Optionally perform web search and return context string."""
        prompt_lower = clean_prompt.lower()
        auto_search = (message.guild and message.guild.id in self.auto_web_guilds) or (message.author.id in self.auto_web_users)
        manual_search = any(x in prompt_lower for x in ["search on web", "web:", "search:"])
        
        if not (auto_search or manual_search):
            return ""

        try:
            recent_context = "\n".join([f"{c.role}: {p.text}" for c in contents[-3:] for p in c.parts if hasattr(p, 'text')])
            query_maker = (
                "You are a search query generator.\n"
                f"LATEST MESSAGE: '{clean_prompt}'\n"
                f"Recent Context: {recent_context}\n\n"
                "TASK: Generate up to 2 DuckDuckGo queries for factual info. Output EXACTLY a JSON array of strings, or [] if no search is needed."
            )
            
            resp = await self._generate_with_fallback(contents=query_maker)
            if not resp or not resp.text: return ""

            raw = resp.text.strip()
            if "```" in raw: raw = raw.split("```")[1].replace("json", "").strip()
            
            try:
                queries = json.loads(raw)
                if queries:
                    log.info(f"Generated Web Queries: {queries}")
                    def fetch():
                        results = []
                        for q in queries[:2]:
                            try:
                                res = DDGS().text(q, max_results=3)
                                if res:
                                    snippets = "\n".join([f"- {r.get('title')}: {r.get('body')}" for r in res])
                                    results.append(f"🔍 [Search: '{q}']:\n{snippets}")
                                time.sleep(1)
                            except: pass
                        return "\n\n".join(results)

                    search_results = await asyncio.to_thread(fetch)
                    if search_results:
                        return f"\n\n[Live Web Search Context]\nFacts gathered automatically:\n{search_results}\n"
            except: pass
        except Exception as e:
            log.error(f"Search pre-pass failed: {e}")
        return ""

    async def _execute_model_flow(self, message: discord.Message, contents: list, config: types.GenerateContentConfig) -> str:
        """Handle function calling loop and final response generation."""
        db = get_db()
        for _ in range(2): # max 2 iterations
            resp = await self._generate_with_fallback(contents=contents, config=config, is_main_chat=True)
            if not resp: return None
            
            if not (hasattr(resp, "function_calls") and resp.function_calls):
                # Clear recent flags if we handled them
                await asyncio.to_thread(lambda: db.table("user_profiles").update({"recent_flags": ""}).eq("user_id", message.author.id).execute())
                return resp.text

            # Handle Function Calls
            call_parts = [types.Part.from_function_call(name=c.name, args=c.args) for c in resp.function_calls]
            contents.append(types.Content(role="model", parts=call_parts))

            response_parts = []
            for call in resp.function_calls:
                log.info(f"Executing Tool: {call.name}")
                if call.name == "update_user_profile":
                    fact = call.args.get("fact", "")
                    # Fetch current dossier first
                    curr = await asyncio.to_thread(lambda: db.table("user_profiles").select("dossier").eq("user_id", message.author.id).execute())
                    new_d = (curr.data[0].get("dossier", "") + "\n" + fact).strip() if curr.data else fact
                    await asyncio.to_thread(lambda: db.table("user_profiles").update({"dossier": new_d}).eq("user_id", message.author.id).execute())
                    res_data = {"result": "Success"}
                elif call.name == "search_discord_history":
                    q = call.args.get("query", "").lower()
                    found = [f"{m.author.display_name}: {m.content}" async for m in message.channel.history(limit=50) if q in m.content.lower()]
                    res_data = {"result": "\n".join(found[:5]) if found else "No matches."}
                else: res_data = {"error": "unknown"}
                
                response_parts.append(types.Part.from_function_response(name=call.name, response=res_data))
            
            contents.append(types.Content(role="user", parts=response_parts))
        return None

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        # TEMPORARILY DISABLED: prevents API quota exhaustion and memory leak
        return

        # Fetch the channel and message
        channel = self.bot.get_channel(payload.channel_id)
        if not channel:
            return
            
        try:
            message = await channel.fetch_message(payload.message_id)
        except discord.errors.NotFound:
            return

        # Ignore reactions on old messages to prevent spamming the API on startup or deep cache loads
        age_seconds = (datetime.now(timezone.utc) - message.created_at).total_seconds()
        if age_seconds > 300: # 5 minutes
            return

        # Check if the reaction was added to a message authored by the bot
        if message.author.id != self.bot.user.id:
            return

        user = self.bot.get_user(payload.user_id)
        if not user:
            try:
                user = await self.bot.fetch_user(payload.user_id)
            except Exception:
                return
                
        if user.bot:
            return

        emoji_name = payload.emoji.name
        key = (payload.message_id, payload.user_id)
        
        # Add emoji to the pending list for this user+message combo
        if key not in self.pending_reactions:
            self.pending_reactions[key] = []
        self.pending_reactions[key].append(emoji_name)
        
        # If this is the first reaction in the batch, start the debounce timer
        if len(self.pending_reactions[key]) == 1:
            asyncio.create_task(self._process_reaction_batch(channel, message, user, key))

    async def _process_reaction_batch(self, channel: discord.TextChannel, message: discord.Message, user: discord.User, key: tuple):
        # TEMPORARILY DISABLED due to quota limits
        return
        """Waits a few seconds to collate all emojis from a user before replying."""
        # Wait 4 seconds to see if they add more emojis
        await asyncio.sleep(4)
        
        # Pop the collected emojis from the dictionary
        emojis = self.pending_reactions.pop(key, None)
        if not emojis:
            return
            
        # Format the emojis for the prompt
        emoji_list_str = ", ".join(f"'{e}'" for e in emojis)
        
        if len(emojis) > 1:
            clean_prompt = f"User {user.display_name} just spammed these reactions {emoji_list_str} to your message: '{message.content}'. Respond to their reaction combo in a short, funny, and slightly ragebaiting way."
        else:
            clean_prompt = f"User {user.display_name} just reacted with the emoji {emoji_list_str} to your message: '{message.content}'. Respond to their reaction in a short, funny, and slightly ragebaiting way."

        try:
            async with channel.typing():
                config = types.GenerateContentConfig(
                    system_instruction=self.system_instruction,
                )
                
                assigned_client = self.get_client()
                if not assigned_client:
                    return
                response = await asyncio.to_thread(
                    assigned_client.models.generate_content,
                    model='gemini-2.0-flash-lite',
                    contents=clean_prompt,
                    config=config
                )
                
                reply_text = response.text
                if len(reply_text) > 2000:
                    reply_text = reply_text[:1996] + "..."

                await message.reply(f"{user.mention} {reply_text}")
                
        except Exception as e:
            log.error(f"Error generating Gemini response for reaction: {e}")

async def setup(bot: commands.Bot):
    await bot.add_cog(AI(bot))
