import local_db


def table_names(conn):
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {r['name'] for r in rows}


def test_init_creates_schema_and_stamps_version(sqlite_env):
    local_db.init_db()
    conn = local_db.get_connection()
    assert {'host', 'test', 'ping', 'download', 'upload', 'server', 'outage', 'network_status', 'setting'} <= table_names(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == local_db.SCHEMA_VERSION
    conn.close()


def test_init_seeds_default_settings(sqlite_env):
    local_db.init_db()
    conn = local_db.get_connection()
    settings = dict(conn.execute("SELECT setting, value FROM setting").fetchall())
    conn.close()
    assert settings['ping_host_1'] == '8.8.8.8'
    assert settings['speedtest_interval_min'] == '5'


def test_init_is_idempotent(sqlite_env):
    local_db.init_db()
    local_db.init_db()


def test_init_scrubs_legacy_credentials(sqlite_env, monkeypatch):
    local_db.init_db()
    conn = local_db.get_connection()
    # Simulate a database written by the old code, which mirrored DB
    # credentials into the setting table.
    conn.execute("INSERT INTO setting (setting, value) VALUES ('db_password', 'hunter2')")
    conn.execute("PRAGMA user_version = 0")
    conn.commit()
    conn.close()

    local_db.init_db()

    conn = local_db.get_connection()
    row = conn.execute("SELECT value FROM setting WHERE setting = 'db_password'").fetchone()
    conn.close()
    assert row is None


def test_credentials_never_written_to_sqlite(sqlite_env, monkeypatch):
    monkeypatch.setenv('DB_PASSWORD', 'super-secret')
    local_db.init_db()
    conn = local_db.get_connection()
    rows = conn.execute("SELECT setting FROM setting WHERE setting LIKE 'db_%'").fetchall()
    conn.close()
    assert rows == []


def test_get_or_create_host_is_single_row(sqlite_env, monkeypatch):
    local_db.init_db()
    first_id = local_db.get_or_create_host()

    # A hostname change must update the existing row, not create a second
    # host — otherwise existing data is orphaned and sync misattributes it.
    monkeypatch.setenv('HOST_HOSTNAME', 'renamed-host')
    second_id = local_db.get_or_create_host()

    assert first_id == second_id
    conn = local_db.get_connection()
    rows = conn.execute("SELECT hostname FROM host").fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0]['hostname'] == 'renamed-host'


def test_host_hash_comes_only_from_env(sqlite_env, monkeypatch):
    monkeypatch.delenv('HOST_HASH', raising=False)
    # No fallback to the container's MAC — that identity is unstable.
    assert local_db.get_host_hash() is None
