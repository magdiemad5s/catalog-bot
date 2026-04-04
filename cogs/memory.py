from __future__ import annotations

import logging
import asyncio
from datetime import datetime, timezone

import discord
from discord.ext import commands, tasks

from db.client import get_db

log = logging.getLogger(__name__)

class Memory(commands.Cog, name="Memory"):
    """🧠 Passive background tracking and profiling for Catalog."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Start the background task once the cog is loaded
        self.profile_monitor.start()

    def cog_unload(self):
        # Ensure the task cancels when the cog unloads
        self.profile_monitor.cancel()

    @tasks.loop(hours=12)
    async def profile_monitor(self):
        """
        Background loop that checks every 12 hours if users have updated PFP or bio.
        If changes exist, updates 'recent_flags' in the DB so AI can comment on it.
        """
        # Wait until the bot is fully ready before doing DB or API calls
        await self.bot.wait_until_ready()
        await self._run_profile_sweep()

    async def _run_profile_sweep(self, guild: discord.Guild | None = None):
        db = get_db()
        log.info("Starting up profile monitor sweep...")
        
        try:
            # If guild is provided (e.g., from !syncprofile), ensure all members are in DB first
            if guild:
                response = db.table("user_profiles").select("user_id").execute()
                known_users = {row["user_id"] for row in response.data}
                
                new_users = []
                for member in guild.members:
                    if not member.bot and member.id not in known_users:
                        new_users.append({
                            "user_id": member.id,
                            "avatar_hash": member.avatar.key if member.avatar else None,
                            "profile_bio": f"{member.name}::{member.global_name}",
                            "last_checked": datetime.now(timezone.utc).isoformat()
                        })
                
                if new_users:
                    # Supabase bulk insert
                    # Max 1000 per insert usually, but let's be safe and insert in chunks if needed
                    for i in range(0, len(new_users), 500):
                        db.table("user_profiles").upsert(new_users[i:i+500]).execute()
                    log.info(f"Inserted {len(new_users)} new user profiles during manual sync.")

            # 1. Fetch all known user profiles from the DB
            response = db.table("user_profiles").select("user_id, avatar_hash, profile_bio, recent_flags").execute()
            rows = response.data

            updated_count = 0
            
            for row in rows:
                user_id = row["user_id"]
                
                # Fetch user from Discord
                user = self.bot.get_user(user_id)
                if not user:
                    try:
                        user = await self.bot.fetch_user(user_id)
                    except discord.errors.NotFound:
                        continue
                    except Exception as e:
                        log.warning(f"Error fetching user {user_id}: {e}")
                        continue
                
                if user.bot:
                    continue

                # 2. Check for changes
                avatar_hash = user.avatar.key if user.avatar else None
                name_hash = f"{user.name}::{user.global_name}"
                
                old_avatar = row.get("avatar_hash")
                old_bio = row.get("profile_bio")
                
                # If these are None, we just learned them for the first time
                if old_avatar is None and old_bio is None:
                    db.table("user_profiles").update({
                        "avatar_hash": avatar_hash,
                        "profile_bio": name_hash,
                        "last_checked": datetime.now(timezone.utc).isoformat()
                    }).eq("user_id", user_id).execute()
                    continue

                changed_avatar = (old_avatar and avatar_hash != old_avatar)
                changed_bio = (old_bio and name_hash != old_bio)
                
                # 3. If there were changes, update DB with flag
                if changed_avatar or changed_bio:
                    flags = []
                    # Append strictly to existing flags so we don't wipe out unread ones!
                    existing_flags = row.get("recent_flags", "")
                    if existing_flags:
                        flags.append(existing_flags)
                        
                    if changed_avatar:
                        flags.append("User recently changed their profile picture/avatar.")
                    if changed_bio:
                        flags.append(f"User recently changed their display name to '{user.display_name}'.")
                    
                    flag_str = " ".join(flags)
                    
                    # Update DB
                    db.table("user_profiles").update({
                        "avatar_hash": avatar_hash,
                        "profile_bio": name_hash,
                        "recent_flags": flag_str,
                        "last_checked": datetime.now(timezone.utc).isoformat()
                    }).eq("user_id", user_id).execute()
                    
                    updated_count += 1
            
            log.info(f"Profile sweep completed. Updated {updated_count} existing profiles.")
            return updated_count
            
        except Exception as e:
            log.error(f"Error in profile sweep: {e}")
            return -1

    @commands.command(name="syncprofile", help="Force syncs all missing user profiles in the server and checks for updates.")
    @commands.has_permissions(administrator=True)
    async def syncprofile_cmd(self, ctx: commands.Context):
        msg = await ctx.reply("🔄 Syncing profiles and checking for updates... This might take a moment.")
        
        async with ctx.typing():
            updated = await self._run_profile_sweep(guild=ctx.guild)
        
        if updated >= 0:
            await msg.edit(content=f"✅ Sync complete! Checked all members and found {updated} updated profiles.")
        else:
            await msg.edit(content="❌ An error occurred while syncing profiles. Check console logs.")

    @profile_monitor.before_loop
    async def before_profile_monitor(self):
        """Wait for the bot to be ready before starting the loop."""
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(Memory(bot))
