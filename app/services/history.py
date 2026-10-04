import json
import secrets
import sqlite3
from pathlib import Path
from ..config import get_settings


class HistoryStore:
    def __init__(self):
        self.settings = get_settings()
        self.url = self.settings.database_url
        self.postgres = self.url.startswith(("postgresql://", "postgres://"))
        if self.postgres:
            import psycopg
            self._psycopg = psycopg
            self._init_postgres()
        elif self.url.startswith("sqlite:///"):
            self.path = Path(self.url.replace("sqlite:///", "", 1))
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._init_sqlite()
        else:
            raise RuntimeError("DATABASE_URL must use SQLite or PostgreSQL")

    def _init_postgres(self):
        with self._psycopg.connect(self.url) as c:
            c.execute("""CREATE TABLE IF NOT EXISTS history (
                id BIGSERIAL PRIMARY KEY, kind TEXT NOT NULL, title TEXT,
                verdict TEXT NOT NULL, confidence INTEGER, payload TEXT NOT NULL,
                created_at TEXT NOT NULL, owner_key TEXT)""")
            c.execute("ALTER TABLE history ADD COLUMN IF NOT EXISTS owner_key TEXT")
            c.execute("CREATE INDEX IF NOT EXISTS idx_history_owner_created ON history(owner_key, created_at DESC)")

    def _init_sqlite(self):
        with self._conn() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, title TEXT,
                verdict TEXT NOT NULL, confidence INTEGER, payload TEXT NOT NULL,
                created_at TEXT NOT NULL, owner_key TEXT)""")
            columns = {row[1] for row in c.execute("PRAGMA table_info(history)").fetchall()}
            if "owner_key" not in columns:
                c.execute("ALTER TABLE history ADD COLUMN owner_key TEXT")
            c.execute("CREATE INDEX IF NOT EXISTS idx_history_owner_created ON history(owner_key, created_at DESC)")

    def _conn(self):
        return sqlite3.connect(self.path, timeout=10)

    def add(self, kind, title, verdict, confidence, payload, created_at, owner_key):
        data = (kind, title, verdict, confidence, json.dumps(payload, ensure_ascii=False), created_at, owner_key)
        if self.postgres:
            with self._psycopg.connect(self.url) as c:
                row = c.execute("INSERT INTO history(kind,title,verdict,confidence,payload,created_at,owner_key) VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING id", data).fetchone()
                return row[0]
        with self._conn() as c:
            return c.execute("INSERT INTO history(kind,title,verdict,confidence,payload,created_at,owner_key) VALUES(?,?,?,?,?,?,?)", data).lastrowid

    def list(self, owner_key, q=""):
        limit = self.settings.max_history_items
        if self.postgres:
            with self._psycopg.connect(self.url) as c:
                rows = c.execute("SELECT id,kind,title,verdict,confidence,created_at FROM history WHERE owner_key=%s AND title ILIKE %s ORDER BY id DESC LIMIT %s", (owner_key, f"%{q}%", limit)).fetchall()
        else:
            with self._conn() as c:
                rows = c.execute("SELECT id,kind,title,verdict,confidence,created_at FROM history WHERE owner_key=? AND title LIKE ? ORDER BY id DESC LIMIT ?", (owner_key, f"%{q}%", limit)).fetchall()
        return [dict(zip(["id", "kind", "title", "verdict", "confidence", "created_at"], r)) for r in rows]

    def get(self, id, owner_key=None):
        if self.postgres:
            with self._psycopg.connect(self.url) as c:
                if owner_key is None:
                    row = c.execute("SELECT payload FROM history WHERE id=%s", (id,)).fetchone()
                else:
                    row = c.execute("SELECT payload FROM history WHERE id=%s AND owner_key=%s", (id, owner_key)).fetchone()
        else:
            with self._conn() as c:
                if owner_key is None:
                    row = c.execute("SELECT payload FROM history WHERE id=?", (id,)).fetchone()
                else:
                    row = c.execute("SELECT payload FROM history WHERE id=? AND owner_key=?", (id, owner_key)).fetchone()
        return json.loads(row[0]) if row else None

    def delete(self, id, owner_key):
        if self.postgres:
            with self._psycopg.connect(self.url) as c:
                return c.execute("DELETE FROM history WHERE id=%s AND owner_key=%s", (id, owner_key)).rowcount
        with self._conn() as c:
            return c.execute("DELETE FROM history WHERE id=? AND owner_key=?", (id, owner_key)).rowcount

    def clear(self, owner_key):
        if self.postgres:
            with self._psycopg.connect(self.url) as c:
                c.execute("DELETE FROM history WHERE owner_key=%s", (owner_key,))
        else:
            with self._conn() as c:
                c.execute("DELETE FROM history WHERE owner_key=?", (owner_key,))


def new_owner_key() -> str:
    return secrets.token_urlsafe(32)
