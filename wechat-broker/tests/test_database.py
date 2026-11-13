import unittest
from unittest.mock import patch

from app.config import settings
from app.database import _engine_options


class DatabaseConfigurationTests(unittest.TestCase):
    def test_sqlite_does_not_receive_mysql_pool_options(self):
        with patch.object(settings, "database_url", "sqlite+aiosqlite:///./test.db"):
            self.assertEqual(_engine_options(), {})

    def test_mysql_receives_bounded_pool_options(self):
        with patch.object(
            settings,
            "database_url",
            "mysql+aiomysql://user:secret@127.0.0.1/test",
        ):
            options = _engine_options()

        self.assertEqual(options["pool_timeout"], 10)
        self.assertEqual(options["connect_args"], {"connect_timeout": 5})


if __name__ == "__main__":
    unittest.main()
