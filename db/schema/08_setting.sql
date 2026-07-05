CREATE TABLE IF NOT EXISTS setting (
    setting  TEXT PRIMARY KEY,
    value    TEXT
);

INSERT INTO setting (setting, value) VALUES
    ('ping_host_1', '8.8.8.8'),
    ('ping_host_2', '1.1.1.1'),
    ('ping_host_3', '9.9.9.9'),
    ('ping_host_4', '208.67.222.222'),
    ('speedtest_interval_min', '5'),
    ('prune_local_days', '365'),
    ('prune_remote_days', '30')
ON CONFLICT (setting) DO NOTHING;
