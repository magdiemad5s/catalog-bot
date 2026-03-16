"""
cogs/levels.py — User-facing XP commands and admin configuration.

Includes /rank, /leaderboard, /editrank, and more.
"""
from __future__ import annotations

import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from cogs.xp import BADGE_KEYS, level_to_xp, xp_to_level
from cogs.moderation import has_mod_role
from db import get_db
from utils import error_embed, info_embed, success_embed

log = logging.getLogger(__name__)


class Levels(commands.Cog, name="Levels"):
    """📊 XP checking and configuration commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _get_rank_info(self, guild_id: int, level: int):
        """Fetch rank label and emoji for a given level."""
        try:
            db = get_db()
            res = db.table("rank_tiers").select("*").eq("guild_id", guild_id).lte("level_min", level).gte("level_max", level).execute()
            if res.data:
                return res.data[0]
        except Exception:
            pass
        return {"label": "Lurker", "emoji": "👁️"}

    # ── User Commands ──────────────────────────────────────────────────────────

    @commands.hybrid_command(name="rank", description="View your or someone else's XP and rank.")
    @app_commands.describe(member="The member to view")
    async def rank(self, ctx: commands.Context, member: Optional[discord.Member] = None):
        """View your or another user's current level, XP, and badges.
        
        Examples:
        `!rank` (shows your rank)
        `!rank @user` (shows another user's rank)
        """
        target = member or ctx.author
        if target.bot:
            return await ctx.reply("Bots don't earn XP!", ephemeral=True)

        try:
            db = get_db()
            
            # Fetch rank profile
            res = db.table("xp_profiles").select("*").eq("guild_id", ctx.guild.id).eq("user_id", target.id).execute()
            profile = res.data[0] if res.data else {"xp": 0, "level": 0, "streak_days": 0}
            
            # Fetch leaderboard position
            lb_res = db.table("xp_profiles").select("user_id").eq("guild_id", ctx.guild.id).order("xp", desc=True).execute()
            rank_pos = 0
            if lb_res.data:
                for idx, row in enumerate(lb_res.data, 1):
                    if row["user_id"] == target.id:
                        rank_pos = idx
                        break

            # Fetch badges
            badges_res = db.table("user_badges").select("badge_key").eq("guild_id", ctx.guild.id).eq("user_id", target.id).execute()
            earned_badges = [BADGE_KEYS.get(b["badge_key"], b["badge_key"]) for b in (badges_res.data or [])]
            
            # Fetch rank tier info
            rank_info = self._get_rank_info(ctx.guild.id, profile["level"])
            
            # Math
            xp = profile["xp"]
            lvl = profile["level"]
            xp_for_current = level_to_xp(lvl)
            xp_for_next = level_to_xp(lvl + 1)
            
            progress = xp - xp_for_current
            required = xp_for_next - xp_for_current
            percentage = min(int((progress / required) * 100), 100)
            
            # Bar string
            filled = int(percentage / 10)
            bar = "█" * filled + "░" * (10 - filled)

            embed = discord.Embed(color=target.color or discord.Color.blurple())
            embed.set_author(name=f"{target.display_name}'s Rank", icon_url=target.display_avatar.url)
            
            desc = f"**{rank_info['emoji']}  {rank_info['label']}**"
            
            embed.description = desc
            embed.add_field(name="Level", value=str(lvl), inline=True)
            embed.add_field(name="XP", value=f"{xp:,}", inline=True)
            embed.add_field(name="Rank", value=f"#{rank_pos}" if rank_pos else "Unranked", inline=True)
            
            embed.add_field(name="Progress", value=f"`{bar}` {percentage}%\n*({progress:,} / {required:,} to Next)*", inline=False)
            
            if profile["streak_days"] > 0:
                embed.add_field(name="🔥 Streak", value=f"{profile['streak_days']} Days", inline=True)
                
            if earned_badges:
                embed.add_field(name="🎖️ Badges", value="\n".join(earned_badges), inline=True)

            await ctx.reply(embed=embed)

        except Exception as e:
            log.error(f"Rank command failed: {e}")
            await ctx.reply(embed=error_embed("Failed to fetch rank profile. Database might be down or unset."))

    @commands.hybrid_command(name="leaderboard", description="View the top XP earners.")
    async def leaderboard(self, ctx: commands.Context):
        """Displays the top 10 users with the highest XP in the server.
        
        Example:
        `!leaderboard`
        """
        try:
            db = get_db()
            res = db.table("xp_profiles").select("user_id, xp, level").eq("guild_id", ctx.guild.id).order("xp", desc=True).limit(10).execute()
            
            if not res.data:
                return await ctx.reply("Nobody has earned any XP yet!")
                
            desc_lines = []
            for idx, row in enumerate(res.data, 1):
                user = ctx.guild.get_member(row["user_id"])
                name = user.display_name if user else f"Unknown User ({row['user_id']})"
                
                # Check for podium badge
                medal = ""
                if idx == 1: medal = "🥇"
                elif idx == 2: medal = "🥈"
                elif idx == 3: medal = "🥉"
                else: medal = f"**#{idx}**"
                
                desc_lines.append(f"{medal} **{name}** — Lvl {row['level']} ({row['xp']:,} XP)")
                
            embed = discord.Embed(
                title=f"🏆 Leaderboard — {ctx.guild.name}",
                description="\n".join(desc_lines),
                color=discord.Color.gold()
            )
            await ctx.reply(embed=embed)
            
        except Exception as e:
            await ctx.reply(embed=error_embed("Failed to load leaderboard."))

    # ── Admin Commands ─────────────────────────────────────────────────────────

    @commands.hybrid_command(name="xp", description="Manually manage a user's XP.")
    @app_commands.describe(action="add, remove, or set", member="Target user", amount="Amount of XP")
    @has_mod_role()
    async def xp(self, ctx: commands.Context, action: str, member: discord.Member, amount: int):
        """Add, remove, or set a user's XP.
        This will trigger level up announcements and role swaps if applicable.
        
        Arguments:
        `action` — "add", "remove", or "set"
        `member` — The user to modify
        `amount` — The amount of XP
        
        Examples:
        `!xp add @user 500`
        `!xp set @user 0`
        """
        action = action.lower()
        if action not in ("add", "remove", "set"):
            return await ctx.reply(embed=error_embed("Action must be 'add', 'remove', or 'set'."))
            
        try:
            db = get_db()
            res = db.table("xp_profiles").select("xp").eq("guild_id", ctx.guild.id).eq("user_id", member.id).execute()
            current_xp = res.data[0]["xp"] if res.data else 0
            
            new_xp = current_xp
            if action == "add": new_xp += amount
            elif action == "remove": new_xp = max(0, new_xp - amount)
            elif action == "set": new_xp = max(0, amount)
            
            new_level = xp_to_level(new_xp)
            
            data = {
                "guild_id": ctx.guild.id,
                "user_id": member.id,
                "xp": new_xp,
                "level": new_level
            }
            if res.data:
                db.table("xp_profiles").update(data).eq("guild_id", ctx.guild.id).eq("user_id", member.id).execute()
            else:
                db.table("xp_profiles").insert(data).execute()
                
            old_level = xp_to_level(current_xp)
            if new_level != old_level:
                xp_cog = self.bot.get_cog("XP")
                if xp_cog:
                    # simulate an old profile so the role remover works
                    pseudo_profile = {"level": old_level}
                    await xp_cog._handle_level_up(member, new_level, pseudo_profile, ctx.message)
                    
                    # fetch actual full profile for badges
                    new_res = db.table("xp_profiles").select("*").eq("guild_id", ctx.guild.id).eq("user_id", member.id).execute()
                    if new_res.data:
                        await xp_cog._check_badges(member, new_res.data[0])
                
            await ctx.reply(embed=success_embed("XP Updated", f"{member.mention} is now Level {new_level} with {new_xp:,} XP."))
            
        except Exception as e:
            await ctx.reply(embed=error_embed("Database error while updating XP."))

    @commands.hybrid_command(name="editrank", description="Rename and re-emoji a rank level tier.")
    @has_mod_role()
    @app_commands.describe(level_min="Starting level of the rank range", name="New display name", emoji="New emoji (or custom ID)")
    async def editrank(self, ctx: commands.Context, level_min: int, level_max: int, name: str, emoji: str):
        """Change the name and emoji of a rank tier.
        
        Arguments:
        `level_min` — The level this rank starts at
        `level_max` — The highest level this rank covers
        `name` — The text name of the rank (e.g. "Wanderer")
        `emoji` — The emoji icon to use (e.g. "🚪" or a custom emoji ID)
        
        Examples:
        `!editrank 1 10 "Wanderer" "🚪"`
        """
        try:
            db = get_db()
            db.table("rank_tiers").upsert({
                "guild_id": ctx.guild.id,
                "level_min": level_min,
                "level_max": level_max,
                "label": name,
                "emoji": emoji
            }).execute()
            
            await ctx.reply(embed=success_embed("Rank Tier Updated", f"Levels {level_min}–{level_max} are now known as:\n**{emoji} {name}**"))
        except Exception as e:
            await ctx.reply(embed=error_embed("Database error while saving rank tier."))

    @commands.hybrid_command(name="setlevelrole", description="Assign a Discord role to a rank tier.")
    @has_mod_role()
    async def setlevelrole(self, ctx: commands.Context, level_min: int, role: discord.Role):
        """Automatically grant a discord role to users entering this rank tier.
        
        Note: The rank block must already be created via `/editrank` before you can attach a role to it.
        
        Arguments:
        `level_min` — The starting level of the rank
        `role` — The Discord role to grant (e.g. @Wanderer)
        
        Example:
        `!setlevelrole 1 @Wanderer`
        """
        try:
            db = get_db()
            # Must already exist or we need to upsert all fields
            res = db.table("rank_tiers").select("*").eq("guild_id", ctx.guild.id).eq("level_min", level_min).execute()
            if not res.data:
                return await ctx.reply(embed=error_embed(f"No rank tier exists starting at level {level_min}. Use `/editrank` to create it first!"))
                
            db.table("rank_tiers").update({"role_id": role.id}).eq("guild_id", ctx.guild.id).eq("level_min", level_min).execute()
            await ctx.reply(embed=success_embed("Role Bound", f"Users hitting level {level_min} will now receive {role.mention}."))
        except Exception as e:
            await ctx.reply(embed=error_embed("Failed to set level role."))

    @commands.hybrid_command(name="setbadgerole", description="Map a Discord role to an achievement badge.")
    @has_mod_role()
    @app_commands.choices(badge=[
        app_commands.Choice(name=name, value=key) for key, name in BADGE_KEYS.items()
    ])
    async def setbadgerole(self, ctx: commands.Context, badge: app_commands.Choice[str], role: discord.Role):
        """Automatically grant a Discord role when a user earns a badge.
        
        Arguments:
        `badge` — The badge to configure (use the command suggestions)
        `role` — The Discord role to grant
        
        Example:
        `!setbadgerole chatterbox @Chatterbox`
        """
        try:
            db = get_db()
            db.table("badge_roles").upsert({
                "guild_id": ctx.guild.id,
                "badge_key": badge.value,
                "role_id": role.id
            }).execute()
            
            await ctx.reply(embed=success_embed("Badge Role Saved", f"The **{badge.name}** badge will now grant {role.mention}."))
        except Exception:
            await ctx.reply(embed=error_embed("Failed to set badge role."))

    @commands.hybrid_command(name="synclevels", description="Force-sync all users with their exact 'Level X' role.")
    @has_mod_role()
    async def synclevels(self, ctx: commands.Context):
        """Loop through the database and ensure everyone has exactly their correct Level role.
        
        This will strip old "Level N" roles from users and dynamically create missing level roles.
        This may take a few minutes on large servers.
        
        Example:
        `!synclevels`
        """
        import asyncio
        
        await ctx.reply(embed=info_embed("Syncing Levels", "Starting level role sync. This might take a moment..."))
        
        try:
            db = get_db()
            res = db.table("xp_profiles").select("user_id, level").eq("guild_id", ctx.guild.id).execute()
        except Exception:
            return await ctx.send(embed=error_embed("Failed to fetch profiles from database."))
            
        profiles = res.data
        if not profiles:
            return await ctx.send(embed=info_embed("Sync Complete", "No users found in the database."))
            
        updated_count = 0
        failed_count = 0
        
        # Pre-fetch existing Level roles to minimize API calls
        level_roles = {r.name: r for r in ctx.guild.roles if r.name.startswith("Level ") or r.name == "Unranked"}
            
        for profile in profiles:
            member = ctx.guild.get_member(profile["user_id"])
            if not member:
                continue # User left the server
                
            target_level = profile["level"]
            target_role_name = "Unranked" if target_level == 0 else f"Level {target_level}"
            
            roles_to_add = []
            roles_to_remove = []
            
            # Find old level roles to remove
            for r in member.roles:
                if (r.name.startswith("Level ") or r.name == "Unranked") and r.name != target_role_name:
                    roles_to_remove.append(r)
                    
            # Check if they already have the target role
            has_target = any(r.name == target_role_name for r in member.roles)
            
            if not has_target:
                target_role = level_roles.get(target_role_name)
                
                if not target_role:
                    # Role doesn't exist in server yet, create it
                    try:
                        target_role = await ctx.guild.create_role(
                            name=target_role_name,
                            color=discord.Color.dark_grey(),
                            reason="Auto-created level role during sync"
                        )
                        level_roles[target_role_name] = target_role # Cache it
                    except discord.Forbidden:
                        failed_count += 1
                        continue
                        
                roles_to_add.append(target_role)
                
            if roles_to_add or roles_to_remove:
                try:
                    final_roles = [r for r in member.roles if r not in roles_to_remove]
                    for r in roles_to_add:
                        if r not in final_roles:
                            final_roles.append(r)
                            
                    await member.edit(roles=final_roles, reason="Level Sync")
                    updated_count += 1
                except discord.Forbidden:
                    failed_count += 1
                    
            # Yield to event loop to avoid rate limits
            await asyncio.sleep(0.1)
            
        await ctx.send(embed=success_embed(
            "Sync Complete", 
            f"Successfully synced **{updated_count}** members.\nFailed to sync **{failed_count}** members (usually due to missing permissions)."
        ))

async def setup(bot: commands.Bot):
    await bot.add_cog(Levels(bot))
