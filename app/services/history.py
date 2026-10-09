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
        self.database_error = None
        if self.postgres:
            import psycopg
            self._psycopg = psycopg
            try:
                self._init_postgres()
            except Exception as exc:
                # A stale/unavailable hosted database must not prevent the
                # web application from starting. Fall back to local SQLite.
                self.database_error = f"PostgreSQL unavailable: {exc}"
                self.postgres = False
                self.url = "sqlite:///./factcheck.db"
                self.path = Path("./factcheck.db")
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self._init_sqlite()
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
                created_at TEXT NOT NULL, owner_key TEXT, share_token TEXT UNIQUE)""")
            c.execute("ALTER TABLE history ADD COLUMN IF NOT EXISTS owner_key TEXT")
            c.execute("ALTER TABLE history ADD COLUMN IF NOT EXISTS share_token TEXT")
            c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_history_share_token ON history(share_token) WHERE share_token IS NOT NULL")
            c.execute("CREATE INDEX IF NOT EXISTS idx_history_owner_created ON history(owner_key, created_at DESC)")

    def _init_sqlite(self):
        with self._conn() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, title TEXT,
                verdict TEXT NOT NULL, confidence INTEGER, payload TEXT NOT NULL,
                created_at TEXT NOT NULL, owner_key TEXT, share_token TEXT UNIQUE)""")
            columns = {row[1] for row in c.execute("PRAGMA table_info(history)").fetchall()}
            if "owner_key" not in columns:
                c.execute("ALTER TABLE history ADD COLUMN owner_key TEXT")
            if "share_token" not in columns:
                c.execute("ALTER TABLE history ADD COLUMN share_token TEXT")
            c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_history_share_token ON history(share_token) WHERE share_token IS NOT NULL")
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

    def create_share(self, id, owner_key):
        """Create/reuse an unguessable public token, only for the owning browser session."""
        token = secrets.token_urlsafe(32)
        if self.postgres:
            with self._psycopg.connect(self.url) as c:
                row = c.execute(
                    "SELECT share_token FROM history WHERE id=%s AND owner_key=%s",
                    (id, owner_key),
                ).fetchone()
                if not row:
                    return None
                if row[0]:
                    return row[0]
                c.execute(
                    "UPDATE history SET share_token=%s WHERE id=%s AND owner_key=%s AND share_token IS NULL",
                    (token, id, owner_key),
                )
                row = c.execute(
                    "SELECT share_token FROM history WHERE id=%s AND owner_key=%s",
                    (id, owner_key),
                ).fetchone()
                return row[0] if row else None
        with self._conn() as c:
            row = c.execute(
                "SELECT share_token FROM history WHERE id=? AND owner_key=?", (id, owner_key)
            ).fetchone()
            if not row:
                return None
            if row[0]:
                return row[0]
            c.execute(
                "UPDATE history SET share_token=? WHERE id=? AND owner_key=? AND share_token IS NULL",
                (token, id, owner_key),
            )
            row = c.execute(
                "SELECT share_token FROM history WHERE id=? AND owner_key=?", (id, owner_key)
            ).fetchone()
            return row[0] if row else None

    def get_shared(self, token):
        if not token or len(token) > 100:
            return None
        if self.postgres:
            with self._psycopg.connect(self.url) as c:
                row = c.execute("SELECT payload FROM history WHERE share_token=%s", (token,)).fetchone()
        else:
            with self._conn() as c:
                row = c.execute("SELECT payload FROM history WHERE share_token=?", (token,)).fetchone()
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
