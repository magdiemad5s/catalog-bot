-- schema.sql
-- Run this once in your Supabase project:
--   Dashboard → SQL Editor → paste → Run
--
-- Tables used by the bot.
-- Add new tables here as features are built.

-- ── User Profiles (Memory & Profiling) ─────────────────────────────────────────
-- Stores AI-generated dossiers and tracking flags for users.
create table if not exists user_profiles (
    user_id       bigint       primary key,
    avatar_hash   text,                            -- Used to detect PFP changes
    profile_bio   text,                            -- Used to detect bio changes
    dossier       text         not null default '',-- AI generated summary of who they are
    recent_flags  text         not null default '',-- Things for the AI to bring up (e.g. "Changed PFP recently")
    has_library_card boolean   not null default false, -- Tracks if the user has completed the onboarding interview
    last_checked  timestamptz  not null default now()
);

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
    last_streak_date text,                           -- 'YYYY-MM-DD' for daily streak tracking (separate from last_xp_at)
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

-- ── AI Search & Interaction tracking ───────────────────────────────────────────

-- Table for guilds that have auto-web enabled globally
create table if not exists auto_web_guilds (
    guild_id bigint primary key,
    enabled_at timestamptz not null default now()
);

-- Table for users who have auto-web enabled personally
create table if not exists auto_web_users (
    user_id bigint primary key,
    enabled_at timestamptz not null default now()
);

-- Add AI interaction logging table (for the leaderboard)
create table if not exists ai_interactions (
    guild_id bigint not null,
    user_id bigint not null,
    interaction_count int not null default 1,
    primary key (guild_id, user_id)
);

-- ── XP Boosts ────────────────────────────────────────────────────────────────
create table if not exists xp_role_boosts (
    guild_id    bigint  not null,
    role_id     bigint  not null,
    multiplier  float   not null default 1.1,
    label       text    not null,
    enabled     boolean not null default true,
    primary key (guild_id, role_id)
);

-- ── Anti-Raid ────────────────────────────────────────────────────────────────
create table if not exists anti_raid_config (
    guild_id                bigint  primary key,
    enabled                 boolean not null default false,
    account_age_min_days    int     not null default 7,
    join_rate_count         int     not null default 5,
    join_rate_window_seconds int    not null default 10,
    penalty_action          text    not null default 'kick', -- 'kick', 'mute', 'ban', 'quarantine'
    mute_duration_minutes   int     not null default 60,
    alert_channel_id        bigint,
    quarantine_role_id      bigint
);

-- ── Word Filter ──────────────────────────────────────────────────────────────
create table if not exists filter_profiles (
    id          bigserial   primary key,
    guild_id    bigint      not null,
    name        text        not null,
    word_list   jsonb       not null default '[]',
    enabled     boolean     not null default true,
    created_at  timestamptz not null default now()
);

create table if not exists filter_config (
    guild_id            bigint  primary key,
    enabled             boolean not null default false,
    active_profile_id   bigint  references filter_profiles(id),
    threshold           int     not null default 3,
    mod_channel_id      bigint
);

create table if not exists filter_log (
    id              bigserial    primary key,
    guild_id        bigint       not null,
    user_id         bigint       not null,
    channel_id      bigint       not null,
    matched_word    text         not null,
    message_content text         not null,
    action_taken    text         not null,
    created_at      timestamptz  not null default now()
);

-- ── Minigames ────────────────────────────────────────────────────────────────
create table if not exists minesweeper_sessions (
    session_id   uuid        primary key default gen_random_uuid(),
    guild_id     bigint      not null default 0,
    user_id      bigint      not null,
    difficulty   text        not null,
    score        int         not null default 0,
    won          boolean     not null default false,
    started_at   timestamptz not null default now(),
    completed_at timestamptz
);

create table if not exists roulette_sessions (
    session_id      uuid        primary key default gen_random_uuid(),
    guild_id        bigint      not null default 0,
    host_user_id    bigint      not null,
    players         jsonb       not null default '[]',
    xp_pool         int         not null default 0,
    winner_user_ids jsonb       not null default '[]',
    xp_won_each     int         not null default 0,
    started_at      timestamptz not null default now(),
    ended_at        timestamptz
);

-- ── Admin Users (Web Panel Auth) ──────────────────────────────────────────────
create table if not exists admin_users (
    id            bigserial    primary key,
    username      text         unique not null,
    password_hash text         not null,
    role          text         not null default 'GUILD_ADMIN', -- 'SUPER_ADMIN' or 'GUILD_ADMIN'
    guild_id      bigint,                                      -- NULL if SUPER_ADMIN
    requires_password_change boolean not null default false,
    requires_setup           boolean not null default false,
    created_at    timestamptz  not null default now()
);

-- ── Guild Global Configurations ─────────────────────────────────────────────
create table if not exists guild_configs (
    guild_id                bigint  primary key,
    is_enabled              boolean not null default true,
    announcement_channel_id bigint,
    mod_channel_id          bigint,
    card_channel_id         bigint,
    rules_channel_id        bigint,
    giveaway_channel_id     bigint,
    mod_role_id             bigint
);

-- ── MIGRATIONS FOR EXISTING DATABASES ───────────────────────────────────────
-- If you have already run the previous version of schema.sql, 
-- run the following block in Supabase SQL Editor to upgrade your tables:

/*
ALTER TABLE admin_users 
ADD COLUMN IF NOT EXISTS role text not null default 'GUILD_ADMIN',
ADD COLUMN IF NOT EXISTS guild_id bigint,
ADD COLUMN IF NOT EXISTS requires_password_change boolean not null default false,
ADD COLUMN IF NOT EXISTS requires_setup boolean not null default false;

-- Add guild_id to minigames
ALTER TABLE minesweeper_sessions 
ADD COLUMN IF NOT EXISTS guild_id bigint not null default 0;

ALTER TABLE roulette_sessions 
ADD COLUMN IF NOT EXISTS guild_id bigint not null default 0;

-- NOTE: If you have existing admins, you should manually upgrade one to 'SUPER_ADMIN'
-- UPDATE admin_users SET role = 'SUPER_ADMIN' WHERE username = 'YOUR_CURRENT_ADMIN';

-- For xp_role_boosts, we need to drop the table and recreate it because the primary key changed.
-- WARNING: This deletes existing role boosts!
DROP TABLE IF EXISTS xp_role_boosts;
CREATE TABLE xp_role_boosts (
    guild_id    bigint  not null,
    role_id     bigint  not null,
    multiplier  float   not null default 1.1,
    label       text    not null,
    enabled     boolean not null default true,
    primary key (guild_id, role_id)
);
*/
