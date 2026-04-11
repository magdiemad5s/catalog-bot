"""
cogs/rules.py — Server Rules management.

Stores rules text and publishes updates to a designated rules channel
via the web admin panel.
"""
from __future__ import annotations

import logging

import discord
from discord.ext import commands

from utils.settings_manager import load_settings, save_settings

log = logging.getLogger(__name__)

# Channel where rules are posted — update this to your server's rules channel ID
RULES_CHANNEL_ID = 1482736369024503808  # Reuses the library-cards channel as fallback


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
        self._rules_text = new_text

        # Persist to settings.json
        settings = load_settings()
        settings["rules_text"] = new_text
        save_settings(settings)

        # Post to every guild's rules channel
        for guild in self.bot.guilds:
            channel = guild.get_channel(RULES_CHANNEL_ID)
            if not channel:
                channel = discord.utils.get(guild.text_channels, name="rules")
            if not channel:
                log.warning(f"No rules channel found in {guild.name}.")
                continue

            # Try to edit the last bot message in the channel, or send a new one
            try:
                async for msg in channel.history(limit=20):
                    if msg.author.id == self.bot.user.id and "📜" in msg.content:
                        await msg.edit(content=f"📜 **Server Rules**\n\n{new_text}")
                        log.info(f"Updated existing rules message in {channel.name}.")
                        break
                else:
                    # No existing rules message found — send new
                    await channel.send(f"📜 **Server Rules**\n\n{new_text}")
                    log.info(f"Posted new rules message in {channel.name}.")
            except discord.Forbidden:
                log.warning(f"Missing permissions to post rules in {channel.name}.")


async def setup(bot: commands.Bot):
    await bot.add_cog(Rules(bot))
