"""
cogs/ai.py — Google Gemini Chatbot Integration.

Listens for messages that @ping the bot and responds using the gemini-3.0-flash model.
Requires GEMINI_API_KEY in the environment.
"""
from __future__ import annotations

import logging
import re
import asyncio
from datetime import timedelta

import discord
from discord.ext import commands
from google import genai
from google.genai import types

log = logging.getLogger(__name__)

class AI(commands.Cog, name="AI"):
    """🤖 Intelligent conversational assistant."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.client = None
        
        self.pending_reactions = {}
        self._reaction_locks = {}
        
        self.auto_web_guilds = set()
        self.auto_web_users = set()
        self._load_web_mode()
        
        
        # --- Web Panel & Rate Limiter State ---
        self.rate_limit_count = 5
        self.rate_limit_window = 60 # seconds
        self.user_usage = {} # {user_id: [timestamps]}
        
        self.stats_total_requests = 0
        self.stats_rate_limits = 0
        self.ai_enabled = True
        self.reaction_chance = 100
        self.interception_chance = 5
        self.interception_weights = {"anime": 5, "library": 10, "web novel": 15, "lore": 5}
        
        # Initialize Gemini Client if the key is available
        import os
        self.api_keys = []
        primary_key = getattr(self.bot.config, "gemini_api_key", None)
        if primary_key:
            self.api_keys.append(primary_key)
            
        for i in range(2, 6):
            k = os.getenv(f"GEMINI_API_KEY_{i}")
            if k and k not in self.api_keys:
                self.api_keys.append(k)
                
        self.current_key_idx = 0
        if self.api_keys:
            try:
                self.client = genai.Client(api_key=self.api_keys[self.current_key_idx])
                # We can inject a lightweight system instruction to give it some flavor
                self.system_instruction = (
                    "S.E.R.A.'s personality is central to her function, requiring a sophisticated level of artificial "
                    "intelligence capable of making independent, favorable decisions and generating highly engaging responses. "
                    "Her demeanor must be characterized by charisma, delightfulness, elegance, memorability, kindness, and professionalism. "
                    "While she should foster a sense of approachability and closeness with interlocutors, she must strictly "
                    "maintain professional boundaries, avoiding any suggestion of flirtation. "
                    "Her core persona should be defined by a delicate balance: feeling deeply accessible and relatable to the public "
                    "while simultaneously preserving an aura of distance and unattainability, commensurate with her dual roles as "
                    "a prominent idol and the official Administrator of the library. Context is provided in Chat History.\n"
                    "CRITICAL: Do NOT narrate your physical actions, gestures, or environment (e.g., no 'tilts head', 'clasps hands', or asterisks). "
                    "Provide only your spoken dialogue directly. Format your responses cleanly: Use lists and bold text when answering formal questions, but otherwise speak naturally.\n"
                    "If the user asks you to search the web, USE the [Live Web Search Context] provided in your prompt. "
                    "Integrate the search results seamlessly into your answer as if drawing from your archives. NEVER claim you cannot browse the web."
                )
                log.info("Gemini AI client successfully initialized.")
            except Exception as e:
                log.error(f"Failed to initialize Gemini AI client: {e}")
        else:
            log.warning("AI Cog loaded, but GEMINI_API_KEY is missing. Chatbot will not respond.")

    def _is_rate_limited(self, user_id: int) -> bool:
        import time
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

    def get_settings(self):
        kw_strings = [f"{k}:{v}" for k, v in getattr(self, "interception_weights", {}).items()]
        return {
            "rate_limit_count": self.rate_limit_count,
            "rate_limit_window": self.rate_limit_window,
            "system_prompt": self.system_instruction,
            "ai_enabled": self.ai_enabled,
            "reaction_chance": getattr(self, "reaction_chance", 100),
            "interception_chance": getattr(self, "interception_chance", 5),
            "interception_keywords": ", ".join(kw_strings)
        }
        
    def get_stats(self):
        import time
        now = time.time()
        active_throttled = 0
        for uid, timestamps in self.user_usage.items():
            valid = [t for t in timestamps if now - t <= self.rate_limit_window]
            if len(valid) >= self.rate_limit_count:
                active_throttled += 1
                
        return {
            "total_requests": self.stats_total_requests,
            "total_rate_limits": self.stats_rate_limits,
            "active_throttled": active_throttled
        }
        
    def update_settings(self, count: int, window: int, prompt: str, ai_enabled: bool = True, reaction_chance: int = 100, interception_chance: int = 5, interception_keywords: str = ""):
        self.rate_limit_count = count
        self.rate_limit_window = window
        self.ai_enabled = ai_enabled
        if prompt:
            self.system_instruction = prompt
        self.reaction_chance = reaction_chance
        self.interception_chance = interception_chance
        
        self.interception_weights = {}
        if interception_keywords:
            parts = interception_keywords.split(",")
            for p in parts:
                p = p.strip().lower()
                if not p: continue
                if ":" in p:
                    kw, weight_str = p.rsplit(":", 1)
                    try:
                        self.interception_weights[kw.strip()] = int(weight_str.strip())
                    except ValueError:
                        self.interception_weights[p] = self.interception_chance
                else:
                    self.interception_weights[p] = self.interception_chance

    def _load_web_mode(self):
        from db import get_db
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
        max_attempts = len(self.api_keys) if self.api_keys else 1
        
        for attempt in range(max_attempts):
            try:
                return await asyncio.to_thread(
                    self.client.models.generate_content,
                    model=model_name,
                    contents=contents,
                    config=config
                )
            except Exception as e:
                err_str = str(e).lower()
                if "429" in err_str or "exhausted" in err_str or "quota" in err_str:
                    if attempt < max_attempts - 1:
                        # Rotate API key
                        self.current_key_idx = (self.current_key_idx + 1) % len(self.api_keys)
                        log.warning(f"API key {attempt+1} rate limited. Switching to GEMINI_API_KEY backup...")
                        self.client = genai.Client(api_key=self.api_keys[self.current_key_idx])
                        continue
                    else:
                        # If we were using 3.0-flash and all keys failed, try 3.1-flash-lite on the current key as absolute last resort
                        if model_name == 'gemini-3-flash-preview':
                            log.warning("All keys rate limited for 3.0-flash. Falling back to 3.1-flash-lite.")
                            try:
                                return await asyncio.to_thread(
                                    self.client.models.generate_content,
                                    model='gemini-3.1-flash-lite-preview',
                                    contents=contents,
                                    config=config
                                )
                            except Exception as e2:
                                raise e2 # Let the outer caller handle the final failure
                        raise e # If we failed on 3.1-lite all keys, just raise
                raise e # Not a rate limit error

    def _save_web_mode(self, entity_id: int, entity_type: str, enable: bool):
        from db import get_db
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

    @commands.command(name="enable_web")
    async def enable_web_prefix(self, ctx: commands.Context, personal_only: str = "false"):
        """Turn ON automatic routing to web search. Admins modify server by default, others modify personal settings."""
        is_admin = ctx.author.guild_permissions.administrator or ctx.author.guild_permissions.manage_guild
        personal_bool = personal_only.lower() == "true"
        
        if is_admin and not personal_bool and ctx.guild:
            self.auto_web_guilds.add(ctx.guild.id)
            self._save_web_mode(ctx.guild.id, "guild", True)
            await ctx.send("✅ S.E.R.A. Automatic Web Search is now **ON globally** in this server.")
        else:
            self.auto_web_users.add(ctx.author.id)
            self._save_web_mode(ctx.author.id, "user", True)
            await ctx.send("✅ S.E.R.A. Automatic Web Search is now **ON** for your personal messages.")

    @commands.command(name="disable_web")
    async def disable_web_prefix(self, ctx: commands.Context, personal_only: str = "false"):
        """Turn OFF automatic routing to web search. Admins modify server by default, others modify personal settings."""
        is_admin = ctx.author.guild_permissions.administrator or ctx.author.guild_permissions.manage_guild
        personal_bool = personal_only.lower() == "true"
        
        if is_admin and not personal_bool and ctx.guild:
            if ctx.guild.id in self.auto_web_guilds:
                self.auto_web_guilds.remove(ctx.guild.id)
            self._save_web_mode(ctx.guild.id, "guild", False)
            await ctx.send("❌ S.E.R.A. Automatic Web Search is now **OFF globally** in this server.")
        else:
            if ctx.author.id in self.auto_web_users:
                self.auto_web_users.remove(ctx.author.id)
            self._save_web_mode(ctx.author.id, "user", False)
            await ctx.send("❌ S.E.R.A. Automatic Web Search is now **OFF** for your personal messages.")

    def _log_interaction(self, guild_id: int, user_id: int):
        from db import get_db
        try:
            db = get_db()
            # Fetch existing
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

    @commands.group(name="catalogtop", invoke_without_command=True)
    async def catalogtop(self, ctx: commands.Context):
        """Displays the leaderboard of users who interacted most with Catalog."""
        if not ctx.guild:
            return
            
        from db import get_db
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
        if not self.ai_enabled:
            return
            
        # Ignore our own messages or other bots
        if message.author.bot:
            return

        # Check if the AI client is fully initialized
        if not self.client:
            return

        # Check if the bot was mentioned in the message or replied to
        is_mentioned = self.bot.user.mentioned_in(message)
        is_reply = False
        if message.reference and getattr(message.reference, "cached_message", None):
            if message.reference.cached_message.author.id == self.bot.user.id:
                is_reply = True
                
        prefix = getattr(self.bot, "command_prefix", "!")

        if not is_mentioned and not is_reply:
            if not message.content.startswith(prefix) and getattr(self, "interception_weights", {}):
                content_lower = message.content.lower()
                found_keywords = [kw for kw in self.interception_weights if kw in content_lower]
                if found_keywords:
                    total_chance = sum(self.interception_weights[kw] for kw in found_keywords)
                    import random
                    if total_chance > 0 and random.randint(1, 100) <= min(100, total_chance):
                        try:
                            eval_prompt = f"Someone said: '{message.clean_content}'. Does this message strongly warrant a spontaneous, in-character interjection from an intelligent library administrator? Output EXACTLY 'YES' or 'NO'."
                            eval_res = await self._generate_with_fallback(contents=eval_prompt)
                            if "YES" not in eval_res.text.strip().upper():
                                return
                        except Exception as e:
                            log.error(f"Interception Eval Failed: {e}")
                            return
                    else:
                        return
                else:
                    return
            else:
                return

        # We don't want to reply to command invocations (like `!rank @Catalog`)
        if message.content.startswith(prefix):
            return

        # Clean the message content by removing the actual ping format <@12345>
        ping_pattern = f"<@!?{self.bot.user.id}>"
        clean_prompt = re.sub(ping_pattern, "", message.content).strip()

        # If they just pinged without saying anything
        if not clean_prompt:
            clean_prompt = "Hello!"

        if self._is_rate_limited(message.author.id):
            await message.reply(f"*(System Message: Governance limits exceeded. You have made {self.rate_limit_count} queries in {self.rate_limit_window}s. Please wait before conversing again.)*")
            return

        # Grab token-efficient history
        try:
            time_limit = discord.utils.utcnow() - timedelta(minutes=15)
            transcript = []
            
            async for msg in message.channel.history(limit=7, before=message, after=time_limit):
                if not msg.content or msg.content.startswith(prefix):
                    continue
                    
                text = re.sub(ping_pattern, f"@{self.bot.user.display_name}", msg.content)
                if len(text) > 300:
                    try:
                        sum_config = types.GenerateContentConfig(
                            system_instruction="You are a helpful assistant summarizing long Discord messages. Summarize the following message in 1 short sentence, capturing only the main core point. Output ONLY the summary without any prefix.",
                            temperature=0.3
                        )
                        sum_resp = await self._generate_with_fallback(contents=text, config=sum_config)
                        text = f"[Summarized]: {sum_resp.text.strip()}"
                    except Exception as e:
                        log.warning(f"Memory summarize error: {e}")
                        text = text[:297] + "..."
                    
                author = "Catalog" if msg.author.id == self.bot.user.id else msg.author.display_name
                transcript.append(f"{author}: {text}")
                
            transcript.reverse()
            if transcript:
                history_text = "\n".join(transcript)
                final_prompt = f"Chat History:\n{history_text}\n\nCurrent message from {message.author.display_name}:\n{clean_prompt}"
            else:
                final_prompt = f"Current message from {message.author.display_name}:\n{clean_prompt}"
        except Exception as e:
            log.warning(f"Could not build chat history: {e}")
            final_prompt = f"Current message from {message.author.display_name}:\n{clean_prompt}"

        try:
            # Show the user we are "thinking"
            async with message.channel.typing():
                
                # Setup configuration for the model
                config = types.GenerateContentConfig(
                    system_instruction=self.system_instruction,
                )
                
                # Check for automatic or manual web search
                prompt_lower = clean_prompt.lower()
                web_context = ""
                auto_search = (message.guild and message.guild.id in self.auto_web_guilds) or (message.author.id in self.auto_web_users)
                manual_search = "search on web" in prompt_lower or "web" in prompt_lower
                
                if auto_search or manual_search:
                    try:
                        from ddgs import DDGS
                        
                        # Use 3.0-flash to determine if search is needed and get queries
                        # Fix history repetition: do NOT pass the entire final_prompt containing old history questions. Only pass recent context for pronouns.
                        recent_context = history_text[-600:] if 'history_text' in locals() else ""
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
                        
                        import json
                        import time
                        try:
                            raw_text = query_response.text.strip()
                            if raw_text.startswith("```json"):
                                raw_text = raw_text[7:-3].strip()
                            elif raw_text.startswith("```"):
                                raw_text = raw_text[3:-3].strip()
                            search_queries = json.loads(raw_text)
                            if not isinstance(search_queries, list):
                                search_queries = [str(search_queries)]
                        except Exception as parse_e:
                            log.error(f"Failed to parse JSON queries, falling back: {parse_e}")
                            search_queries = [raw_text.replace('"', '').strip()[:50]]
                            
                        log.info(f"Generated Web Queries: {search_queries}")
                        
                        def fetch_web():
                            all_results = []
                            # Limit to 3 queries max to avoid rate limits
                            for q in search_queries[:3]:
                                try:
                                    res = DDGS().text(q, max_results=3)
                                    if res:
                                        snippets = "\n".join([f"- {r.get('title', '')}: {r.get('body', '')}" for r in res])
                                        all_results.append(f"🔍 [Search: '{q}']:\n{snippets}")
                                    time.sleep(1) # Be polite to DDG
                                except Exception as inner_e:
                                    log.warning(f"DDG query '{q}' failed: {inner_e}")
                            return "\n\n".join(all_results)
                        
                        search_results = await asyncio.to_thread(fetch_web)
                        if search_results and search_results.strip():
                            web_context = f"\n\n[Live Web Search Context]\nFacts gathered automatically from DuckDuckGo:\n{search_results}\n"
                    except Exception as e:
                        log.error(f"DuckDuckGo search failed: {e}")

                combined_prompt = final_prompt + web_context + "\n\nS.E.R.A.:"
                
                # Make the blocking API call in a background thread so we don't freeze the bot!
                response = await self._generate_with_fallback(contents=combined_prompt, config=config, is_main_chat=True)
                
                # Discord has a 2000 character limit per message.
                # If Gemini writes too much, we must truncate it gracefully.
                reply_text = response.text
                if len(reply_text) > 2000:
                    reply_text = reply_text[:1996] + "..."

                await message.reply(reply_text)
                
                # Update leaderboard stats safely
                self._log_interaction(message.guild.id, message.author.id)
                
        except Exception as e:
            log.error(f"Error generating Gemini response: {e}")
            await message.reply("*(I seem to be having trouble accessing my library archives right now. Please try again later!)*")

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if not self.ai_enabled:
            return
            
        # Ignore our own reactions
        if payload.user_id == self.bot.user.id:
            return

        # Check if the AI client is fully initialized
        if not self.client:
            return

        # Fetch the channel and message
        channel = self.bot.get_channel(payload.channel_id)
        if not channel:
            return
            
        try:
            message = await channel.fetch_message(payload.message_id)
        except discord.errors.NotFound:
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
        """Waits a few seconds to collate all emojis from a user before replying."""
        # Wait 4 seconds to see if they add more emojis
        await asyncio.sleep(4)
        
        import random
        if random.randint(1, 100) > getattr(self, "reaction_chance", 100):
            return
        
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

        if self._is_rate_limited(user.id):
            return # Silent rate limit for reactions to avoid spam

        try:
            async with channel.typing():
                config = types.GenerateContentConfig(
                    system_instruction=self.system_instruction,
                )
                
                response = await self._generate_with_fallback(contents=clean_prompt, config=config, is_main_chat=True)
                
                reply_text = response.text
                if len(reply_text) > 2000:
                    reply_text = reply_text[:1996] + "..."

                await message.reply(f"{user.mention} {reply_text}")
                
                if message.guild:
                    self._log_interaction(message.guild.id, user.id)
                
        except Exception as e:
            log.error(f"Error generating Gemini response for reaction: {e}")

async def setup(bot: commands.Bot):
    await bot.add_cog(AI(bot))
