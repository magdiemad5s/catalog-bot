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

HEADER = "📜 **Server Rules**\n\n"


class Rules(commands.Cog, name="Rules"):
    """📜 Server rules management from the web panel."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
    def get_rules_text(self, guild_id: int) -> str:
        """Return the current rules text (called by web.py dashboard)."""
        settings = load_settings(guild_id)
        return settings.get("rules_text", "")

    async def update_rules_text(self, guild_id: int, new_text: str):
        """Update rules in memory + settings file, then post to the rules channel."""
        if not new_text or not new_text.strip():
            log.warning("update_rules_text called with empty text. Skipping post.")
            return

        log.info(f"update_rules_text called with {len(new_text)} chars for guild {guild_id}.")

        # Persist to settings_{guild_id}.json
        settings = load_settings(guild_id)
        settings["rules_text"] = new_text
        save_settings(settings, guild_id)
        log.info(f"Rules text saved to settings_{guild_id}.json.")

        # Post to the specific guild's rules channel
        guild = self.bot.get_guild(guild_id)
        if guild:
            channel = None
            try:
                from db.client import get_db
                db = get_db()
                res = db.table("guild_configs").select("rules_channel_id").eq("guild_id", guild.id).execute()
                if res.data and res.data[0].get("rules_channel_id"):
                    channel = guild.get_channel(int(res.data[0]["rules_channel_id"]))
            except Exception as e:
                pass

            if not channel:
                channel = discord.utils.get(guild.text_channels, name="rules")
            if not channel:
                log.warning(f"No rules channel found in {guild.name}.")
                return

            log.info(f"Found rules channel: #{channel.name} ({channel.id}) in {guild.name}")

            # 1. Clean up OLD bot rules messages (Delete-and-Repost strategy)
            try:
                deleted_count = 0
                async for msg in channel.history(limit=50):
                    if msg.author.id == self.bot.user.id:
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
            # We use 3800 to leave plenty of room for safely fitting in an embed description (4096 limit)
            chunks = self._chunk_text(new_text, limit=3800)
            
            try:
                for i, chunk in enumerate(chunks):
                    embed = discord.Embed(
                        description=chunk,
                        color=discord.Color.from_rgb(101, 67, 33)
                    )
                    
                    # First embed decorations
                    if i == 0:
                        embed.title = "📜 The Arcane Codex — Server Laws"
                        embed.set_author(
                            name="Catalog — The Living Archive",
                            icon_url=self.bot.user.display_avatar.url
                        )
                        embed.add_field(name="⠀", value="─────────────────────────────────", inline=False)
                    
                    # Bottom decorative line for all embeds
                    embed.add_field(name="⠀", value="─────────────────────────────────", inline=False)
                    
                    # Last embed footer and timestamp
                    if i == len(chunks) - 1:
                        embed.set_footer(
                            text="✦ By entering these halls, you agree to abide by the Codex ✦",
                            icon_url=self.bot.user.display_avatar.url
                        )
                        embed.timestamp = discord.utils.utcnow()
                    
                    # Visual spacing between multiple embed chunks
                    if i > 0:
                        await channel.send("⠀")
                        await asyncio.sleep(0.3)

                    await channel.send(embed=embed)
                    await asyncio.sleep(0.3)
                    
                log.info(f"Posted rules in {len(chunks)} embed(s) to {channel.name}.")
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
