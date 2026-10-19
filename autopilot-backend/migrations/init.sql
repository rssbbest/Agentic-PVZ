-- ============================================================
-- Zombie Apocalypse: Agentic Event  –  Database Schema
-- PostgreSQL 14+
-- ============================================================

-- Extension for UUID primary keys (optional but clean)
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ----------------------------------------------------------
-- teams
-- ----------------------------------------------------------
CREATE TABLE IF NOT EXISTS teams (
    id         SERIAL PRIMARY KEY,
    name       VARCHAR(120) NOT NULL UNIQUE,
    created_at TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- ----------------------------------------------------------
-- submissions  –  one row per uploaded agent.py
-- Each submission is run against EVERY enemy config (one run each);
-- total_score is the average of the run scores once all runs are done.
-- ----------------------------------------------------------
DO $$ BEGIN
    CREATE TYPE submission_status AS ENUM ('pending', 'completed', 'failed');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE TABLE IF NOT EXISTS submissions (
    id          SERIAL PRIMARY KEY,
    team_id     INT               NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    file_path   TEXT              NOT NULL,          -- path/name stored on disk
    status      submission_status NOT NULL DEFAULT 'pending',
    total_score NUMERIC(10, 4),
    created_at  TIMESTAMPTZ       NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_submissions_team  ON submissions(team_id);
CREATE INDEX IF NOT EXISTS idx_submissions_score ON submissions(total_score DESC NULLS LAST);

-- ----------------------------------------------------------
-- enemy_configs  –  the test suite; owned by the backend, seeded on startup
-- ----------------------------------------------------------
CREATE TABLE IF NOT EXISTS enemy_configs (
    id      SERIAL PRIMARY KEY,
    name    VARCHAR(120) NOT NULL,
    enemies JSONB        NOT NULL,
    -- example: [{"enemyId": 0, "count": 5}, {"enemyId": 1, "count": 5}]
    budget  INT                       -- optional cost budget for this test case
);
ALTER TABLE enemy_configs ADD COLUMN IF NOT EXISTS budget INT;

-- ----------------------------------------------------------
-- runs  –  one row per (submission, enemy config)
-- ----------------------------------------------------------
DO $$ BEGIN
    CREATE TYPE run_status AS ENUM (
        'pending',
        'invalid',
        'simulating',
        'completed',
        'failed'
    );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE TABLE IF NOT EXISTS runs (
    id                 SERIAL PRIMARY KEY,
    submission_id      INT         NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
    enemy_config_id    INT         NOT NULL REFERENCES enemy_configs(id),

    status             run_status  NOT NULL DEFAULT 'pending',

    -- SDK response: {valid: bool, violations: [...]}
    sdk_status         JSONB,

    -- Agent's defence placement output
    defence_placement  JSONB,

    -- Full evaluator response payload
    evaluator_output   JSONB,

    -- Extracted final score
    score              NUMERIC(10, 4),

    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (submission_id, enemy_config_id)
);

-- Leaderboard index (score DESC, NULLs last by default)
CREATE INDEX IF NOT EXISTS idx_runs_score      ON runs(score DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS idx_runs_submission ON runs(submission_id);
CREATE INDEX IF NOT EXISTS idx_runs_status     ON runs(status);

-- ----------------------------------------------------------
-- team_data  –  per-team defence placement + enemy config
--               used by the Unity client via POST /api/team
-- ----------------------------------------------------------
CREATE TABLE IF NOT EXISTS team_data (
    id                     SERIAL PRIMARY KEY,
    team_id                INT   NOT NULL UNIQUE REFERENCES teams(id) ON DELETE CASCADE,
    -- [{\"defenceId\": 0, \"defenceGridPosition\": {\"x\": 0, \"y\": 0}}, ...]
    defence_placement_data JSONB NOT NULL DEFAULT '[]',
    -- [{\"enemyId\": 0, \"count\": 5}, ...]
    enemies                JSONB NOT NULL DEFAULT '[]',
    spawn_settings         JSONB,
    game_settings          JSONB,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_team_data_team ON team_data(team_id);

-- ----------------------------------------------------------
-- results_log  –  verbatim result payloads from POST /api/results
-- ----------------------------------------------------------
CREATE TABLE IF NOT EXISTS results_log (
    id          SERIAL PRIMARY KEY,
    data        JSONB       NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ----------------------------------------------------------
-- Default seed for local testing with Unity simulator
-- ----------------------------------------------------------
INSERT INTO teams (name) VALUES ('TEAM1') ON CONFLICT (name) DO NOTHING;
INSERT INTO teams (name) VALUES ('team1') ON CONFLICT (name) DO NOTHING;

INSERT INTO team_data (team_id, defence_placement_data, enemies, spawn_settings, game_settings)
SELECT 
    t.id,
    '[]'::jsonb,
    '[{"enemyId": 0, "count": 6, "allowedRows": [1, 2, 3]}, {"enemyId": 1, "count": 4, "allowedRows": [0, 1, 2, 3, 4]}]'::jsonb,
    '{"interArrivalTime": 6.0, "timeDecreaseRate": 0.25, "minInterArrivalTime": 0.0}'::jsonb,
    '{"reinforcementsEnabled": true, "reinforcementIntervalSeconds": 10.0, "reinforcementHoldSeconds": 1.0, "sendScreenshotToo": false}'::jsonb
FROM teams t
WHERE t.name IN ('TEAM1', 'team1')
ON CONFLICT (team_id) DO NOTHING;

-- Synchronize sequences so future inserts don't collide with seeded IDs
SELECT setval(pg_get_serial_sequence('teams', 'id'), COALESCE(max(id), 1)) FROM teams;
SELECT setval(pg_get_serial_sequence('team_data', 'id'), COALESCE(max(id), 1)) FROM team_data;
