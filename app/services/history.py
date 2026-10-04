import json
import sqlite3
from pathlib import Path
from ..config import get_settings

class HistoryStore:
    def __init__(self):
        self.url=get_settings().database_url
        self.postgres=self.url.startswith(("postgresql://","postgres://"))
        if self.postgres:
            import psycopg
            self._psycopg=psycopg
            self._init_postgres()
        elif self.url.startswith("sqlite:///"):
            self.path=Path(self.url.replace("sqlite:///",""))
            self.path.parent.mkdir(parents=True,exist_ok=True)
            self._init_sqlite()
        else:
            raise RuntimeError("DATABASE_URL must use SQLite or PostgreSQL")

    def _schema(self):
        return """CREATE TABLE IF NOT EXISTS history (
            id SERIAL PRIMARY KEY,
            kind TEXT NOT NULL,
            title TEXT,
            verdict TEXT NOT NULL,
            confidence INTEGER,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL
        )"""

    def _init_postgres(self):
        with self._psycopg.connect(self.url) as c:
            c.execute(self._schema())

    def _init_sqlite(self):
        with self._conn() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                title TEXT,
                verdict TEXT NOT NULL,
                confidence INTEGER,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""")

    def _conn(self):
        return sqlite3.connect(self.path)

    def add(self,kind,title,verdict,confidence,payload,created_at):
        data=(kind,title,verdict,confidence,json.dumps(payload,ensure_ascii=False),created_at)
        if self.postgres:
            with self._psycopg.connect(self.url) as c:
                return c.execute(
                    "INSERT INTO history(kind,title,verdict,confidence,payload,created_at) VALUES(%s,%s,%s,%s,%s,%s) RETURNING id",
                    data
                ).fetchone()[0]
        with self._conn() as c:
            return c.execute(
                "INSERT INTO history(kind,title,verdict,confidence,payload,created_at) VALUES(?,?,?,?,?,?)",
                data
            ).lastrowid

    def list(self,q=""):
        if self.postgres:
            with self._psycopg.connect(self.url) as c:
                rows=c.execute(
                    "SELECT id,kind,title,verdict,confidence,created_at FROM history WHERE title LIKE %s ORDER BY id DESC",
                    (f"%{q}%",)
                ).fetchall()
        else:
            with self._conn() as c:
                rows=c.execute(
                    "SELECT id,kind,title,verdict,confidence,created_at FROM history WHERE title LIKE ? ORDER BY id DESC",
                    (f"%{q}%",)
                ).fetchall()
        return [dict(zip(["id","kind","title","verdict","confidence","created_at"],r)) for r in rows]

    def get(self,id):
        if self.postgres:
            with self._psycopg.connect(self.url) as c:
                r=c.execute("SELECT payload FROM history WHERE id=%s",(id,)).fetchone()
        else:
            with self._conn() as c:
                r=c.execute("SELECT payload FROM history WHERE id=?",(id,)).fetchone()
        return json.loads(r[0]) if r else None

    def delete(self,id):
        if self.postgres:
            with self._psycopg.connect(self.url) as c:
                return c.execute("DELETE FROM history WHERE id=%s",(id,)).rowcount
        with self._conn() as c:
            return c.execute("DELETE FROM history WHERE id=?",(id,)).rowcount

    def clear(self):
        if self.postgres:
            with self._psycopg.connect(self.url) as c:
                c.execute("DELETE FROM history")
        else:
            with self._conn() as c:
                c.execute("DELETE FROM history")
