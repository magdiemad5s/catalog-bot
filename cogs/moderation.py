"""
cogs/moderation.py — Staff moderation commands.

Access is gated to members holding the role defined by MOD_ROLE_ID in .env.
Every action is also logged to the `mod_logs` table in Supabase.
"""
from __future__ import annotations

import datetime
import logging

import discord
from discord import app_commands
from discord.ext import commands

from utils import error_embed, success_embed, warning_embed

log = logging.getLogger(__name__)

# Role ID allowed to run moderation commands (set in .env as MOD_ROLE_ID)
MOD_ROLE_ID = 1482710375706263664


# ── Checks ─────────────────────────────────────────────────────────────────────

def has_mod_role():
    """Custom check: the invoker must have the designated mod role."""
    async def predicate(ctx: commands.Context) -> bool:
        # Server owner always passes
        if ctx.guild and ctx.author == ctx.guild.owner:
            return True
        role_ids = [r.id for r in getattr(ctx.author, "roles", [])]
        if MOD_ROLE_ID not in role_ids:
            raise commands.CheckFailure(
                f"You need the <@&{MOD_ROLE_ID}> role to use this command."
            )
        return True
    return commands.check(predicate)


async def _log_action(bot: commands.Bot, guild_id: int, action: str,
                      target_id: int, moderator_id: int, reason: str) -> None:
    """Write a row to mod_logs in Supabase (silently skips if DB not connected)."""
    try:
        from db import get_db
        get_db().table("mod_logs").insert({
            "guild_id":     guild_id,
            "action":       action,
            "target_id":    target_id,
            "moderator_id": moderator_id,
            "reason":       reason,
        }).execute()
    except RuntimeError:
        pass   # DB not initialised — skip silently
    except Exception as exc:
        log.warning("Failed to write mod log: %s", exc)


# ── Cog ────────────────────────────────────────────────────────────────────────

class Moderation(commands.Cog, name="Moderation"):
    """🔨 Moderation tools (mod-role only)."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _outranks(self, ctx: commands.Context, member: discord.Member) -> bool:
        """True when the author's top role is above the target's top role."""
        return member.top_role < ctx.author.top_role

    # ── /kick ──────────────────────────────────────────────────────────────────
    @commands.hybrid_command(name="kick", description="Kick a member from the server.")
    @has_mod_role()
    @app_commands.describe(member="The member to kick", reason="Reason for kick")
    async def kick(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"):
        if not self._outranks(ctx, member):
            return await ctx.reply(embed=error_embed("You can't kick someone with an equal or higher role."))
        try:
            await member.kick(reason=reason)
            await _log_action(self.bot, ctx.guild.id, "kick", member.id, ctx.author.id, reason)
            embed = success_embed("Member Kicked", f"**{member}** was kicked.")
            embed.add_field(name="Reason",    value=reason,             inline=True)
            embed.add_field(name="Moderator", value=ctx.author.mention, inline=True)
            await ctx.reply(embed=embed)
        except discord.Forbidden:
            await ctx.reply(embed=error_embed("I don't have permission to kick that member."))

    # ── /ban ───────────────────────────────────────────────────────────────────
    @commands.hybrid_command(name="ban", description="Ban a member from the server.")
    @has_mod_role()
    @app_commands.describe(member="The member to ban", reason="Reason for ban")
    async def ban(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"):
        if not self._outranks(ctx, member):
            return await ctx.reply(embed=error_embed("You can't ban someone with an equal or higher role."))
        try:
            await member.ban(reason=reason)
            await _log_action(self.bot, ctx.guild.id, "ban", member.id, ctx.author.id, reason)
            embed = success_embed("Member Banned", f"**{member}** was banned.")
            embed.add_field(name="Reason",    value=reason,             inline=True)
            embed.add_field(name="Moderator", value=ctx.author.mention, inline=True)
            await ctx.reply(embed=embed)
        except discord.Forbidden:
            await ctx.reply(embed=error_embed("I don't have permission to ban that member."))

    # ── /unban ─────────────────────────────────────────────────────────────────
    @commands.hybrid_command(name="unban", description="Unban a user by their ID or user#tag.")
    @has_mod_role()
    @app_commands.describe(user="User ID or user#tag to unban")
    async def unban(self, ctx: commands.Context, *, user: str):
        target = None
        async for entry in ctx.guild.bans():
            if str(entry.user.id) == user or str(entry.user) == user:
                target = entry.user
                break
        if target is None:
            return await ctx.reply(embed=error_embed("No banned user found with that ID or tag."))
        await ctx.guild.unban(target)
        await _log_action(self.bot, ctx.guild.id, "unban", target.id, ctx.author.id, "Unbanned")
        embed = success_embed("User Unbanned", f"**{target}** has been unbanned.")
        embed.add_field(name="Moderator", value=ctx.author.mention)
        await ctx.reply(embed=embed)

    # ── /timeout ───────────────────────────────────────────────────────────────
    @commands.hybrid_command(name="timeout", description="Timeout a member for a given number of minutes.")
    @has_mod_role()
    @app_commands.describe(
        member="The member to timeout",
        minutes="Duration in minutes (max 40320 = 28 days)",
        reason="Reason for timeout",
    )
    async def timeout(self, ctx: commands.Context, member: discord.Member,
                      minutes: int = 10, *, reason: str = "No reason provided"):
        if not self._outranks(ctx, member):
            return await ctx.reply(embed=error_embed("You can't timeout someone with an equal or higher role."))
        minutes = max(1, min(minutes, 40320))
        until = discord.utils.utcnow() + datetime.timedelta(minutes=minutes)
        try:
            await member.timeout(until, reason=reason)
            await _log_action(self.bot, ctx.guild.id, "timeout", member.id, ctx.author.id, reason)
            embed = success_embed("Member Timed Out", f"**{member}** timed out for **{minutes}min**.")
            embed.add_field(name="Reason",    value=reason,             inline=True)
            embed.add_field(name="Moderator", value=ctx.author.mention, inline=True)
            await ctx.reply(embed=embed)
        except discord.Forbidden:
            await ctx.reply(embed=error_embed("I don't have permission to timeout that member."))

    # ── /untimeout ─────────────────────────────────────────────────────────────
    @commands.hybrid_command(name="untimeout", description="Remove a timeout from a member.")
    @has_mod_role()
    @app_commands.describe(member="The member to untimeout")
    async def untimeout(self, ctx: commands.Context, member: discord.Member):
        await member.timeout(None)
        await _log_action(self.bot, ctx.guild.id, "untimeout", member.id, ctx.author.id, "Timeout removed")
        await ctx.reply(embed=success_embed("Timeout Removed", f"**{member}**'s timeout has been lifted."))

    # ── /purge ─────────────────────────────────────────────────────────────────
    @commands.hybrid_command(name="purge", description="Bulk delete messages in this channel.")
    @has_mod_role()
    @app_commands.describe(amount="Number of messages to delete (max 1000)")
    async def purge(self, ctx: commands.Context, amount: int = 10):
        """Bulk delete messages from the current channel.
        
        Note: Discord does not allow bots to natively bulk-delete messages older than 14 days.
        
        Arguments:
        `amount` — The number of messages to permanently delete (up to 1000)
        
        Examples:
        `!purge` (Deletes exactly 10 by default)
        `!purge 100` (Deletes the last 100)
        """
        amount = max(1, min(amount, 1000))
        try:
            # We add 1 to amount to account for the actual !purge command message itself
            deleted = await ctx.channel.purge(limit=amount + 1)
            
            # The logic below accounts for slash commands where there is no invocation message to delete
            num_deleted = len(deleted)
            if ctx.interaction is None:
                num_deleted -= 1
                
            await ctx.send(
                embed=success_embed("Messages Purged", f"Deleted **{max(0, num_deleted)}** message(s)."),
                delete_after=5,
            )
        except discord.HTTPException as e:
            if e.code == 50034:
                return await ctx.reply(embed=error_embed("Discord prevents bots from bulk-deleting messages older than 14 days. Older messages were kept."))
            await ctx.reply(embed=error_embed(f"Failed to purge messages: {e.text}"))

    # ── Error handler ──────────────────────────────────────────────────────────
    async def cog_command_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.CheckFailure):
            await ctx.reply(embed=error_embed(str(error)), ephemeral=True)
        else:
            raise error   # let the global handler deal with it


async def setup(bot: commands.Bot):
    await bot.add_cog(Moderation(bot))
