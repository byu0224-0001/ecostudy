import json
import os
import sqlite3
from pathlib import Path


def default_path(root: Path) -> Path:
    override = os.environ.get("RADAR_DB", "").strip()
    if override:
        return Path(override)
    if os.environ.get("VERCEL"):
        return Path("/tmp/radar.sqlite")
    return root / "data" / "radar.sqlite"


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(
        """
        create table if not exists briefs (
            id text primary key,
            keyword text not null,
            created_at text not null,
            payload text not null
        )
        """
    )
    return conn


def save_brief(conn: sqlite3.Connection, report: dict) -> None:
    conn.execute(
        "insert or replace into briefs (id, keyword, created_at, payload) values (?, ?, ?, ?)",
        (report["id"], report["keyword"], report["generated_at"], json.dumps(report, ensure_ascii=False)),
    )
    conn.commit()


def list_briefs(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("select payload from briefs order by created_at desc").fetchall()
    return [json.loads(row[0]) for row in rows]


def get_brief(conn: sqlite3.Connection, report_id: str) -> dict | None:
    row = conn.execute("select payload from briefs where id = ?", (report_id,)).fetchone()
    if row is None:
        return None
    return json.loads(row[0])
