from __future__ import annotations

import asyncio
import re
import ssl

import pymysql
from pymysql import MySQLError
from sqlalchemy.engine import make_url

from app.config import settings


_TABLE_NAME = re.compile(r"^[A-Za-z0-9_]+$")


class TicketStoreError(RuntimeError):
    pass


class TicketStore:
    def __init__(self) -> None:
        if not settings.ticket_database_url:
            raise TicketStoreError("WECHAT_BROKER_TICKET_DATABASE_URL is not configured")
        url = make_url(settings.ticket_database_url)
        if url.drivername not in {"mysql", "mysql+aiomysql"}:
            raise TicketStoreError("Ticket database must use MySQL")
        if not url.host or not url.username or not url.database:
            raise TicketStoreError("Ticket database URL is incomplete")
        table = settings.ticket_database_table.strip()
        if not _TABLE_NAME.fullmatch(table):
            raise TicketStoreError("Ticket database table name is invalid")
        self.host = url.host
        self.port = url.port or 3306
        self.user = url.username
        self.password = url.password or ""
        self.database = url.database
        self.table = table

    async def latest_ticket(self) -> str:
        try:
            row = await asyncio.wait_for(asyncio.to_thread(self._read_ticket), timeout=8)
        except (asyncio.TimeoutError, MySQLError, OSError) as exc:
            raise TicketStoreError("Unable to read the component ticket database") from exc
        if not row or not isinstance(row[0], str) or not row[0].strip():
            raise TicketStoreError("The component ticket database has no current ticket")
        return row[0].strip()

    def _read_ticket(self):
        connection = None
        try:
            connection = pymysql.connect(
                host=self.host,
                port=self.port,
                user=self.user,
                password=self.password,
                database=self.database,
                connect_timeout=5,
                read_timeout=5,
                write_timeout=5,
                autocommit=True,
                ssl=self._ssl_context(),
            )
            with connection.cursor() as cursor:
                cursor.execute(
                    f"SELECT ticket_value FROM `{self.table}` WHERE name=%s LIMIT 1",
                    ("ticket",),
                )
                return cursor.fetchone()
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _ssl_context() -> ssl.SSLContext | None:
        if not settings.ticket_database_ssl:
            return None
        context = ssl.create_default_context()
        if not settings.ticket_database_ssl_verify:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        return context
