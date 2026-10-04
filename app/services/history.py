import json,sqlite3
from pathlib import Path
from ..config import get_settings
class HistoryStore:
    def __init__(self):
        u=get_settings().database_url
        if not u.startswith("sqlite"):raise RuntimeError("Use SQLite for the included single-instance history store; migrate this adapter to PostgreSQL before horizontal scaling.")
        self.path=Path(u.replace("sqlite:///","")); self.path.parent.mkdir(parents=True,exist_ok=True)
        with self._conn() as c:c.execute("CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, title TEXT, verdict TEXT NOT NULL, confidence INTEGER, payload TEXT NOT NULL, created_at TEXT NOT NULL)")
    def _conn(self):return sqlite3.connect(self.path)
    def add(self,kind,title,verdict,confidence,payload,created_at):
        with self._conn() as c:return c.execute("INSERT INTO history(kind,title,verdict,confidence,payload,created_at) VALUES(?,?,?,?,?,?)",(kind,title,verdict,confidence,json.dumps(payload,ensure_ascii=False),created_at)).lastrowid
    def list(self,q=""):
        with self._conn() as c:rows=c.execute("SELECT id,kind,title,verdict,confidence,created_at FROM history WHERE title LIKE ? ORDER BY id DESC",(f"%{q}%",)).fetchall()
        return [dict(zip(["id","kind","title","verdict","confidence","created_at"],r)) for r in rows]
    def get(self,id):
        with self._conn() as c:r=c.execute("SELECT payload FROM history WHERE id=?",(id,)).fetchone()
        return json.loads(r[0]) if r else None
    def delete(self,id):
        with self._conn() as c:return c.execute("DELETE FROM history WHERE id=?",(id,)).rowcount
    def clear(self):
        with self._conn() as c:c.execute("DELETE FROM history")
