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
        """Attempts generation with 3.0-flash. If quota exhausted, rotates API keys. If all fail, falls back to 3.1-flash-lite."""
        model_name = 'gemini-3.1-flash-lite-preview' if is_main_chat else 'gemini-3-flash-preview'
        
        # Consolidate all available clients into one pool for rotation
        clients = self.primary_clients + ([self.fallback_client] if self.fallback_client else [])
        if not clients:
            log.error("No Gemini clients available for generation.")
            return None
            
        max_attempts = len(clients)
        
        for attempt in range(max_attempts):
            # Ensure index is always within bounds of the CURRENT pool
            idx = self.current_client_idx % len(clients)
            assigned_client = clients[idx]
            
            try:
                return await asyncio.to_thread(
                    assigned_client.models.generate_content,
                    model=model_name,
                    contents=contents,
                    config=config
                )
            except Exception as e:
                err_str = str(e).lower()
                if "429" in err_str or "exhausted" in err_str or "quota" in err_str:
                    if attempt < max_attempts - 1:
                        # Rotate to next client in the pool
                        self.current_client_idx = (idx + 1) % len(clients)
                        log.warning(f"API key {idx+1} rate limited. Switching to next available key...")
                        continue
                    else:
                        # If we were using 3.0-flash and all keys failed, try 3.1-flash-lite as absolute last resort
                        if model_name == 'gemini-3-flash-preview':
                            log.warning("All keys rate limited for 3.0-flash. Falling back to 3.1-flash-lite.")
                            try:
                                return await asyncio.to_thread(
                                    assigned_client.models.generate_content,
                                    model='gemini-3.1-flash-lite-preview',
                                    contents=contents,
                                    config=config
                                )
                            except Exception as e2:
                                raise e2
                        raise e
                raise e

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
        # Ignore our own messages or other bots
        if message.author.bot:
            return

        # Respect the AI killswitch from the web panel
        if not self.ai_enabled:
            return

        # Check if the AI client is fully initialized
        if not self.primary_clients and not self.fallback_client:
            return

        # Prevent double-responding if they are in the Welcome onboarding flow
        welcome_cog = self.bot.get_cog("Welcome")
        if welcome_cog and message.author.id in welcome_cog.active_onboarding:
            return

        # If in a server, require a mention. If in a DM, respond to everything.
        if message.guild and not self.bot.user.mentioned_in(message):
            return

        # Deny DM interaction if user already has a library card
        if isinstance(message.channel, discord.DMChannel):
            try:
                db = get_db()
                res = db.table("user_profiles").select("has_library_card").eq("user_id", message.author.id).execute()
                if res.data and res.data[0].get("has_library_card"):
                    await message.reply("You're officially registered now! I don't chat in DMs anymore—please come ping me in the server at **☕〃the-main-hall** to talk.")
                    return
            except Exception as e:
                log.warning(f"Failed to check DB for DM block: {e}")

        # We don't want to reply to command invocations (like `!rank @Catalog`)
        prefix = getattr(self.bot, "command_prefix", "!")
        if message.content.startswith(prefix):
            return

        # Clean the message content by removing the actual ping format <@12345>
        # so the AI doesn't get confused by random number strings
        ping_pattern = f"<@!?{self.bot.user.id}>"
        clean_prompt = re.sub(ping_pattern, "", message.content).strip()

        # If they just pinged without saying anything
        if not clean_prompt:
            clean_prompt = "Hello!"

        try:
            # Show the user we are "thinking"
            async with message.channel.typing():
                
                # Fetch dossier & profile info from DB
                db = get_db()
                row = db.table("user_profiles").select("dossier, recent_flags").eq("user_id", message.author.id).execute()
                
                dossier_text = ""
                flags_text = ""
                if row.data:
                    dossier = row.data[0].get("dossier", "").strip()
                    flags = row.data[0].get("recent_flags", "").strip()
                    if dossier:
                        dossier_text = f"\nWhat you know about this user:\n{dossier}"
                    if flags:
                        flags_text = f"\nRecent updates about this user:\n{flags}\n(If these are relevant to the conversation, feel free to bring them up naturally)."

                dynamic_system_instruction = (
                    f"{self.system_instruction}\n\n"
                    f"You are currently talking to: {message.author.display_name}."
                    f"{dossier_text}"
                    f"{flags_text}"
                )

                # Define function declaration tools
                search_history_func = types.FunctionDeclaration(
                    name="search_discord_history",
                    description="Searches the recent Discord channel history for specific keywords. Use this when the user asks what we were talking about, or if they ask about a past message.",
                    parameters={
                        "type": "OBJECT",
                        "properties": {
                            "query": {"type": "STRING", "description": "The keyword or topic to search for in past messages."}
                        },
                        "required": ["query"]
                    }
                )

                update_profile_func = types.FunctionDeclaration(
                    name="update_user_profile",
                    description="Saves a permanent fact or detail about the user to their profile dossier. Use this when the user tells you personal details (e.g. 'I just got a dog', 'I like pizza').",
                    parameters={
                        "type": "OBJECT",
                        "properties": {
                            "fact": {"type": "STRING", "description": "A concise summary of the new fact to remember about the user."}
                        },
                        "required": ["fact"]
                    }
                )

                # Setup configuration for the model, enabling Function Tools!
                config = types.GenerateContentConfig(
                    system_instruction=dynamic_system_instruction,
                    tools=[
                        search_history_func, 
                        update_profile_func
                    ],
                    automatic_function_calling={"disable": True},
                    temperature=0.7,
                )
                
                # Gather short term memory context (last 5 messages)
                contents = []
                
                # Identify if this is a reply to another message
                referenced_msg = None
                if message.reference:
                    # Check cache first
                    if isinstance(message.reference.resolved, discord.Message):
                        referenced_msg = message.reference.resolved
                    else:
                        # Fetch from API if not cached
                        try:
                            referenced_msg = await message.channel.fetch_message(message.reference.message_id)
                        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                            referenced_msg = None

                history_limit = 5
                history = [m async for m in message.channel.history(limit=history_limit, before=message)]
                history.reverse() # Oldest to newest
                
                for past_msg in history:
                    # Don't include bot command invocations in history to reduce noise
                    if past_msg.content.startswith(prefix):
                        continue
                    
                    # Avoid double-including the referenced message if it's already in the recent history
                    if referenced_msg and past_msg.id == referenced_msg.id:
                        continue
                        
                    role = "model" if past_msg.author.id == self.bot.user.id else "user"
                    
                    text_content = past_msg.content
                    if role == "user":
                        # Strip ping formatting out of history so the bot isn't distracted
                        text_content = re.sub(ping_pattern, "", text_content).strip()
                        if not text_content:
                            continue
                        text_content = f"{past_msg.author.display_name}: {text_content}"
                        
                    if text_content:
                        contents.append(types.Content(role=role, parts=[types.Part.from_text(text=text_content)]))
                
                # If there IS a referenced message, inject it now as a specialized context "user" turn
                if referenced_msg:
                    ref_parts = []
                    ref_text = f"[Replying to {referenced_msg.author.display_name}: \"{referenced_msg.content or '(no text)'}\"]"
                    ref_parts.append(types.Part.from_text(text=ref_text))
                    
                    # Include images from the referenced message
                    if referenced_msg.attachments:
                        for attachment in referenced_msg.attachments:
                            if attachment.content_type and attachment.content_type.startswith("image/"):
                                try:
                                    img_data = await attachment.read()
                                    ref_parts.append(
                                        types.Part.from_bytes(data=img_data, mime_type=attachment.content_type)
                                    )
                                except Exception as e:
                                    log.warning(f"Failed to read attachment from referenced message: {e}")
                    
                    contents.append(types.Content(role="user", parts=ref_parts))

                # Finally, append the CURRENT message
                current_parts = [types.Part.from_text(text=clean_prompt)]
                
                # Download and attach any images from the current message
                if message.attachments:
                    for attachment in message.attachments:
                        # Check if it's an image
                        if attachment.content_type and attachment.content_type.startswith("image/"):
                            image_bytes = await attachment.read()
                            current_parts.append(
                                types.Part.from_bytes(
                                    data=image_bytes,
                                    mime_type=attachment.content_type
                                )
                            )
                
                contents.append(types.Content(role="user", parts=current_parts))

                # --- Search Pre-pass ---
                prompt_lower = clean_prompt.lower()
                web_context = ""
                auto_search = (message.guild and message.guild.id in self.auto_web_guilds) or (message.author.id in self.auto_web_users)
                # Tightened manual trigger to avoid false positives
                manual_search = "search on web" in prompt_lower or prompt_lower.startswith("web:") or prompt_lower.startswith("search:")
                
                if auto_search or manual_search:
                    try:
                        # Use Gemini to determine if search is needed and get queries
                        # Build a compact history for the query maker
                        recent_context = ""
                        for c in contents[-3:]: # last few turns
                            for p in c.parts:
                                if hasattr(p, 'text'):
                                    recent_context += f"{c.role}: {p.text}\n"

                        query_maker = (
                            "You are a search query generator.\n"
                            f"LATEST MESSAGE: '{clean_prompt}'\n"
                            f"Recent Context: {recent_context}\n\n"
                            "TASK: If the LATEST MESSAGE asks a factual question requiring live internet access, generate up to 2 DuckDuckGo queries. "
                            "Use the Recent Context ONLY to figure out missing pronouns (like 'it', 'this book', 'latest chapter'). "
                            "Do NOT generate queries for things related to the chat itself, user names, emojis, or casual greetings. "
                            "IMPORTANT: Strip meta-commands like 'search the web' from the queries. Output EXACTLY a JSON array of strings, or [] if no search is needed."
                        )
                        
                        query_response = await self._generate_with_fallback(contents=query_maker)
                        
                        if query_response and query_response.text:
                            raw_text = query_response.text.strip()
                            # Strip markdown fences if present
                            if raw_text.startswith("```json"):
                                raw_text = raw_text[7:-3].strip()
                            elif raw_text.startswith("```"):
                                raw_text = raw_text[3:-3].strip()
                            
                            try:
                                search_queries = json.loads(raw_text)
                                if not isinstance(search_queries, list):
                                    search_queries = [str(search_queries)]
                            except Exception as parse_e:
                                log.error(f"Failed to parse JSON queries, falling back: {parse_e}")
                                # Fallback: try to find anything that looks like a search query
                                search_queries = [raw_text.replace('"', '').strip()[:50]]
                                
                            if search_queries and any(q.strip() for q in search_queries):
                                log.info(f"Generated Web Queries: {search_queries}")
                                
                                def fetch_web():
                                    all_results = []
                                    # Limit to 3 queries max
                                    for q in search_queries[:3]:
                                        if not q.strip(): continue
                                        try:
                                            # Use max_results=3 for token efficiency
                                            res = DDGS().text(q, max_results=3)
                                            if res:
                                                snippets = "\n".join([f"- {r.get('title', '')}: {r.get('body', '')}" for r in res])
                                                all_results.append(f"🔍 [Search: '{q}']:\n{snippets}")
                                            # Be polite to DDG (Note: This sleep is safe as it's within a background thread)
                                            time.sleep(1) 
                                        except Exception as inner_e:
                                            log.warning(f"DDG query '{q}' failed: {inner_e}")
                                    return "\n\n".join(all_results)
                                
                                search_results = await asyncio.to_thread(fetch_web)
                                if search_results and search_results.strip():
                                    web_context = f"\n\n[Live Web Search Context]\nFacts gathered automatically from DuckDuckGo:\n{search_results}\n"
                                    # Inject context into the system prompt
                                    dynamic_system_instruction += web_context
                                    # Rebuild config instead of mutating it to ensure compatibility
                                    config = types.GenerateContentConfig(
                                        system_instruction=dynamic_system_instruction,
                                        tools=config.tools,
                                        automatic_function_calling=config.automatic_function_calling,
                                        temperature=config.temperature
                                    )
                    except Exception as e:
                        log.error(f"Search pre-pass failed: {e}")

                # Function calling loop (max 2 iterations)
                max_iterations = 2
                final_reply_text = "*I encountered an error.*"
                
                for iteration in range(max_iterations):
                    # Use _generate_with_fallback which handles key rotation and tiered models
                    response = await self._generate_with_fallback(contents=contents, config=config, is_main_chat=True)
                    
                    if not response:
                        raise Exception("Failed to generate content after manual retry.")
                    
                    if hasattr(response, "function_calls") and response.function_calls:
                        # Append the model's call to the history
                        call_parts = []
                        for call in response.function_calls:
                            call_parts.append(types.Part.from_function_call(name=call.name, args=call.args))
                        
                        contents.append(types.Content(role="model", parts=call_parts))

                        response_parts = []
                        for call in response.function_calls:
                            log.info(f"Gemini executed Function Call: {call.name} with {call.args}")
                            
                            if call.name == "update_user_profile":
                                fact = call.args.get("fact", "")
                                new_dossier = dossier_text.replace("\nWhat you know about this user:\n", "")
                                if new_dossier:
                                    new_dossier += "\n" + fact
                                else:
                                    new_dossier = fact
                                    
                                # Save to DB
                                db.table("user_profiles").update({"dossier": new_dossier}).eq("user_id", message.author.id).execute()
                                result_data = {"result": f"Successfully added '{fact}' to their persistent profile."}
                                
                            elif call.name == "search_discord_history":
                                query = call.args.get("query", "").lower()
                                deep_history = [m async for m in message.channel.history(limit=100, before=message)]
                                found_messages = []
                                for m in deep_history:
                                    if query in m.content.lower():
                                        found_messages.append(f"{m.author.display_name}: {m.content}")
                                
                                if found_messages:
                                    res_str = "Found in logs:\n" + "\n".join(found_messages[:5])
                                else:
                                    res_str = "No matches found in the recent channel history."
                                
                                result_data = {"result": res_str}
                            else:
                                result_data = {"error": "Unknown function"}

                            response_parts.append(types.Part.from_function_response(name=call.name, response=result_data))
                        
                        contents.append(types.Content(role="user", parts=response_parts))
                        continue

                    # If it didn't call a function, it just gave us text
                    final_reply_text = response.text
                    break
                
                # Discord has a 2000 character limit
                if final_reply_text and len(final_reply_text) > 2000:
                    final_reply_text = final_reply_text[:1996] + "..."

                # Clear recent_flags from DB
                if row.data and row.data[0].get("recent_flags", "").strip():
                    db.table("user_profiles").update({"recent_flags": ""}).eq("user_id", message.author.id).execute()
                
                await message.reply(final_reply_text)
                
                # Log interaction for leaderboard
                if message.guild:
                    self._log_interaction(message.guild.id, message.author.id)
                
        except Exception as e:
            log.error(f"Error generating Gemini response: {e}")
            await message.reply("*(I seem to be having trouble accessing my library archives right now. Please try again later!)*")

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        # TEMPORARILY DISABLED: Gemini 3.1 Flash free tier has a strict 15 RPM limit.
        # AI reactions consume too many quotas and crash the bot's core functionality.
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
                    model='gemini-3.1-flash-lite-preview',
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
