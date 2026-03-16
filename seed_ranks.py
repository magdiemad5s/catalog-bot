import os
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

url = os.getenv("SUPABASE_URL")
key = os.getenv("SUPABASE_KEY")
guild_id = 1482705779697123378 # Taken from the debug logs you provided

supabase: Client = create_client(url, key)

ranks = [
    {"guild_id": guild_id, "level_min": 1, "level_max": 10, "label": "Wanderer", "emoji": "🚪"},
    {"guild_id": guild_id, "level_min": 11, "level_max": 20, "label": "Seeker", "emoji": "🕯️"},
    {"guild_id": guild_id, "level_min": 21, "level_max": 30, "label": "Initiate", "emoji": "📜"},
    {"guild_id": guild_id, "level_min": 31, "level_max": 40, "label": "Apprentice", "emoji": "🔮"},
    {"guild_id": guild_id, "level_min": 41, "level_max": 50, "label": "Alchemist of Words", "emoji": "⚗️"},
    {"guild_id": guild_id, "level_min": 51, "level_max": 60, "label": "Runic Reader", "emoji": "🌿"},
    {"guild_id": guild_id, "level_min": 61, "level_max": 70, "label": "Tome Guardian", "emoji": "⚔️"},
    {"guild_id": guild_id, "level_min": 71, "level_max": 80, "label": "Mystic Scribe", "emoji": "🌙"},
    {"guild_id": guild_id, "level_min": 81, "level_max": 90, "label": "Arcane Scholar", "emoji": "🔱"},
    {"guild_id": guild_id, "level_min": 91, "level_max": 999, "label": "Oracle of the Library", "emoji": "👁️"},
]

for rank in ranks:
    print(supabase.table("rank_tiers").upsert(rank).execute())
print("Done seeding ranks!")
