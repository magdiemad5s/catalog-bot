# S.E.R.A / Catalog Bot - Project Information

## 🚀 Overview
Catalog Bot (codenamed S.E.R.A) is a high-performance modular Discord bot designed for community engagement, automated moderation, and AI-powered interaction. It features a fully integrated web administration panel for live configuration.

## 🛠️ Technology Stack
- **Core Framework**: [discord.py](https://discordpy.readthedocs.io/)
- **Web Layer**: [aiohttp](https://docs.aiohttp.org/) with [Jinja2](https://jinja.palletsprojects.com/) templating
- **Database**: [Supabase](https://supabase.com/) (PostgreSQL)
- **AI Integration**: [Google Gemini 1.5/2.0](https://ai.google.dev/) (Flash & Flash Lite models)
- **Search Engine**: DuckDuckGo (via `ddgs`)
- **Security**: `bcrypt` for dashboard authentication

## 📁 Repository Structure
- `bot.py`: Main Bot class and lifecycle management.
- `web.py`: Aiohttp server initialization and route registration.
- `config.py`: Dataclass-based configuration loader.
- `schema.sql`: Database schema definitions for Supabase.
- `cogs/`:
    - `ai.py`: Gemini-powered conversational system with function calling & web search.
    - `anti_raid.py`: Dynamic join-rate limiting and automated quarantine.
    - `levels.py`: XP tracking, rank tiers, and badge role rewards.
    - `giveaway.py`: Moderator-controlled role giveaways with race-condition protection.
    - `admin.py`: Deployment and developer utilities (e.g., `!update`, `!seedadmin`).
- `routes/`: Modular web route handlers for the admin panel.
- `templates/`: HTML templates for the dashboard interface.

## ⚙️ Requirements & Installation

### Python Version
Python **3.10+** (Required for asynchronous typing and aiohttp compatibility).

### Dependencies
Install via `pip install -r requirements.txt`:
- `discord.py`
- `supabase`
- `google-genai`
- `aiohttp`, `aiohttp-jinja2`, `aiohttp-session`
- `bcrypt`, `cryptography`
- `ddgs` (DuckDuckGo Search)

### Environment Variables (.env)
```env
DISCORD_TOKEN=your_token_here
SUPABASE_URL=your_supabase_url
SUPABASE_KEY=your_supabase_anon_key
GEMINI_API_KEY=primary_key
GEMINI_API_KEY_2=fallback_1
GEMINI_API_KEY_3=fallback_2
WEB_SECRET_KEY=32_char_random_string
MOD_ROLE_ID=id_of_moderator_role
```

## 🔐 Web Admin Access
Initial access requires seeding an admin account via Discord:
1. Run `!seedadmin username password` (or `/seedadmin`) in a channel where the bot has access.
2. The bot will hash the password with `bcrypt` and store it in the `admin_users` table.
3. Access the dashboard at `http://your-ip:8080/login`.

## ⚠️ Security Notes
- All database operations are wrapped in `asyncio.to_thread` to prevent blocking the Discord event loop.
- The web panel uses `EncryptedCookieStorage` (Fernet) for session management.
- User-generated content in the dashboard is protected by Jinja2 auto-escaping.
