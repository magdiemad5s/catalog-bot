"""
cogs/rules.py — Server Rules management.

Stores rules text and publishes updates to a designated rules channel
via the web admin panel.
"""
from __future__ import annotations

import asyncio
import logging

import discord
from discord.ext import commands

from utils.settings_manager import load_settings, save_settings

log = logging.getLogger(__name__)

# Channel where rules are posted — update this to your server's rules channel ID
RULES_CHANNEL_ID = 1482736366990262283  # Reuses the library-cards channel as fallback
HEADER = "📜 **Server Rules**\n\n"


class Rules(commands.Cog, name="Rules"):
    """📜 Server rules management from the web panel."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        settings = load_settings()
        self._rules_text = settings.get("rules_text", "")

    def get_rules_text(self) -> str:
        """Return the current rules text (called by web.py dashboard)."""
        return self._rules_text

    async def update_rules_text(self, new_text: str):
        """Update rules in memory + settings file, then post to the rules channel."""
        if not new_text or not new_text.strip():
            log.warning("update_rules_text called with empty text. Skipping post.")
            return

        self._rules_text = new_text
        log.info(f"update_rules_text called with {len(new_text)} chars.")

        # Persist to settings.json
        settings = load_settings()
        settings["rules_text"] = new_text
        save_settings(settings)
        log.info("Rules text saved to settings.json.")

        # Post to every guild's rules channel
        for guild in self.bot.guilds:
            channel = guild.get_channel(RULES_CHANNEL_ID)
            if not channel:
                channel = discord.utils.get(guild.text_channels, name="rules")
            if not channel:
                log.warning(f"No rules channel found in {guild.name}. Tried ID={RULES_CHANNEL_ID} and name='rules'.")
                continue

            log.info(f"Found rules channel: #{channel.name} ({channel.id}) in {guild.name}")

            # 1. Clean up OLD bot rules messages (Delete-and-Repost strategy)
            try:
                deleted_count = 0
                async for msg in channel.history(limit=50):
                    if msg.author.id == self.bot.user.id and "📜" in msg.content:
                        await msg.delete()
                        await asyncio.sleep(0.5)  # Avoid hitting rate limits
                        deleted_count += 1
                if deleted_count > 0:
                    log.info(f"Deleted {deleted_count} old rules messages in {channel.name}.")
            except discord.Forbidden:
                log.warning(f"Missing permissions to delete old rules in {channel.name}.")
            except Exception as e:
                 log.error(f"Cleanup failed in {channel.name}: {e}")

            # 2. Split and Post new rules
            # We use 1800 to leave plenty of room for HEADER (22 chars) and safety
            chunks = self._chunk_text(new_text, limit=1800)
            
            try:
                for i, chunk in enumerate(chunks):
                    prefix = HEADER if i == 0 else ""
                    await channel.send(f"{prefix}{chunk}")
                    await asyncio.sleep(0.3)
                log.info(f"Posted rules in {len(chunks)} chunks to {channel.name}.")
            except discord.Forbidden:
                log.warning(f"Missing permissions to post rules in {channel.name}.")
            except Exception as e:
                log.error(f"Failed to post rules in {channel.name}: {e}", exc_info=True)

    def _chunk_text(self, text: str, limit: int = 2000) -> list[str]:
        """Split text into chunks by newline where possible."""
        if not text:
            return []

        if len(text) <= limit:
            return [text]
            
        chunks = []
        current_chunk = ""
        
        lines = text.split('\n')
        for line in lines:
            if len(current_chunk) + len(line) + 1 > limit:
                if current_chunk:
                    chunks.append(current_chunk.strip())
                    current_chunk = line + '\n'
                else:
                    # Single line is too long, force split
                    for i in range(0, len(line), limit):
                        chunks.append(line[i:i+limit])
                    current_chunk = ""
            else:
                current_chunk += line + '\n'
        
        if current_chunk:
            chunks.append(current_chunk.strip())
            
        return chunks


async def setup(bot: commands.Bot):
    await bot.add_cog(Rules(bot))
