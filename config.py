"""
config.py — Centralised configuration.
Add new settings here; access them anywhere via the Config object.
"""
from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass, field

from dotenv import load_dotenv

log = logging.getLogger(__name__)


@dataclass
class Config:
    # ── Core ──────────────────────────────────────────────────────────────────
    token: str
    prefix: str = "!"
    gemini_api_key: str = ""

    # ── Supabase ──────────────────────────────────────────────────────────────
    supabase_url: str = ""
    supabase_key: str = ""

    # ── Web Panel ─────────────────────────────────────────────────────────────
    web_password: str = "admin123"

    # ── Moderation ────────────────────────────────────────────────────────────
    # Role ID required to run any moderation command.
    mod_role_id: int = 0

    # ── Runtime flags (set by CLI args, not .env) ─────────────────────────────
    sync_commands: bool = True

    # ── Derived / internal ────────────────────────────────────────────────────
    cog_dir: str = field(default="cogs", init=False)

    # ── Factory ───────────────────────────────────────────────────────────────
    @classmethod
    def load(cls) -> "Config":
        """Load config from the .env file, then validate required values."""
        load_dotenv()

        token = os.getenv("DISCORD_TOKEN", "").strip()
        if not token:
            log.critical("DISCORD_TOKEN is not set. Add it to your .env file.")
            sys.exit(1)

        supabase_url = os.getenv("SUPABASE_URL", "").strip()
        supabase_key = os.getenv("SUPABASE_KEY", "").strip()
        if not supabase_url or not supabase_key:
            log.warning("SUPABASE_URL or SUPABASE_KEY not set — database features will be unavailable.")

        raw_mod_role = os.getenv("MOD_ROLE_ID", "0").strip()
        try:
            mod_role_id = int(raw_mod_role)
        except ValueError:
            log.warning("MOD_ROLE_ID in .env is not a valid integer — defaulting to 0 (disabled).")
            mod_role_id = 0
            
        gemini_api_key = os.getenv("GEMINI_API_KEY", "").strip()
        if not gemini_api_key:
            log.warning("GEMINI_API_KEY is not set — AI chat features will remain offline.")

        web_password = os.getenv("WEB_PASSWORD", "admin123").strip()

        return cls(
            token=token,
            prefix=os.getenv("PREFIX", "!"),
            supabase_url=supabase_url,
            supabase_key=supabase_key,
            mod_role_id=mod_role_id,
            gemini_api_key=gemini_api_key,
            web_password=web_password,
        )

