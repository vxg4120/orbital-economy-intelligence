import common.db


def test_default_database_url_parses():
    assert common.db.DEFAULT_DATABASE_URL == "postgresql://oei:oei@localhost:5433/oei"
    assert common.db._database_url().startswith("postgresql://")
