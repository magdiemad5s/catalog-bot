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

from utils.embeds import error_embed

log = logging.getLogger(__name__)

class Admin(commands.Cog, name="Admin"):
    """🛠️ Administrator utilities."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

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
