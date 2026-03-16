-- schema.sql
-- Run this once in your Supabase project:
--   Dashboard → SQL Editor → paste → Run
--
-- Tables used by the bot.
-- Add new tables here as features are built.

-- ── Warnings ───────────────────────────────────────────────────────────────────
-- Stores member warnings issued via the !warn command.
create table if not exists warnings (
    id            bigserial primary key,
    guild_id      bigint       not null,
    user_id       bigint       not null,
    moderator_id  bigint       not null,
    reason        text         not null default 'No reason provided',
    created_at    timestamptz  not null default now()
);

-- Index for quick lookup of a specific user's warnings in a guild
create index if not exists idx_warnings_guild_user
    on warnings (guild_id, user_id);

-- ── Mod logs ───────────────────────────────────────────────────────────────────
-- General moderation action log (kick, ban, timeout, etc.)
create table if not exists mod_logs (
    id            bigserial primary key,
    guild_id      bigint       not null,
    action        text         not null,   -- 'kick' | 'ban' | 'unban' | 'timeout' | 'warn'
    target_id     bigint       not null,
    moderator_id  bigint       not null,
    reason        text         not null default 'No reason provided',
    created_at    timestamptz  not null default now()
);

create index if not exists idx_mod_logs_guild
    on mod_logs (guild_id, created_at desc);

-- ── XP / Leveling System ──────────────────────────────────────────────────────

-- xp_profiles: one row per (guild, user)
create table if not exists xp_profiles (
    id            bigserial    primary key,
    guild_id      bigint       not null,
    user_id       bigint       not null,
    xp            int          not null default 0,
    level         int          not null default 0,
    messages_sent int          not null default 0,
    voice_minutes int          not null default 0,
    streak_days   int          not null default 0,
    last_xp_at    timestamptz,
    last_seen_at  timestamptz  not null default now(),
    unique (guild_id, user_id)
);
create index if not exists idx_xp_profiles_guild_xp on xp_profiles (guild_id, xp desc);

-- Rank definitions (editable per guild)
create table if not exists rank_tiers (
    guild_id    bigint  not null,
    level_min   int     not null,   -- start of range
    level_max   int     not null,   -- end of range
    role_id     bigint,             -- Discord role ID (null = display only)
    label       text    not null,   -- display name, e.g. "Wanderer"
    emoji       text    not null default '🚪',
    primary key (guild_id, level_min)
);

-- Note: Instead of inserting a default "Lurker" here globally for a specific guild ID 
-- (which we don't know during schema execution), the bot should fall back to "Lurker" if no ranks exist, 
-- or you insert it via the bot command later:  /editrank 0 10 Lurker 👁️

-- Badge role mappings + earned records
create table if not exists badge_roles (
    guild_id   bigint  not null,
    badge_key  text    not null,
    role_id    bigint  not null,
    primary key (guild_id, badge_key)
);

create table if not exists user_badges (
    guild_id   bigint       not null,
    user_id    bigint       not null,
    badge_key  text         not null,
    earned_at  timestamptz  not null default now(),
    primary key (guild_id, user_id, badge_key)
);

-- Per-guild XP settings
create table if not exists xp_settings (
    guild_id          bigint  primary key,
    xp_min            int     not null default 15,
    xp_max            int     not null default 25,
    cooldown_seconds  int     not null default 60,
    levelup_channel   bigint          -- NULL = reply in current channel
);
