"""
cogs/ai.py — Google Gemini Chatbot Integration.

Listens for messages that @ping the bot and responds using the gemini-3.0-flash model.
Requires GEMINI_API_KEY in the environment.
"""
from __future__ import annotations

import logging
import re
import asyncio

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
        
        # Debounce dictionary for reactions: (message_id, user_id) -> list[emoji_name]
        self.pending_reactions = {}
        self._reaction_locks = {}
        
        # Initialize Gemini Client if the key is available
        api_key = getattr(self.bot.config, "gemini_api_key", None)
        if api_key:
            try:
                self.client = genai.Client(api_key=api_key)
                # We can inject a lightweight system instruction to give it some flavor
                self.system_instruction = "You are Catalog, a funny, slightly unhinged Discord librarian who occasionally ragebaits and stirs the pot, but ultimately remains a helpful assistant. Keep your responses concise for Discord chat. Add humor and light sarcasm."
                log.info("Gemini AI client successfully initialized.")
            except Exception as e:
                log.error(f"Failed to initialize Gemini AI client: {e}")
        else:
            log.warning("AI Cog loaded, but GEMINI_API_KEY is missing. Chatbot will not respond.")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Ignore our own messages or other bots
        if message.author.bot:
            return

        # Check if the AI client is fully initialized
        if not self.client:
            return

        # Check if the bot was mentioned in the message
        if not self.bot.user.mentioned_in(message):
            return

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
                
                # Setup configuration for the model
                config = types.GenerateContentConfig(
                    system_instruction=self.system_instruction,
                )
                
                # Make the blocking API call in a background thread so we don't freeze the bot!
                import asyncio
                response = await asyncio.to_thread(
                    self.client.models.generate_content,
                    model='gemini-3.1-flash-lite-preview',
                    contents=clean_prompt,
                    config=config
                )
                
                # Discord has a 2000 character limit per message.
                # If Gemini writes too much, we must truncate it gracefully.
                reply_text = response.text
                if len(reply_text) > 2000:
                    reply_text = reply_text[:1996] + "..."

                await message.reply(reply_text)
                
        except Exception as e:
            log.error(f"Error generating Gemini response: {e}")
            await message.reply("*(I seem to be having trouble accessing my library archives right now. Please try again later!)*")

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
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
