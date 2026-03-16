"""
bot.py — The Bot class.
Responsible for: startup, cog loading, global error handling, and core events.
Add new lifecycle logic (e.g. DB init) here, not in main.py.
"""
from __future__ import annotations

import logging
import os

import discord
from discord.ext import commands

from config import Config
from db import init_db, get_db
from utils.embeds import error_embed

log = logging.getLogger("bot")


class LibraryBot(commands.Bot):

    def __init__(self, config: Config):
        self.config = config

        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        intents.guilds = True

        super().__init__(
            command_prefix=config.prefix,
            intents=intents,
            help_command=None,      # defined in cogs/general.py instead
            case_insensitive=True,
        )

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    async def setup_hook(self):
        """Runs before the bot connects. Initialise DB, load cogs, optionally sync slash commands."""
        # --- Database ---
        if self.config.supabase_url and self.config.supabase_key:
            init_db(self.config.supabase_url, self.config.supabase_key)
        else:
            log.warning("Supabase credentials missing — running without database.")

        # --- Cogs ---
        await self._load_cogs()

        # --- Slash commands ---
        if self.config.sync_commands:
            await self.tree.sync()
            log.info("Slash commands synced.")
        else:
            log.info("Slash command sync skipped (--no-sync).")

    async def _load_cogs(self):
        """Auto-loads every *.py file inside the cogs/ directory."""
        cog_dir = os.path.join(os.path.dirname(__file__), self.config.cog_dir)
        if not os.path.isdir(cog_dir):
            log.warning("No '%s/' directory found — skipping cog loading.", self.config.cog_dir)
            return

        for filename in sorted(os.listdir(cog_dir)):
            if filename.endswith(".py") and not filename.startswith("_"):
                ext = f"{self.config.cog_dir}.{filename[:-3]}"
                try:
                    await self.load_extension(ext)
                    log.info("Loaded cog: %s", ext)
                except Exception as exc:
                    log.error("Failed to load cog %s: %s", ext, exc, exc_info=True)

    # ── Events ─────────────────────────────────────────────────────────────────

    async def on_ready(self):
        log.info("Logged in as %s (ID: %s)", self.user, self.user.id)
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name=f"{self.config.prefix}help  |  The Library",
            )
        )
        
        # --- Seed default ranks for new servers ---
        try:
            db = get_db()
            for guild in self.guilds:
                res = db.table("rank_tiers").select("level_min").eq("guild_id", guild.id).limit(1).execute()
                if not res.data:
                    log.info(f"No ranks found for guild {guild.name} ({guild.id}). Seeding defaults...")
                    ranks = [
                        {"guild_id": guild.id, "level_min": 1, "level_max": 10, "label": "Wanderer", "emoji": "🚪"},
                        {"guild_id": guild.id, "level_min": 11, "level_max": 20, "label": "Seeker", "emoji": "🕯️"},
                        {"guild_id": guild.id, "level_min": 21, "level_max": 30, "label": "Initiate", "emoji": "📜"},
                        {"guild_id": guild.id, "level_min": 31, "level_max": 40, "label": "Apprentice", "emoji": "🔮"},
                        {"guild_id": guild.id, "level_min": 41, "level_max": 50, "label": "Alchemist of Words", "emoji": "⚗️"},
                        {"guild_id": guild.id, "level_min": 51, "level_max": 60, "label": "Runic Reader", "emoji": "🌿"},
                        {"guild_id": guild.id, "level_min": 61, "level_max": 70, "label": "Tome Guardian", "emoji": "⚔️"},
                        {"guild_id": guild.id, "level_min": 71, "level_max": 80, "label": "Mystic Scribe", "emoji": "🌙"},
                        {"guild_id": guild.id, "level_min": 81, "level_max": 90, "label": "Arcane Scholar", "emoji": "🔱"},
                        {"guild_id": guild.id, "level_min": 91, "level_max": 999, "label": "Oracle of the Library", "emoji": "👁️"},
                    ]
                    for rank in ranks:
                        db.table("rank_tiers").upsert(rank).execute()
        except RuntimeError:
            pass # DB not initialized
        except Exception as e:
            log.error(f"Failed to seed ranks on ready: {e}")

    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError):
        """Global error handler — cogs can still define their own local handlers."""
        if isinstance(error, commands.CommandNotFound):
            return
        if isinstance(error, commands.MissingPermissions):
            await ctx.reply(embed=error_embed("You don't have permission to use this command."))
        elif isinstance(error, commands.BotMissingPermissions):
            await ctx.reply(embed=error_embed("I'm missing the permissions needed to do that."))
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.reply(embed=error_embed(f"Missing argument: `{error.param.name}`"))
        elif isinstance(error, commands.BadArgument):
            await ctx.reply(embed=error_embed("Invalid argument provided."))
        elif isinstance(error, commands.CommandOnCooldown):
            await ctx.reply(embed=error_embed(f"Slow down! Try again in **{error.retry_after:.1f}s**."))
        else:
            log.error("Unhandled error in %s: %s", ctx.command, error, exc_info=True)
            await ctx.reply(embed=error_embed("Something went wrong. Please try again."))
