import unittest
from unittest.mock import patch

from app.config import settings
from app.ticket_store import TicketStore, TicketStoreError


class Cursor:
    def __init__(self, row):
        self.row = row
        self.query = None
        self.params = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, query, params):
        self.query = query
        self.params = params

    def fetchone(self):
        return self.row


class Connection:
    def __init__(self, row):
        self.cursor_value = Cursor(row)
        self.closed = False

    def cursor(self):
        return self.cursor_value

    def close(self):
        self.closed = True


class TicketStoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_reads_only_named_ticket_row(self):
        connection = Connection(("ticket-value",))
        with (
            patch.object(
                settings,
                "ticket_database_url",
                "mysql+aiomysql://reader:secret@mysql.example/test",
            ),
            patch.object(settings, "ticket_database_table", "fa_ticket"),
            patch.object(settings, "ticket_database_ssl", False),
            patch("app.ticket_store.pymysql.connect", return_value=connection),
        ):
            ticket = await TicketStore().latest_ticket()

        self.assertEqual(ticket, "ticket-value")
        self.assertEqual(connection.cursor_value.params, ("ticket",))
        self.assertIn("SELECT ticket_value", connection.cursor_value.query)
        self.assertTrue(connection.closed)

    def test_rejects_unsafe_table_name(self):
        with (
            patch.object(
                settings,
                "ticket_database_url",
                "mysql+aiomysql://reader:secret@mysql.example/test",
            ),
            patch.object(settings, "ticket_database_table", "ticket; DROP TABLE users"),
        ):
            with self.assertRaises(TicketStoreError):
                TicketStore()


if __name__ == "__main__":
    unittest.main()
