import logging
import re
from datetime import datetime, timezone

import discord
from discord.ext import commands

from db import get_db

log = logging.getLogger(__name__)

class WordFilter(commands.Cog):
    """🔏 Message content filter system."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _get_config(self, guild_id: int) -> dict:
        try:
            db = get_db()
            res = db.table("filter_config").select("*").eq("guild_id", guild_id).execute()
            if res.data:
                return res.data[0]
        except Exception:
            pass
        return {
            "guild_id": guild_id,
            "enabled": False,
            "active_profile_id": None,
            "threshold": 3,
            "mod_channel_id": None
        }

    async def _get_profile_words(self, profile_id: int) -> list:
        try:
            db = get_db()
            res = db.table("filter_profiles").select("word_list").eq("id", profile_id).execute()
            if res.data:
                return res.data[0].get("word_list", [])
        except Exception:
            pass
        return []

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        config = await self._get_config(message.guild.id)
        if not config.get("enabled") or not config.get("active_profile_id"):
            return

        # Bypass for certain roles (optional, usually mods)
        # For now, let's assume if they have manage_messages they bypass
        if message.author.guild_permissions.manage_messages:
            return

        words = await self._get_profile_words(config["active_profile_id"])
        if not words:
            return

        content = message.content.lower()
        matched_word = None
        
        for word in words:
            # Simple substring check, but can be improved to regex if needed
            if word.lower() in content:
                matched_word = word
                break
        
        if matched_word:
            await self._handle_violation(message, config, matched_word)

    async def _handle_violation(self, message: discord.Message, config: dict, word: str):
        guild = message.guild
        user = message.author
        
        # 1. Delete message
        try:
            await message.delete()
        except discord.Forbidden:
            log.warning(f"Missing permissions to delete message in {guild.id}")
            return

        # 2. Log to DB
        db = get_db()
        db.table("filter_log").insert({
            "guild_id": guild.id,
            "user_id": user.id,
            "channel_id": message.channel.id,
            "matched_word": word,
            "message_content": message.content,
            "action_taken": "deleted"
        }).execute()

        # 3. DM User
        try:
            embed = discord.Embed(
                title="Message Removed",
                description=f"Your message in **{guild.name}** was removed because it contained a filtered word.",
                color=discord.Color.orange()
            )
            await user.send(embed=embed)
        except discord.Forbidden:
            pass

        # 4. Check for threshold
        log_res = db.table("filter_log").select("count", count="exact").eq("guild_id", guild.id).eq("user_id", user.id).execute()
        violation_count = log_res.count if log_res.count is not None else 0
        
        if violation_count >= config.get("threshold", 3):
            await self._report_to_mods(user, config, violation_count)

    async def _report_to_mods(self, user: discord.Member, config: dict, count: int):
        mod_channel_id = config.get("mod_channel_id")
        if not mod_channel_id:
            return
            
        channel = self.bot.get_channel(mod_channel_id)
        if not channel:
            return

        embed = discord.Embed(
            title="⚠️ Automated Filter Report",
            description=f"User **{user}** ({user.id}) has reached **{count}** filter violations.",
            color=discord.Color.red(),
            timestamp=datetime.now(timezone.utc)
        )
        embed.add_field(name="User", value=user.mention)
        embed.set_footer(text="A moderator should review recent filter logs.")
        
        view = discord.ui.View()
        # Placeholder for dynamic button if we had a web link
        # view.add_item(discord.ui.Button(label="Review Logs", url=f"..."))
        
        await channel.send(embed=embed, view=view)

async def setup(bot: commands.Bot):
    await bot.add_cog(WordFilter(bot))
