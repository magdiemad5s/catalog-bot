"""
cogs/admin.py — Admin utilities and auto-update command.
"""
from __future__ import annotations

import os
import sys
import subprocess
import logging
import discord
from discord.ext import commands

import bcrypt
from utils.embeds import error_embed

log = logging.getLogger(__name__)

class Admin(commands.Cog, name="Admin"):
    """🛠️ Administrator utilities."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.hybrid_command(name="seedadmin", description="Seed a new admin account for the web panel.")
    @commands.has_permissions(administrator=True)
    async def seedadmin(self, ctx: commands.Context, username: str, password: str):
        """Securely hashes and stores an admin credential into the database.
        
        Recommended to use as a slash command or in a private channel.
        """
        if not ctx.guild:
            await ctx.send("❌ This command must be used in a server.", ephemeral=True)
            return

        # Delete message if it's a prefix command to protect the password
        if ctx.interaction is None:
            try:
                await ctx.message.delete()
            except discord.Forbidden:
                pass
                
        # Hash password
        salt = bcrypt.gensalt()
        hashed = bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')
        
        try:
            from db.client import get_db
            db = get_db()
            # We use asyncio.to_thread because supabase-py is synchronous
            import asyncio
            await asyncio.to_thread(
                lambda: db.table("admin_users").upsert({
                    "username": username,
                    "password_hash": hashed,
                    "role": "GUILD_ADMIN",
                    "guild_id": ctx.guild.id,
                    "requires_password_change": True,
                    "requires_setup": True
                }, on_conflict="username").execute()
            )
            
            await ctx.send(f"✅ Admin account '**{username}**' seeded successfully for this server.\nPlease log in to the web dashboard to complete the mandatory setup.", ephemeral=True)
            log.info(f"Admin account '{username}' seeded by {ctx.author} for guild {ctx.guild.id}.")
        except Exception as e:
            await ctx.send(f"❌ Database error: {e}", ephemeral=True)
            log.error(f"Failed to seed admin: {e}")

    @commands.hybrid_command(name="update", description="Pulls latest code from GitHub and restarts the bot cleanly.")
    @commands.has_permissions(administrator=True)
    async def update_cmd(self, ctx: commands.Context):
        embed = discord.Embed(
            title="🔄 System Update",
            description="Initiating `git pull` sequence...",
            color=discord.Color.blurple()
        )
        msg = await ctx.reply(embed=embed)
        
        try:
            # 1. Pull the latest from the working branch
            result = subprocess.run(
                ["git", "pull"],
                capture_output=True,
                text=True,
                check=True
            )
            out = result.stdout.strip()
            
            if "Already up to date." in out:
                embed.description = f"```\n{out}\n```\nRebooting the python process anyway to collect local changes..."
                await msg.edit(embed=embed)
            else:
                embed.description = f"**Update Pulled Successfully:**\n```\n{out}\n```\nExecuting clean reboot..."
                await msg.edit(embed=embed)
                
        except subprocess.CalledProcessError as e:
            err = e.stderr.strip() or e.stdout.strip()
            log.error(f"Git pull failed: {err}")
            await msg.edit(embed=error_embed(f"Git Pull Failed:\n```\n{err}\n```\nUpdate aborted."))
            return
            
        # 2. Update Python dependencies via pip
        try:
            embed.description += "\n\n📦 Checking for dependency updates..."
            await msg.edit(embed=embed)
            
            pip_result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "-r", "requirements.txt"],
                capture_output=True,
                text=True,
                check=True
            )
            # Only append pip logs if there was an actual installation/change, otherwise it clogs the UI
            if "Requirement already satisfied" not in pip_result.stdout or "Successfully installed" in pip_result.stdout:
                log.info("Dependencies updated via pip.")
                embed.description += "\n✅ Dependencies synchronized."
                await msg.edit(embed=embed)
        except subprocess.CalledProcessError as e:
            err = e.stderr.strip() or e.stdout.strip()
            log.warning(f"Pip install failed: {err}")
            embed.description += f"\n⚠️ **Pip Warning:** Failed to install dependencies. You may need to manual install.\n```\n{err}\n```"
            await msg.edit(embed=embed)

        # 3. Flush logs
        sys.stdout.flush()
        sys.stderr.flush()
        
        # 3. Process replacement (no zombies)
        try:
            log.info("Executing os.execv to restart python environment...")
            os.execv(sys.executable, ['python'] + sys.argv)
        except Exception as e:
            await msg.edit(embed=error_embed(f"os.execv failed to trigger reboot: `{e}`"))
            log.error(f"Restart error: {e}")

async def setup(bot: commands.Bot):
    await bot.add_cog(Admin(bot))
