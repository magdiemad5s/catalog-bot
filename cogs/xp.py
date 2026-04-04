"""
cogs/xp.py — Passive XP engine.

Handles earning XP tracking, streaks, rank upgrades, and badge awards.
"""
from __future__ import annotations

import logging
import random
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands, tasks

from db import get_db
from utils import info_embed

log = logging.getLogger(__name__)

# Constants
BADGE_KEYS = {
    "on_fire": "🔥 On Fire",
    "chill_streak": "❄️ Chill Streak",
    "chatterbox": "💬 Chatterbox",
    "town_crier": "📣 Town Crier",
    "voice_veteran": "🎤 Voice Veteran",
    "night_owl": "🌙 Night Owl",
    "podium": "🏆 Podium",
    "pioneer": "🎯 Pioneer"
}

def xp_to_level(xp: int) -> int:
    """Calculate level from XP. Formula: xp(n) = 5n^2 + 50n + 100"""
    level = 0
    while True:
        req = 5 * (level ** 2) + 50 * level + 100
        if xp < req:
            return level
        xp -= req
        level += 1

def level_to_xp(level: int) -> int:
    """Calculate total XP required to reach a specific level.
    The XP to go from level N to N+1 is 5N^2 + 50N + 100.
    """
    if level <= 0: return 0
    # Sum of (5n^2 + 50n + 100) from n=0 to level-1
    # = 5 * sum(n^2) + 50 * sum(n) + 100 * level
    # sum(n^2) for n=0 to L-1 is (L-1)*L*(2L-1)/6
    # sum(n) for n=0 to L-1 is (L-1)*L/2
    L = level
    sum_n2 = (L - 1) * L * (2 * L - 1) / 6
    sum_n = (L - 1) * L / 2
    return int(5 * sum_n2 + 50 * sum_n + 100 * L)

class XP(commands.Cog, name="XP"):
    """📈 Passive XP and leveling system."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._voice_xp_task.start()

    def cog_unload(self):
        self._voice_xp_task.cancel()

    async def _get_guild_settings(self, guild_id: int) -> dict:
        try:
            res = get_db().table("xp_settings").select("*").eq("guild_id", guild_id).execute()
            if res.data:
                return res.data[0]
        except Exception:
            pass
        # Defaults if no row exists
        return {
            "guild_id": guild_id,
            "xp_min": 15,
            "xp_max": 25,
            "cooldown_seconds": 60,
            "levelup_channel": None
        }

    async def _award_xp(self, message: discord.Message | None, member: discord.Member, amount: int, is_voice: bool = False):
        if member.bot:
            return

        guild_id = member.guild.id
        user_id = member.id
        now = datetime.now(timezone.utc)
        
        try:
            db = get_db()
        except RuntimeError:
            return # DB not initialized

        # 1. Fetch current profile
        res = db.table("xp_profiles").select("*").eq("guild_id", guild_id).eq("user_id", user_id).execute()
        
        # Determine streak bonus
        streak_days = 0
        bonus_multiplier = 1.0
        
        if res.data:
            profile = res.data[0]
            last_xp_dt = None
            if profile.get("last_xp_at"):
                last_xp_dt = datetime.fromisoformat(profile["last_xp_at"].replace("Z", "+00:00"))
            
            streak_days = profile.get("streak_days", 0)
            
            if last_xp_dt:
                delta = now - last_xp_dt
                if delta.days == 1:
                    # Active yesterday - continue streak
                    streak_days += 1
                elif delta.days > 1:
                    # Missed a day - break streak
                    streak_days = 0
            
            # Apply streak bonus (20% if streak > 0)
            if streak_days > 0:
                bonus_multiplier = 1.2
        
        final_xp_amount = int(amount * bonus_multiplier)
        
        # 2. Upsert profile
        new_messages = 0 if is_voice else 1
        new_voice_mins = 1 if is_voice else 0

        # PostgreSQL upsert using supabase
        update_data = {
            "guild_id": guild_id,
            "user_id": user_id,
            "last_xp_at": now.isoformat(),
            "last_seen_at": now.isoformat(),
            "streak_days": streak_days
        }

        # Need to read old values to increment them safely avoiding race condition
        # (Supabase py doesn't have a clean increment, so we do it in app logic for now)
        old_xp = 0
        old_level = 0
        old_messages = 0
        old_voice = 0
        
        if res.data:
            old_xp = res.data[0].get("xp", 0)
            old_level = res.data[0].get("level", 0)
            old_messages = res.data[0].get("messages_sent", 0)
            old_voice = res.data[0].get("voice_minutes", 0)
            
        new_xp = old_xp + final_xp_amount
        new_level = xp_to_level(new_xp)
        
        update_data["xp"] = new_xp
        update_data["level"] = new_level
        update_data["messages_sent"] = old_messages + new_messages
        update_data["voice_minutes"] = old_voice + new_voice_mins

        try:
            # First try update
            if res.data:
                db.table("xp_profiles").update(update_data).eq("guild_id", guild_id).eq("user_id", user_id).execute()
            else:
                db.table("xp_profiles").insert(update_data).execute()
        except Exception as e:
            log.error(f"Failed to upsert xp profile: {e}")
            return

        # 3. Check level up
        if new_level > old_level:
            await self._handle_level_up(member, new_level, update_data, message)
            
        # 4. Check badges
        await self._check_badges(member, update_data)

    async def _handle_level_up(self, member: discord.Member, new_level: int, profile_data: dict, message: discord.Message | None):
        guild = member.guild
        db = get_db()
        
        # 1. Fetch new rank tier
        res = db.table("rank_tiers").select("*").eq("guild_id", guild.id).lte("level_min", new_level).gte("level_max", new_level).execute()
        new_rank = res.data[0] if res.data else None
        
        # Fetch old rank tier (if old_level > 0, to remove old role)
        old_level = profile_data.get("level", 0) - 1
        old_rank = None
        if old_level >= 0:
            old_res = db.table("rank_tiers").select("*").eq("guild_id", guild.id).lte("level_min", old_level).gte("level_max", old_level).execute()
            old_rank = old_res.data[0] if old_res.data else None

        # 2. Swap Rank roles
        roles_to_add = []
        roles_to_remove = []
        
        if new_rank and new_rank.get("role_id"):
            role = guild.get_role(new_rank["role_id"])
            if role: roles_to_add.append(role)
            
        if old_rank and old_rank.get("role_id") and (not new_rank or old_rank["role_id"] != new_rank.get("role_id")):
            role = guild.get_role(old_rank["role_id"])
            if role: roles_to_remove.append(role)

        # 2.5 Real-time exact Level Roles ("Level X")
        # Find any old "Level *" roles or "Unranked" the user has and mark them for removal
        for r in member.roles:
            if r.name.startswith("Level ") or r.name == "Unranked":
                roles_to_remove.append(r)
                
        # Determine the name. If level is 0, they are Unranked.
        exact_level_role_name = "Unranked" if new_level == 0 else f"Level {new_level}"
        exact_role = discord.utils.get(guild.roles, name=exact_level_role_name)
        
        if not exact_role:
            try:
                # Need to create it! Let's make it a nice default grey to not overwrite their name color
                exact_role = await guild.create_role(
                    name=exact_level_role_name,
                    color=discord.Color.dark_grey(),
                    reason=f"Auto-created level role for Level {new_level}"
                )
            except discord.Forbidden:
                log.warning(f"Missing permissions to create role '{exact_level_role_name}' in {guild}")
                exact_role = None
                
        if exact_role:
            roles_to_add.append(exact_role)

        if roles_to_add or roles_to_remove:
            try:
                # Convert lists to sets to remove duplicates, then back to lists
                final_roles = [r for r in member.roles if r not in roles_to_remove]
                for r in roles_to_add:
                    if r not in final_roles:
                        final_roles.append(r)
                await member.edit(roles=final_roles)
            except discord.Forbidden:
                log.warning(f"Missing permissions to manage roles for {member} in {guild}")

        # 3. Send message
        settings = await self._get_guild_settings(guild.id)
        channel_id = settings.get("levelup_channel")
        channel = guild.get_channel(channel_id) if channel_id else (message.channel if message else None)
        
        if channel:
            rank_text = f"They reached the rank of **{new_rank['emoji']} {new_rank['label']}**!" if new_rank else ""
            embed = info_embed(
                f"Level Up! 🎉",
                f"Congratulations {member.mention}, you reached **Level {new_level}**!\n{rank_text}"
            )
            try:
                await channel.send(embed=embed)
            except discord.Forbidden:
                pass


    async def _check_badges(self, member: discord.Member, profile: dict):
        db = get_db()
        guild_id = member.guild.id
        user_id = member.id
        
        # Fetch user's current badges
        existing_badges_res = db.table("user_badges").select("badge_key").eq("guild_id", guild_id).eq("user_id", user_id).execute()
        existing_badges = {b["badge_key"] for b in existing_badges_res.data} if existing_badges_res.data else set()

        new_badges = []
        
        # Fire
        if "on_fire" not in existing_badges and profile.get("streak_days", 0) >= 7:
            new_badges.append("on_fire")
            
        # Chill Streak
        if "chill_streak" not in existing_badges and profile.get("streak_days", 0) >= 30:
            new_badges.append("chill_streak")
            
        # Chatterbox
        if "chatterbox" not in existing_badges and profile.get("messages_sent", 0) >= 1000:
            new_badges.append("chatterbox")
            
        # Town Crier
        if "town_crier" not in existing_badges and profile.get("messages_sent", 0) >= 5000:
            new_badges.append("town_crier")
            
        # Voice Veteran
        if "voice_veteran" not in existing_badges and profile.get("voice_minutes", 0) >= 60 * 60:
            new_badges.append("voice_veteran")
            
        # Night Owl (rough approximation - active between 12am and 4am UTC)
        now = datetime.now(timezone.utc)
        if "night_owl" not in existing_badges and 0 <= now.hour < 4:
            # We don't track daily night owl counts properly yet, so we grant it on the first night
            # To do "5 separate days", we'd need another DB column. For MVP, we grant on first night active.
            new_badges.append("night_owl")

        # Pioneer
        if "pioneer" not in existing_badges and profile.get("level", 0) >= 10:
             # Check if they are in top 10
             pioneers_res = db.table("user_badges").select("count").eq("guild_id", guild_id).eq("badge_key", "pioneer").execute()
             count = len(pioneers_res.data) if pioneers_res.data else 0
             if count < 10:
                 new_badges.append("pioneer")

        # Podium (checked purely via leaderboards command dynamically, usually not stored, but we can store it)
        # We will not compute podium on every tick.
        
        # Grant new badges
        if new_badges:
             badge_roles_map = {}
             # Fetch mappings
             roles_res = db.table("badge_roles").select("*").eq("guild_id", guild_id).execute()
             if roles_res.data:
                 badge_roles_map = {r["badge_key"]: r["role_id"] for r in roles_res.data}
                 
             roles_to_add = []
             for b in new_badges:
                 db.table("user_badges").insert({"guild_id": guild_id, "user_id": user_id, "badge_key": b}).execute()
                 
                 role_id = badge_roles_map.get(b)
                 if role_id:
                     role = member.guild.get_role(role_id)
                     if role: roles_to_add.append(role)
                     
             if roles_to_add:
                 try:
                     await member.add_roles(*roles_to_add)
                 except discord.Forbidden:
                     pass

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        settings = await self._get_guild_settings(message.guild.id)
        
        # Cooldown check
        try:
            db = get_db()
            res = db.table("xp_profiles").select("last_xp_at").eq("guild_id", message.guild.id).eq("user_id", message.author.id).execute()
            if res.data and res.data[0].get("last_xp_at"):
                last_dt = datetime.fromisoformat(res.data[0]["last_xp_at"].replace("Z", "+00:00"))
                if (datetime.now(timezone.utc) - last_dt).total_seconds() < settings.get("cooldown_seconds", 60):
                    return # On cooldown
        except Exception:
            pass # Fail open

        amount = random.randint(settings.get("xp_min", 15), settings.get("xp_max", 25))
        await self._award_xp(message, message.author, amount, is_voice=False)


    @tasks.loop(minutes=1.0)
    async def _voice_xp_task(self):
        """Background task: grants 10 XP per minute to active voice users."""
        for guild in self.bot.guilds:
            for vc in guild.voice_channels:
                for member in vc.members:
                    if member.bot: continue
                    # Must not be muted/deafened or AFK
                    if member.voice.self_deaf or member.voice.self_mute or member.voice.mute or member.voice.deaf:
                        continue
                    if guild.afk_channel and vc.id == guild.afk_channel.id:
                        continue
                        
                    await self._award_xp(None, member, 10, is_voice=True)

    @_voice_xp_task.before_loop
    async def before_voice_task(self):
        await self.bot.wait_until_ready()

async def setup(bot: commands.Bot):
    await bot.add_cog(XP(bot))
