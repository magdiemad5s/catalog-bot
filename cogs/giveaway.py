"""
cogs/giveaway.py — Library Member role assignment & Giveaway system.

Commands:
  !giverole              — assign Library Member role to yourself (must have card)
  !giverole @member      — assign to a specific member (must have card)
  !giverole auto         — bulk-assign to EVERY member with has_library_card=true in DB
  !giveaway "reward" rolename — Picks a random member from the given role, routes
                                a confirmation through the moderator-only channel,
                                then posts the winner to the giveaway channel.
"""
from __future__ import annotations

import asyncio
import logging
import random
import time

import discord
from discord.ext import commands, tasks

from db.client import get_db
from utils import error_embed, success_embed, info_embed, warning_embed

log = logging.getLogger(__name__)

# ── Channel / Role constants ───────────────────────────────────────────────────
GIVEAWAY_CHANNEL_ID   = 1492314197378203698   # #giveaway
MOD_CHANNEL_ID        = 1482973008812441623   # #moderator-only
LIBRARY_MEMBER_ROLE   = "Library Member"

# Reactions used in the mod-confirmation flow
REACT_POST    = "✅"
REACT_CANCEL  = "❌"
REACT_EDIT    = "✏️"

# Pending giveaways older than this (seconds) are auto-expired
PENDING_TTL_SECONDS = 3600  # 1 hour



# ── Cog ───────────────────────────────────────────────────────────────────────

class Giveaway(commands.Cog, name="Giveaway"):
    """🎉 Library Member role assignment and giveaway management."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Tracks active giveaway confirmation messages awaiting mod reaction.
        # Maps message_id -> {reward, winner, giveaway_channel, mod_channel, created_at}
        self._pending_giveaways: dict[int, dict] = {}
        self._cleanup_stale.start()

    def cog_unload(self):
        self._cleanup_stale.cancel()

    @tasks.loop(minutes=5)
    async def _cleanup_stale(self):
        """Remove pending giveaways older than PENDING_TTL_SECONDS."""
        now = time.time()
        stale = [
            mid for mid, data in self._pending_giveaways.items()
            if now - data.get("created_at", now) > PENDING_TTL_SECONDS
        ]
        for mid in stale:
            data = self._pending_giveaways.pop(mid, None)
            if data:
                log.info(
                    f"Expired stale giveaway: reward='{data['reward']}' "
                    f"(pending > {PENDING_TTL_SECONDS // 60} min)"
                )

    @_cleanup_stale.before_loop
    async def _before_cleanup(self):
        await self.bot.wait_until_ready()

    # ── !giverole ─────────────────────────────────────────────────────────────

    @commands.command(name="giverole")
    @commands.has_permissions(manage_roles=True)
    async def giverole(self, ctx: commands.Context, *, target_input: str = None):
        """Assign the Library Member role to members who completed their library card.

        Usage:
          !giverole              — assign to yourself
          !giverole @username    — assign to a specific member
          !giverole 123456789    — assign by user ID
          !giverole auto         — bulk-assign to everyone with has_library_card=true in DB
        """
        # ── Branch: bulk mode ──────────────────────────────────────────────────
        if target_input and target_input.strip().lower() == "auto":
            await self._giverole_auto(ctx)
            return

        # ── Single-member mode ─────────────────────────────────────────────────
        if target_input:
            try:
                target = await commands.MemberConverter().convert(ctx, target_input)
            except commands.MemberNotFound:
                return await ctx.reply(
                    embed=error_embed(f"Could not find member: `{target_input}`")
                )
        else:
            target = ctx.author

        await self._giverole_single(ctx, target)

    # ── Single-member helper ───────────────────────────────────────────────────

    async def _giverole_single(self, ctx: commands.Context, target: discord.Member):
        """Internal: verify card and assign role to one member."""
        # ── Verify library card completion via DB ──────────────────────────────
        try:
            db = get_db()
            res = (
                db.table("user_profiles")
                  .select("has_library_card")
                  .eq("user_id", target.id)
                  .execute()
            )
            has_card = bool(res.data and res.data[0].get("has_library_card"))
        except Exception as e:
            log.error(f"DB check failed for giverole on {target.id}: {e}")
            return await ctx.reply(
                embed=error_embed(
                    f"Database error while verifying library card. Please try again.\n`{e}`"
                )
            )

        if not has_card:
            return await ctx.reply(
                embed=error_embed(
                    f"{target.display_name} has not completed the library card setup yet.\n"
                    "They must finish the onboarding interview before receiving the Library Member role."
                )
            )

        role = await self._ensure_role(ctx)
        if not role:
            return

        if role in target.roles:
            return await ctx.reply(
                embed=warning_embed(
                    "Already a Member",
                    f"{target.mention} already has the **{LIBRARY_MEMBER_ROLE}** role.",
                )
            )

        try:
            await target.add_roles(role, reason=f"Library card setup verified by {ctx.author}")
            embed = success_embed(
                "Library Member Assigned",
                f"{target.mention} has been inducted as a **{LIBRARY_MEMBER_ROLE}**! 📚",
            )
            embed.add_field(name="Assigned by", value=ctx.author.mention, inline=True)
            await ctx.reply(embed=embed)
            log.info(f"Assigned '{LIBRARY_MEMBER_ROLE}' to {target.name} in {ctx.guild.name}.")
        except discord.Forbidden:
            await ctx.reply(embed=error_embed("I don't have permission to assign that role."))

    # ── Bulk (auto) helper ─────────────────────────────────────────────────────

    async def _giverole_auto(self, ctx: commands.Context):
        """Internal: bulk-assign Library Member to all users with has_library_card=true."""
        await ctx.reply(
            embed=info_embed(
                "Bulk Role Assignment Started",
                "Fetching all members with a completed library card from the database…"
            )
        )

        # ── Pull all card-holders from DB ──────────────────────────────────────
        try:
            db = get_db()
            res = (
                db.table("user_profiles")
                  .select("user_id")
                  .eq("has_library_card", True)
                  .execute()
            )
            card_holder_ids = set()
            for row in (res.data or []):
                try:
                    card_holder_ids.add(int(row["user_id"]))
                except (TypeError, ValueError) as conv_err:
                    log.warning(f"[giverole auto] Skipping bad user_id {row.get('user_id')}: {conv_err}")
        except Exception as e:
            log.error(f"DB fetch failed for giverole auto: {e}")
            return await ctx.send(
                embed=error_embed(f"Failed to fetch data from the database: `{e}`")
            )

        if not card_holder_ids:
            return await ctx.send(
                embed=warning_embed(
                    "No Card Holders Found",
                    "No users with `has_library_card = true` were found in the database."
                )
            )

        role = await self._ensure_role(ctx)
        if not role:
            return

        # Ensure the member cache is fully populated (requires members intent)
        if not ctx.guild.chunked:
            await ctx.guild.chunk()

        # ── Iterate guild members and assign ──────────────────────────────────
        assigned   = 0
        skipped    = 0
        not_found  = 0
        failed     = 0

        for uid in card_holder_ids:
            member = ctx.guild.get_member(uid)
            if member is None:
                not_found += 1
                continue
            if role in member.roles:
                skipped += 1
                continue
            try:
                await member.add_roles(
                    role,
                    reason=f"!giverole auto — library card verified · triggered by {ctx.author}"
                )
                assigned += 1
                log.info(f"[giverole auto] Assigned '{LIBRARY_MEMBER_ROLE}' to {member.name}.")
                # Throttle: Discord rate limit is ~5 role edits/sec
                await asyncio.sleep(0.4)
            except discord.Forbidden:
                failed += 1
                log.warning(f"[giverole auto] Forbidden assigning role to {uid}.")
            except Exception as e:
                failed += 1
                log.error(f"[giverole auto] Error assigning role to {uid}: {e}")

        # ── Summary report ─────────────────────────────────────────────────────
        embed = success_embed(
            "Bulk Role Assignment Complete",
            f"Processed **{len(card_holder_ids)}** card holder(s) from the database.",
        )
        embed.add_field(name="✅ Assigned",              value=str(assigned),  inline=True)
        embed.add_field(name="⏭️ Already had role",      value=str(skipped),   inline=True)
        embed.add_field(name="❓ Not in server",         value=str(not_found), inline=True)
        if failed:
            embed.add_field(name="❌ Failed (permissions)", value=str(failed), inline=True)
        embed.set_footer(text=f"Triggered by {ctx.author.display_name}")
        await ctx.send(embed=embed)
        log.info(
            f"[giverole auto] Done. assigned={assigned}, skipped={skipped}, "
            f"not_found={not_found}, failed={failed}."
        )

    # ── Shared role resolver ───────────────────────────────────────────────────

    async def _ensure_role(self, ctx: commands.Context) -> discord.Role | None:
        """Return the Library Member role, creating it if absent. Returns None on error."""
        role = discord.utils.get(ctx.guild.roles, name=LIBRARY_MEMBER_ROLE)
        if not role:
            try:
                role = await ctx.guild.create_role(
                    name=LIBRARY_MEMBER_ROLE,
                    color=discord.Color.from_str("#8B6CAF"),
                    reason="Auto-created by !giverole command",
                )
                log.info(f"Created role '{LIBRARY_MEMBER_ROLE}' in {ctx.guild.name}.")
            except discord.Forbidden:
                await ctx.send(embed=error_embed("I don't have permission to create roles."))
                return None
        return role

    # ── !giveaway ─────────────────────────────────────────────────────────────

    @commands.command(name="giveaway")
    @commands.has_permissions(manage_roles=True)
    async def giveaway(self, ctx: commands.Context, reward: str, *, role_name: str):
        """Pick a random member from a role and route the winner through mod approval.

        Usage:
          !giveaway "Weekend Pass" Library Member
          !giveaway "Nitro" VIP
        """
        # ── Find the target role ───────────────────────────────────────────────
        role = discord.utils.get(ctx.guild.roles, name=role_name)
        if not role:
            return await ctx.reply(
                embed=error_embed(f"Role **{role_name}** not found in this server.")
            )

        # ── Reject duplicate giveaways for the same role ───────────────────────
        active_roles = {v["role"].id for v in self._pending_giveaways.values()}
        if role.id in active_roles:
            return await ctx.reply(
                embed=warning_embed(
                    "Giveaway Already Pending",
                    f"There's already an active giveaway pending for **{role.name}**. "
                    "Approve or cancel it before starting a new one.",
                )
            )

        # ── Filter eligible members (in role, not a bot) ───────────────────────
        candidates = [m for m in role.members if not m.bot]
        if not candidates:
            return await ctx.reply(
                embed=error_embed(f"No eligible members found in the **{role_name}** role.")
            )

        # ── Pick a winner ──────────────────────────────────────────────────────
        winner = random.choice(candidates)

        # ── Find the mod channel ───────────────────────────────────────────────
        mod_channel_id = MOD_CHANNEL_ID
        try:
            db = get_db()
            res = db.table("guild_configs").select("mod_channel_id").eq("guild_id", ctx.guild.id).execute()
            if res.data and res.data[0].get("mod_channel_id"):
                mod_channel_id = int(res.data[0]["mod_channel_id"])
        except Exception:
            pass

        mod_channel = ctx.guild.get_channel(mod_channel_id)
        if not mod_channel:
            return await ctx.reply(
                embed=error_embed(
                    f"Moderator channel (ID: `{mod_channel_id}`) not found. "
                    "Please verify the channel ID."
                )
            )

        # ── Send confirmation embed to mods ───────────────────────────────────
        confirm_embed = discord.Embed(
            title="🎁 Giveaway — Awaiting Moderator Approval",
            color=discord.Color.from_str("#FFD700"),
        )
        confirm_embed.add_field(name="🏆 Reward",       value=f"**{reward}**",               inline=True)
        confirm_embed.add_field(name="🎲 Eligible Pool", value=f"**{len(candidates)}** members from **{role.name}**", inline=True)
        confirm_embed.add_field(name="🎉 Selected Winner", value=winner.mention,             inline=False)
        confirm_embed.add_field(
            name="Actions",
            value=(
                f"{REACT_POST}  — Post result to <#{GIVEAWAY_CHANNEL_ID}>\n"
                f"{REACT_CANCEL}  — Cancel the giveaway\n"
                f"{REACT_EDIT}  — Re-roll & pick a different winner"
            ),
            inline=False,
        )
        confirm_embed.set_footer(text=f"Giveaway initiated by {ctx.author.display_name}")
        if winner.display_avatar:
            confirm_embed.set_thumbnail(url=winner.display_avatar.url)

        try:
            mod_msg = await mod_channel.send(embed=confirm_embed)
            await mod_msg.add_reaction(REACT_POST)
            await mod_msg.add_reaction(REACT_CANCEL)
            await mod_msg.add_reaction(REACT_EDIT)
        except discord.Forbidden:
            return await ctx.reply(
                embed=error_embed(f"I can't send messages to <#{mod_channel_id}>.")
            )

        # ── Track pending confirmation ─────────────────────────────────────────
        self._pending_giveaways[mod_msg.id] = {
            "reward":     reward,
            "role":       role,
            "winner":     winner,
            "pool":       candidates,
            "initiator":  ctx.author,
            "mod_channel_id": mod_channel_id,
            "created_at": time.time(),
        }

        await ctx.reply(
            embed=info_embed(
                "Giveaway Pending Approval",
                f"A winner has been selected from the **{role.name}** role.\n"
                f"Moderators in <#{mod_channel_id}> have been notified to review and approve.",
            )
        )
        log.info(
            f"Giveaway pending: reward='{reward}', winner={winner.name}, "
            f"role={role.name}, mod_msg={mod_msg.id}"
        )

    # ── Reaction listener (mod confirmation flow) ──────────────────────────────

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        # Ignore bot's own reactions
        if payload.user_id == self.bot.user.id:
            return

        # Only process reactions in the mod channel
        mod_channel_id = MOD_CHANNEL_ID
        try:
            db = get_db()
            res = db.table("guild_configs").select("mod_channel_id").eq("guild_id", payload.guild_id).execute()
            if res.data and res.data[0].get("mod_channel_id"):
                mod_channel_id = int(res.data[0]["mod_channel_id"])
        except Exception:
            pass

        if payload.channel_id != mod_channel_id:
            # Also check if it matches the tracked giveaway's mod channel, if available
            data = self._pending_giveaways.get(payload.message_id)
            if not data or payload.channel_id != data.get("mod_channel_id", mod_channel_id):
                return

        # Only care about messages we're tracking
        if payload.message_id not in self._pending_giveaways:
            return

        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            log.warning(f"on_raw_reaction_add: guild {payload.guild_id} not in cache.")
            return

        # Must be a moderator (manage_messages permission)
        mod_member = guild.get_member(payload.user_id)
        if not mod_member:
            try:
                mod_member = await guild.fetch_member(payload.user_id)
            except discord.NotFound:
                return
        if not mod_member.guild_permissions.manage_messages:
            return

        emoji = str(payload.emoji)
        if emoji not in [REACT_POST, REACT_CANCEL, REACT_EDIT]:
            return

        # Atomically claim this giveaway to prevent race conditions
        # (two mods reacting simultaneously across await boundaries)
        if emoji in [REACT_POST, REACT_CANCEL]:
            data = self._pending_giveaways.pop(payload.message_id, None)
            if data is None:
                return  # another reaction already handled it
        else:
            # Re-roll: keep the entry (we'll update it in-place)
            data = self._pending_giveaways.get(payload.message_id)
            if data is None:
                return

        mod_channel_id = data.get("mod_channel_id", payload.channel_id)
        mod_channel = guild.get_channel(mod_channel_id)

        # Fetch the original mod confirmation message so we can delete / update it
        mod_msg = None
        try:
            if mod_channel:
                mod_msg = await mod_channel.fetch_message(payload.message_id)
        except (discord.NotFound, AttributeError):
            mod_msg = None

        reward    = data["reward"]
        role      = data["role"]
        winner    = data["winner"]
        pool      = data["pool"]
        initiator = data["initiator"]

        # ── ✅ Post the winner ─────────────────────────────────────────────────
        if emoji == REACT_POST:
            giveaway_channel_id = GIVEAWAY_CHANNEL_ID
            try:
                db = get_db()
                res = db.table("guild_configs").select("giveaway_channel_id").eq("guild_id", guild.id).execute()
                if res.data and res.data[0].get("giveaway_channel_id"):
                    giveaway_channel_id = int(res.data[0]["giveaway_channel_id"])
            except Exception:
                pass
                
            giveaway_channel = guild.get_channel(giveaway_channel_id)
            if not giveaway_channel:
                if mod_channel:
                    await mod_channel.send(
                        embed=error_embed(f"Giveaway channel (ID: `{giveaway_channel_id}`) not found!")
                    )
                return

            # Re-validate winner is still in the guild (they may have left)
            fresh_winner = guild.get_member(winner.id)
            if not fresh_winner:
                try:
                    fresh_winner = await guild.fetch_member(winner.id)
                except discord.NotFound:
                    if mod_channel:
                        await mod_channel.send(
                            embed=error_embed(
                                f"Winner {winner.mention} is no longer in the server. "
                                f"Use {REACT_EDIT} to re-roll a new winner."
                            )
                        )
                    # Put data back so mods can re-roll
                    self._pending_giveaways[payload.message_id] = data
                    return
            winner = fresh_winner

            announce_embed = discord.Embed(
                title="🎉 Giveaway Winner Announced!",
                description=(
                    f"Congratulations {winner.mention}! 🎊\n\n"
                    f"You've been selected as the winner of:\n"
                    f"**🏆 {reward}**"
                ),
                color=discord.Color.from_str("#FFD700"),
            )
            announce_embed.add_field(name="Eligible Pool", value=f"**{len(pool)}** members from **{role.name}**", inline=True)
            announce_embed.add_field(name="Approved by",   value=mod_member.mention,  inline=True)
            if winner.display_avatar:
                announce_embed.set_thumbnail(url=winner.display_avatar.url)
            announce_embed.set_footer(text=f"Drawn by {initiator.display_name} • Approved by {mod_member.display_name}")

            try:
                await giveaway_channel.send(
                    content=f"🎊 {winner.mention} — you've won the giveaway!",
                    embed=announce_embed,
                )
                log.info(
                    f"Giveaway winner posted: {winner.name} wins '{reward}', "
                    f"approved by {mod_member.name}."
                )
            except discord.Forbidden:
                if mod_channel:
                    await mod_channel.send(
                        embed=error_embed(f"I can't send messages to <#{giveaway_channel_id}>.")
                    )

            if mod_msg:
                await mod_msg.delete()

        # ── ❌ Cancel giveaway ─────────────────────────────────────────────────
        elif emoji == REACT_CANCEL:
            if mod_msg:
                cancel_embed = discord.Embed(
                    title="❌ Giveaway Cancelled",
                    description=f"The **{reward}** giveaway was cancelled by {mod_member.mention}.",
                    color=discord.Color.from_str("#ED4245"),
                )
                await mod_msg.edit(embed=cancel_embed)
                try:
                    await mod_msg.clear_reactions()
                except discord.Forbidden:
                    pass
            log.info(f"Giveaway '{reward}' cancelled by {mod_member.name}.")

        # ── ✏️ Re-roll a new winner ────────────────────────────────────────────
        elif emoji == REACT_EDIT:
            # Refresh pool from live role membership to avoid stale Member refs
            live_role = guild.get_role(role.id)
            if live_role:
                pool = [m for m in live_role.members if not m.bot]
                data["pool"] = pool
            else:
                pool = data["pool"]

            # Build cumulative exclusion set so previous winners can't be re-selected
            excluded = data.setdefault("excluded", set())
            excluded.add(winner.id)
            new_pool = [m for m in pool if m.id not in excluded]
            pool_exhausted = False
            if not new_pool:
                # Everyone has been excluded — reset but keep current winner out
                excluded.clear()
                excluded.add(winner.id)
                new_pool = [m for m in pool if m.id not in excluded]
                if not new_pool:
                    new_pool = pool  # single-member pool edge case
                    pool_exhausted = True

            new_winner = random.choice(new_pool)

            # Pre-flight: bail if a concurrent ✅/❌ already resolved this
            if payload.message_id not in self._pending_giveaways:
                return

            # Notify mods when pool is exhausted (single-member or fully cycled)
            if pool_exhausted and mod_channel:
                await mod_channel.send(
                    embed=warning_embed(
                        "Pool Exhausted",
                        "All eligible members have been selected at least once. "
                        "The exclusion list has been reset — the same winner may be re-selected."
                    )
                )

            if mod_msg:
                reroll_embed = discord.Embed(
                    title="🎁 Giveaway — Awaiting Moderator Approval",
                    description=f"✏️ **Re-rolled** by {mod_member.mention}",
                    color=discord.Color.from_str("#FFD700"),
                )
                reroll_embed.add_field(name="🏆 Reward",        value=f"**{reward}**",               inline=True)
                reroll_embed.add_field(name="🎲 Eligible Pool", value=f"**{len(pool)}** members from **{role.name}**", inline=True)
                reroll_embed.add_field(name="🎉 New Winner",    value=new_winner.mention,             inline=False)
                reroll_embed.add_field(
                    name="Actions",
                    value=(
                        f"{REACT_POST}  — Post result to <#{GIVEAWAY_CHANNEL_ID}>\n"
                        f"{REACT_CANCEL}  — Cancel the giveaway\n"
                        f"{REACT_EDIT}  — Re-roll again"
                    ),
                    inline=False,
                )
                reroll_embed.set_footer(text=f"Giveaway initiated by {initiator.display_name}")
                if new_winner.display_avatar:
                    reroll_embed.set_thumbnail(url=new_winner.display_avatar.url)
                await mod_msg.edit(embed=reroll_embed)

                # Clear all reactions and re-add bot reactions to prevent stale triggers
                try:
                    await mod_msg.clear_reactions()
                except discord.Forbidden:
                    pass
                for react in [REACT_POST, REACT_CANCEL, REACT_EDIT]:
                    await mod_msg.add_reaction(react)

            # Final guarded write-back — after all awaits complete
            # A concurrent ✅/❌ could have popped the entry during the awaits above
            if payload.message_id in self._pending_giveaways:
                data["winner"] = new_winner
                self._pending_giveaways[payload.message_id] = data

            log.info(
                f"Giveaway '{reward}' re-rolled by {mod_member.name}. "
                f"New winner: {new_winner.name}."
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(Giveaway(bot))
