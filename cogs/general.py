"""
cogs/general.py — Core utility commands available in every server.
Contains: help, ping, info
"""
from __future__ import annotations

import discord
from discord.ext import commands

from utils import info_embed, error_embed


class General(commands.Cog, name="General"):
    """ℹ️ General-purpose commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── /ping ──────────────────────────────────────────────────────────────────
    @commands.hybrid_command(name="ping", description="Check the bot's latency.")
    async def ping(self, ctx: commands.Context):
        ws = round(self.bot.latency * 1000)
        colour = discord.Color.green() if ws < 150 else discord.Color.orange()
        embed = discord.Embed(title="🏓 Pong!", description=f"**WebSocket:** `{ws}ms`", color=colour)
        await ctx.reply(embed=embed)

    # ── /help ──────────────────────────────────────────────────────────────────
    @commands.hybrid_command(name="help", description="List all available commands or get help for a specific command.")
    @discord.app_commands.describe(command_name="The specific command to get help for.")
    async def help_cmd(self, ctx: commands.Context, command_name: str = None):
        prefix = self.bot.command_prefix
        
        if command_name:
            cmd = self.bot.get_command(command_name.lower())
            if not cmd or getattr(cmd, "hidden", False):
                return await ctx.reply(embed=error_embed(f"Command `{command_name}` not found."))
            
            embed = discord.Embed(
                title=f"Command: {prefix}{cmd.name}",
                description=cmd.help or cmd.description or "No detailed description available.",
                color=discord.Color.blurple()
            )
            if cmd.aliases:
                embed.add_field(name="Aliases", value=", ".join(cmd.aliases), inline=False)
            embed.set_footer(text=f"Requested by {ctx.author}", icon_url=ctx.author.display_avatar.url)
            return await ctx.reply(embed=embed)
            
        embed = discord.Embed(
            title="📖 Help — The Library Bot",
            description=f"Prefix: `{prefix}` · Slash commands also work (`/`)\nUse `{prefix}help <command>` for more details.",
            color=discord.Color.blurple(),
        )
        embed.set_footer(
            text=f"Requested by {ctx.author}",
            icon_url=ctx.author.display_avatar.url,
        )

        grouped: dict[str, list[str]] = {}
        for cmd in sorted(self.bot.commands, key=lambda c: c.name):
            if cmd.hidden:
                continue
            cog_name = cmd.cog_name or "Misc"
            grouped.setdefault(cog_name, []).append(f"`{prefix}{cmd.name}`")

        for cog_name, cmds in sorted(grouped.items()):
            embed.add_field(name=cog_name, value=" · ".join(cmds), inline=False)

        await ctx.reply(embed=embed)

    # ── /info ──────────────────────────────────────────────────────────────────
    @commands.hybrid_command(name="info", description="Show info about the bot.")
    async def info(self, ctx: commands.Context):
        embed = info_embed("The Library Bot")
        embed.add_field(name="Servers",  value=str(len(self.bot.guilds)),                              inline=True)
        embed.add_field(name="Users",    value=str(sum(g.member_count or 0 for g in self.bot.guilds)), inline=True)
        embed.add_field(name="Prefix",   value=f"`{self.bot.command_prefix}`",                        inline=True)
        embed.add_field(name="Latency",  value=f"`{round(self.bot.latency * 1000)}ms`",               inline=True)
        embed.set_footer(text="discord.py")
        await ctx.reply(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(General(bot))
