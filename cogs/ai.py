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
from google import genai
from google.genai import types

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
            'total_requests': getattr(self, 'total_requests', 0),
            'total_rate_limits': getattr(self, 'total_rate_limits', 0),
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

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Ignore our own messages or other bots
        if message.author.bot:
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
                history = [m async for m in message.channel.history(limit=5, before=message)]
                history.reverse() # Oldest to newest
                
                for past_msg in history:
                    # Don't include bot command invocations in history to reduce noise
                    if past_msg.content.startswith(prefix):
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

                # Function calling loop (max 2 iterations to save on quota)
                max_iterations = 2
                reply_text = "*I encountered an error.*"
                
                # Retrieve the active load-balanced key
                assigned_client = self.get_client()
                if not assigned_client:
                    await message.reply("*(My brain's completely disconnected! No api keys found!)*")
                    return
                
                for iteration in range(max_iterations):
                    # Because we are using tools, we need to handle potential iterations or just let the API do it
                    # If a rate limit hits, we will try up to 2 fallback attempts
                    retry_attempts = 2
                    response = None
                    
                    while retry_attempts > 0:
                        try:
                            response = await asyncio.to_thread(
                                assigned_client.models.generate_content,
                                model='gemini-3.1-flash-lite-preview', # Reverted to lite for highest RPD (500/day)
                                contents=contents,
                                config=config
                            )
                            break # Success! Break out of the retry loop.
                            
                        except Exception as e:
                            error_str = str(e)
                            if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                                log.warning(f"Active Gemini API Key was rate limited! Swapping clients... {e}")
                                retry_attempts -= 1
                                
                                if retry_attempts > 0:
                                    await message.reply("*(Phew, brain is a bit overloaded! Let me think for a second...)*")
                                    await asyncio.sleep(4) # Pace our request
                                    
                                    # Swap to emergency fallback, or round-robin to the next primary
                                    if self.fallback_client and assigned_client != self.fallback_client:
                                        assigned_client = self.fallback_client
                                    else:
                                        assigned_client = self.get_client()
                                else:
                                    raise e # Out of retries, throw the error
                            else:
                                raise e # Non-quota error, throw immediately
                    
                    if not response:
                        raise Exception("Failed to generate content after max retries.")
                    
                    if hasattr(response, "function_calls") and response.function_calls:
                        # Model wants to call a function
                        # Append the model's call to the history so it remembers requesting it
                        call_parts = []
                        for call in response.function_calls:
                            # Note: The exact structure of function_call in SDK depends, but typically exposes name and args
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
                                # Extremely rudimentary local search on deeper history
                                deep_history = [m async for m in message.channel.history(limit=100, before=message)]
                                found_messages = []
                                for m in deep_history:
                                    if query in m.content.lower():
                                        found_messages.append(f"{m.author.display_name}: {m.content}")
                                
                                if found_messages:
                                    res_str = "Found in logs:\n" + "\n".join(found_messages[:5]) # limit to top 5
                                else:
                                    res_str = "No matches found in the recent channel history."
                                
                                result_data = {"result": res_str}
                            else:
                                result_data = {"error": "Unknown function"}

                            response_parts.append(types.Part.from_function_response(name=call.name, response=result_data))
                        
                        contents.append(types.Content(role="user", parts=response_parts))
                        # Loop continues to generate the NEXT response with the data!
                        continue

                    # If it didn't call a function, it just gave us text
                    reply_text = response.text
                    break
                
                # Discord has a 2000 character limit per message.
                if reply_text and len(reply_text) > 2000:
                    reply_text = reply_text[:1996] + "..."

                # Clear recent_flags from DB once they have been read by the AI
                if row.data and row.data[0].get("recent_flags", "").strip():
                    db.table("user_profiles").update({"recent_flags": ""}).eq("user_id", message.author.id).execute()
                await message.reply(reply_text)
                
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
                
                response = await asyncio.to_thread(
                    self.client.models.generate_content,
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
