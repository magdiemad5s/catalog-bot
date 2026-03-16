"""
utils/embeds.py — Reusable embed factories.

Every cog should use these instead of building embeds inline, so the
bot's visual style stays consistent and is easy to update from one place.

Usage:
    from utils import error_embed, success_embed, info_embed

    await ctx.reply(embed=success_embed("Done!", "Member was kicked."))
"""
from __future__ import annotations

import discord

# ── Colour palette ─────────────────────────────────────────────────────────────
COLOUR_SUCCESS = discord.Color.from_str("#57F287")   # green
COLOUR_ERROR   = discord.Color.from_str("#ED4245")   # red
COLOUR_INFO    = discord.Color.from_str("#5865F2")   # blurple
COLOUR_WARNING = discord.Color.from_str("#FEE75C")   # yellow


def success_embed(title: str, description: str = "") -> discord.Embed:
    """A green ✅ embed for confirmations."""
    return discord.Embed(
        title=f"✅ {title}",
        description=description,
        color=COLOUR_SUCCESS,
    )


def error_embed(description: str, title: str = "Error") -> discord.Embed:
    """A red ❌ embed for errors."""
    return discord.Embed(
        title=f"❌ {title}",
        description=description,
        color=COLOUR_ERROR,
    )


def info_embed(title: str, description: str = "") -> discord.Embed:
    """A blurple ℹ️ embed for general information."""
    return discord.Embed(
        title=f"ℹ️ {title}",
        description=description,
        color=COLOUR_INFO,
    )


def warning_embed(title: str, description: str = "") -> discord.Embed:
    """A yellow ⚠️ embed for warnings."""
    return discord.Embed(
        title=f"⚠️ {title}",
        description=description,
        color=COLOUR_WARNING,
    )
