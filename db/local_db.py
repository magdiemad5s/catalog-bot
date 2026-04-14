import sqlite3
import os
import logging
import time

log = logging.getLogger("local_db")
DB_PATH = "data/local_storage.db"

def init_local_db():
    """Initialises the local SQLite database for persistent bot configuration."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Create bot_config table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bot_config (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    
    # Create mafia_sessions_persistence table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS mafia_sessions_persistence (
            session_id TEXT PRIMARY KEY,
            guild_id INTEGER,
            state_json TEXT,
            rejoin_tokens_json TEXT,
            created_at INTEGER,
            expires_at INTEGER
        )
    """)
    
    # --- Persistence Indexes ---
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_mafia_expires ON mafia_sessions_persistence(expires_at)")
    
    # Seed default base_url if not exists
    cursor.execute("INSERT OR IGNORE INTO bot_config (key, value) VALUES (?, ?)", 
                   ("base_url", "http://catalogcore.com"))
    
    # Seed advanced Mafia settings
    mafia_defaults = [
        ("max_players", "20"),
        ("night_duration", "45"),
        ("day_duration", "60"),
        ("jester_enabled", "true"),
        ("framer_enabled", "true"),
        ("allowed_guilds", "") # Comma-separated
    ]
    for k, v in mafia_defaults:
        cursor.execute("INSERT OR IGNORE INTO bot_config (key, value) VALUES (?, ?)", (k, v))
        
    conn.commit()
    conn.close()
    log.info(f"Local SQLite database initialised at {DB_PATH}")

def get_config(key: str, default: str = None) -> str:
    """Retrieves a value from the local bot_config table."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM bot_config WHERE key = ?", (key,))
        row = cursor.fetchone()
        conn.close()
        return row[0] if row else default
    except Exception as e:
        log.error(f"Failed to get local config {key}: {e}")
        return default

def set_config(key: str, value: str):
    """Sets a value in the local bot_config table."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO bot_config (key, value) VALUES (?, ?)", (key, value))
        conn.commit()
        conn.close()
    except Exception as e:
        log.error(f"Failed to set local config {key}: {e}")

def expire_old_sessions():
    """Purge rows where expires_at < now()."""
    try:
        now = int(time.time())
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM mafia_sessions_persistence WHERE expires_at > 0 AND expires_at < ?", (now,))
        conn.commit()
        conn.close()
    except Exception as e:
        log.error(f"Failed to expire old mafia sessions: {e}")
