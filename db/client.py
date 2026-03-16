"""
db/client.py — Supabase client wrapper.

Initialises a single shared client on startup and exposes a helper
to access it from anywhere in the bot.

Usage in a cog:
    from db import get_db

    rows = await get_db().table("users").select("*").execute()
"""
from __future__ import annotations

import logging

from supabase import Client, create_client

log = logging.getLogger(__name__)

_client: Client | None = None


def init_db(url: str, key: str) -> Client:
    """Create and store the global Supabase client. Call once at startup."""
    global _client
    _client = create_client(url, key)
    log.info("Supabase client initialised.")
    return _client


def get_db() -> Client:
    """Return the shared Supabase client. Raises if init_db() was not called."""
    if _client is None:
        raise RuntimeError("Database not initialised. Call init_db() first.")
    return _client
