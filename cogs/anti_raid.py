import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, List

import discord
from discord.ext import commands

from db import get_db
from utils import error_embed, info_embed

log = logging.getLogger(__name__)

class AntiRaid(commands.Cog):
    """🛡️ Anti-raid detection and response system."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # guild_id -> list of join datetimes
        self._join_trackers: Dict[int, List[datetime]] = {}

    async def _get_config(self, guild_id: int) -> dict:
        try:
            db = get_db()
            res = db.table("anti_raid_config").select("*").eq("guild_id", guild_id).execute()
            if res.data:
                return res.data[0]
        except Exception as e:
            log.error(f"Failed to fetch anti-raid config for guild {guild_id}: {e}")
        return {
            "guild_id": guild_id,
            "enabled": False,
            "account_age_min_days": 7,
            "join_rate_count": 5,
            "join_rate_window_seconds": 10,
            "penalty_action": "kick",
            "mute_duration_minutes": 60,
            "alert_channel_id": None,
            "quarantine_role_id": None
        }

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        guild = member.guild
        config = await self._get_config(guild.id)
        
        if not config.get("enabled"):
            return

        now = datetime.now(timezone.utc)
        
        # 1. Check Account Age
        age_days = (now - member.created_at).days
        is_suspicious_age = age_days < config.get("account_age_min_days", 7)
        
        # 2. Check Join Rate
        if guild.id not in self._join_trackers:
            self._join_trackers[guild.id] = []
        
        # Add current join and clean up old ones
        self._join_trackers[guild.id].append(now)
        window = timedelta(seconds=config.get("join_rate_window_seconds", 10))
        self._join_trackers[guild.id] = [dt for dt in self._join_trackers[guild.id] if now - dt <= window]
        
        is_raid_detected = len(self._join_trackers[guild.id]) >= config.get("join_rate_count", 5)
        
        if is_suspicious_age or is_raid_detected:
            reason = "Raid detection trigger" if is_raid_detected else "Suspicious account age"
            log.warning(f"Anti-Raid triggered in {guild.name} for {member.name} ({member.id}). Reason: {reason}")
            await self._apply_penalty(member, config, reason)

    async def _apply_penalty(self, member: discord.Member, config: dict, reason: str):
        guild = member.guild
        action = config.get("penalty_action", "kick")
        
        try:
            if action == "kick":
                await member.kick(reason=reason)
            elif action == "ban":
                await member.ban(reason=reason, delete_message_days=1)
            elif action == "mute":
                duration = timedelta(minutes=config.get("mute_duration_minutes", 60))
                await member.timeout(duration, reason=reason)
            elif action == "quarantine" and config.get("quarantine_role_id"):
                role = guild.get_role(config.get("quarantine_role_id"))
                if role:
                    await member.add_roles(role, reason=reason)
            
            # Send Alert
            alert_channel_id = config.get("alert_channel_id")
            if alert_channel_id:
                channel = guild.get_channel(alert_channel_id)
                if channel:
                    embed = discord.Embed(
                        title="🚨 Anti-Raid Alert",
                        description=f"Automated action taken against **{member}**.",
                        color=discord.Color.red(),
                        timestamp=datetime.now(timezone.utc)
                    )
                    embed.add_field(name="User ID", value=member.id)
                    embed.add_field(name="Trigger Reason", value=reason)
                    embed.add_field(name="Action Taken", value=action.capitalize())
                    embed.set_footer(text=f"Account Age: {(datetime.now(timezone.utc) - member.created_at).days} days")
                    await channel.send(embed=embed)
                    
        except discord.Forbidden:
            log.error(f"Missing permissions to {action} member {member.id} in {guild.id}")

async def setup(bot: commands.Bot):
    await bot.add_cog(AntiRaid(bot))
