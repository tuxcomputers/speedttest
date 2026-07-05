-- Retention settings for local SQLite pruning, managed centrally like the
-- other settings. prune_remote_days applies to rows already synced to this
-- server; prune_local_days is the ceiling for everything else (and the only
-- rule on hosts with no remote configured).
INSERT INTO setting (setting, value) VALUES
    ('prune_local_days', '365'),
    ('prune_remote_days', '30')
ON CONFLICT (setting) DO NOTHING;
