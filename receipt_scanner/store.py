from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

import psycopg

SQLITE_SCHEMA = """
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS receipts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  source_name TEXT,
  ocr_engine TEXT,
  ocr_notes TEXT,
  ocr_text TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id TEXT NOT NULL,
  receipt_id INTEGER NOT NULL,
  name TEXT NOT NULL,
  qty REAL,
  unit_price REAL,
  amount REAL,
  raw_line TEXT,
  FOREIGN KEY(receipt_id) REFERENCES receipts(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS name_aliases (
  user_id TEXT NOT NULL,
  raw_name TEXT NOT NULL,
  canonical_name TEXT NOT NULL,
  cnt INTEGER NOT NULL DEFAULT 1,
  last_seen_at TEXT NOT NULL,
  PRIMARY KEY(user_id, raw_name, canonical_name)
);

CREATE INDEX IF NOT EXISTS idx_items_user ON items(user_id);
CREATE INDEX IF NOT EXISTS idx_items_name ON items(name);
CREATE INDEX IF NOT EXISTS idx_items_receipt ON items(receipt_id);
CREATE INDEX IF NOT EXISTS idx_receipts_user ON receipts(user_id);
CREATE INDEX IF NOT EXISTS idx_aliases_user_raw ON name_aliases(user_id, raw_name);
CREATE INDEX IF NOT EXISTS idx_aliases_user_canonical ON name_aliases(user_id, canonical_name);
"""

POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS receipts (
  id BIGSERIAL PRIMARY KEY,
  user_id TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  source_name TEXT,
  ocr_engine TEXT,
  ocr_notes TEXT,
  ocr_text TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS items (
  id BIGSERIAL PRIMARY KEY,
  user_id TEXT NOT NULL,
  receipt_id BIGINT NOT NULL REFERENCES receipts(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  qty DOUBLE PRECISION,
  unit_price DOUBLE PRECISION,
  amount DOUBLE PRECISION,
  raw_line TEXT
);

CREATE TABLE IF NOT EXISTS name_aliases (
  user_id TEXT NOT NULL,
  raw_name TEXT NOT NULL,
  canonical_name TEXT NOT NULL,
  cnt INTEGER NOT NULL DEFAULT 1,
  last_seen_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY(user_id, raw_name, canonical_name)
);

CREATE INDEX IF NOT EXISTS idx_items_user ON items(user_id);
CREATE INDEX IF NOT EXISTS idx_items_name ON items(name);
CREATE INDEX IF NOT EXISTS idx_items_receipt ON items(receipt_id);
CREATE INDEX IF NOT EXISTS idx_receipts_user ON receipts(user_id);
CREATE INDEX IF NOT EXISTS idx_aliases_user_raw ON name_aliases(user_id, raw_name);
CREATE INDEX IF NOT EXISTS idx_aliases_user_canonical ON name_aliases(user_id, canonical_name);
"""


@dataclass
class ReceiptRow:
    id: int
    created_at: str
    source_name: Optional[str]
    ocr_engine: Optional[str]
    ocr_notes: Optional[str]

def _is_postgres(dsn: str) -> bool:
    dsn = (dsn or "").strip()
    return dsn.startswith("postgres://") or dsn.startswith("postgresql://")


def _ensure_sqlite_columns(conn: sqlite3.Connection) -> None:
    # 兼容老的 sqlite 文件（早期没有 user_id 列）
    cur = conn.execute("PRAGMA table_info(receipts)")
    cols = {r[1] for r in cur.fetchall()}
    if "user_id" not in cols:
        conn.execute("ALTER TABLE receipts ADD COLUMN user_id TEXT")
        conn.execute("UPDATE receipts SET user_id = COALESCE(user_id, 'local') WHERE user_id IS NULL")

    cur = conn.execute("PRAGMA table_info(items)")
    cols = {r[1] for r in cur.fetchall()}
    if "user_id" not in cols:
        conn.execute("ALTER TABLE items ADD COLUMN user_id TEXT")
        conn.execute("UPDATE items SET user_id = COALESCE(user_id, 'local') WHERE user_id IS NULL")

    # name_aliases 旧主键不含 user_id：简单起见，新建表并搬运
    cur = conn.execute("PRAGMA table_info(name_aliases)")
    cols = {r[1] for r in cur.fetchall()}
    if "user_id" not in cols:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS name_aliases_new (
              user_id TEXT NOT NULL,
              raw_name TEXT NOT NULL,
              canonical_name TEXT NOT NULL,
              cnt INTEGER NOT NULL DEFAULT 1,
              last_seen_at TEXT NOT NULL,
              PRIMARY KEY(user_id, raw_name, canonical_name)
            )
            """
        )
        try:
            conn.execute(
                """
                INSERT OR IGNORE INTO name_aliases_new(user_id, raw_name, canonical_name, cnt, last_seen_at)
                SELECT 'local' AS user_id, raw_name, canonical_name, cnt, last_seen_at
                FROM name_aliases
                """
            )
            conn.execute("DROP TABLE name_aliases")
            conn.execute("ALTER TABLE name_aliases_new RENAME TO name_aliases")
        except Exception:
            # 如果旧表不存在或结构异常，交给 executescript 的 IF NOT EXISTS 兜底
            pass


def ensure_db(dsn: str) -> None:
    if _is_postgres(dsn):
        with psycopg.connect(dsn) as conn:
            conn.execute("SET TIME ZONE 'UTC'")
            for stmt in [s.strip() for s in POSTGRES_SCHEMA.split(";") if s.strip()]:
                conn.execute(stmt)
        return

    Path(os.path.dirname(dsn) or ".").mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(dsn) as conn:
        conn.execute("PRAGMA foreign_keys = ON;")
        _ensure_sqlite_columns(conn)
        conn.executescript(SQLITE_SCHEMA)


def insert_receipt(
    dsn: str,
    *,
    user_id: str,
    source_name: Optional[str],
    ocr_engine: Optional[str],
    ocr_notes: Optional[str],
    ocr_text: str,
) -> int:
    ensure_db(dsn)
    created_at = datetime.now().isoformat(timespec="seconds")
    if _is_postgres(dsn):
        with psycopg.connect(dsn) as conn:
            cur = conn.execute(
                """
                INSERT INTO receipts(user_id, created_at, source_name, ocr_engine, ocr_notes, ocr_text)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (user_id, created_at, source_name, ocr_engine, ocr_notes, ocr_text),
            )
            return int(cur.fetchone()[0])

    with sqlite3.connect(dsn) as conn:
        conn.execute("PRAGMA foreign_keys = ON;")
        cur = conn.execute(
            """
            INSERT INTO receipts(user_id, created_at, source_name, ocr_engine, ocr_notes, ocr_text)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_id, created_at, source_name, ocr_engine, ocr_notes, ocr_text),
        )
        return int(cur.lastrowid)


def insert_items(dsn: str, *, user_id: str, receipt_id: int, rows: Iterable[dict]) -> None:
    ensure_db(dsn)
    if _is_postgres(dsn):
        with psycopg.connect(dsn) as conn:
            conn.executemany(
                """
                INSERT INTO items(user_id, receipt_id, name, qty, unit_price, amount, raw_line)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                [
                    (
                        user_id,
                        receipt_id,
                        r.get("name") or "",
                        r.get("qty"),
                        r.get("unit_price"),
                        r.get("amount"),
                        r.get("raw"),
                    )
                    for r in rows
                ],
            )
        return

    with sqlite3.connect(dsn) as conn:
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.executemany(
            """
            INSERT INTO items(user_id, receipt_id, name, qty, unit_price, amount, raw_line)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    user_id,
                    receipt_id,
                    r.get("name") or "",
                    r.get("qty"),
                    r.get("unit_price"),
                    r.get("amount"),
                    r.get("raw"),
                )
                for r in rows
            ),
        )


def list_receipts(dsn: str, *, user_id: str, limit: int = 50) -> list[ReceiptRow]:
    ensure_db(dsn)
    if _is_postgres(dsn):
        with psycopg.connect(dsn) as conn:
            cur = conn.execute(
                """
                SELECT id, created_at::text AS created_at, source_name, ocr_engine, ocr_notes
                FROM receipts
                WHERE user_id = %s
                ORDER BY id DESC
                LIMIT %s
                """,
                (user_id, limit),
            )
            return [ReceiptRow(**dict(zip([d.name for d in cur.description], row))) for row in cur.fetchall()]

    with sqlite3.connect(dsn) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            """
            SELECT id, created_at, source_name, ocr_engine, ocr_notes
            FROM receipts
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (user_id, limit),
        )
        return [ReceiptRow(**dict(r)) for r in cur.fetchall()]


def query_items(
    dsn: str,
    *,
    user_id: str,
    name_like: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    limit: int = 500,
) -> list[dict]:
    ensure_db(dsn)
    where = []
    params: list[object] = []

    where.append("i.user_id = ?")
    params.append(user_id)

    if name_like:
        where.append("i.name LIKE ?")
        params.append(f"%{name_like}%")
    if from_date:
        where.append("r.created_at >= ?")
        params.append(from_date)
    if to_date:
        where.append("r.created_at <= ?")
        params.append(to_date)

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    if _is_postgres(dsn):
        # 重新拼 params/占位符
        where_pg = []
        params_pg: list[object] = [user_id]
        where_pg.append("i.user_id = %s")
        if name_like:
            where_pg.append("i.name ILIKE %s")
            params_pg.append(f"%{name_like}%")
        if from_date:
            where_pg.append("r.created_at >= %s")
            params_pg.append(from_date)
        if to_date:
            where_pg.append("r.created_at <= %s")
            params_pg.append(to_date)
        where_sql_pg = "WHERE " + " AND ".join(where_pg)

        with psycopg.connect(dsn) as conn:
            cur = conn.execute(
                f"""
                SELECT
                  i.id,
                  r.created_at::text AS created_at,
                  r.source_name,
                  i.name,
                  i.qty,
                  i.unit_price,
                  i.amount
                FROM items i
                JOIN receipts r ON r.id = i.receipt_id
                {where_sql_pg}
                ORDER BY i.id DESC
                LIMIT %s
                """,
                (*params_pg, limit),
            )
            cols = [d.name for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

    with sqlite3.connect(dsn) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            f"""
            SELECT
              i.id,
              r.created_at,
              r.source_name,
              i.name,
              i.qty,
              i.unit_price,
              i.amount
            FROM items i
            JOIN receipts r ON r.id = i.receipt_id
            {where_sql}
            ORDER BY i.id DESC
            LIMIT ?
            """,
            (*params, limit),
        )
        return [dict(r) for r in cur.fetchall()]


def upsert_alias(dsn: str, *, user_id: str, raw_name: str, canonical_name: str) -> None:
    raw_name = (raw_name or "").strip()
    canonical_name = (canonical_name or "").strip()
    if not raw_name or not canonical_name:
        return
    ensure_db(dsn)
    now = datetime.now().isoformat(timespec="seconds")
    if _is_postgres(dsn):
        with psycopg.connect(dsn) as conn:
            conn.execute(
                """
                INSERT INTO name_aliases(user_id, raw_name, canonical_name, cnt, last_seen_at)
                VALUES (%s, %s, %s, 1, %s)
                ON CONFLICT(user_id, raw_name, canonical_name) DO UPDATE SET
                  cnt = name_aliases.cnt + 1,
                  last_seen_at = EXCLUDED.last_seen_at
                """,
                (user_id, raw_name, canonical_name, now),
            )
        return

    with sqlite3.connect(dsn) as conn:
        conn.execute(
            """
            INSERT INTO name_aliases(user_id, raw_name, canonical_name, cnt, last_seen_at)
            VALUES (?, ?, ?, 1, ?)
            ON CONFLICT(user_id, raw_name, canonical_name) DO UPDATE SET
              cnt = cnt + 1,
              last_seen_at = excluded.last_seen_at
            """,
            (user_id, raw_name, canonical_name, now),
        )


def best_canonical_for_raw(dsn: str, *, user_id: str, raw_name: str) -> Optional[str]:
    raw_name = (raw_name or "").strip()
    if not raw_name:
        return None
    ensure_db(dsn)
    if _is_postgres(dsn):
        with psycopg.connect(dsn) as conn:
            cur = conn.execute(
                """
                SELECT canonical_name
                FROM name_aliases
                WHERE user_id = %s AND raw_name = %s
                ORDER BY cnt DESC, last_seen_at DESC
                LIMIT 1
                """,
                (user_id, raw_name),
            )
            row = cur.fetchone()
            return str(row[0]) if row else None

    with sqlite3.connect(dsn) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            """
            SELECT canonical_name
            FROM name_aliases
            WHERE user_id = ? AND raw_name = ?
            ORDER BY cnt DESC, last_seen_at DESC
            LIMIT 1
            """,
            (user_id, raw_name),
        )
        row = cur.fetchone()
        return str(row["canonical_name"]) if row else None


def list_canonical_names(dsn: str, *, user_id: str, limit: int = 5000) -> list[str]:
    ensure_db(dsn)
    if _is_postgres(dsn):
        with psycopg.connect(dsn) as conn:
            cur = conn.execute(
                """
                SELECT canonical_name, SUM(cnt) AS total
                FROM name_aliases
                WHERE user_id = %s
                GROUP BY canonical_name
                ORDER BY total DESC, canonical_name ASC
                LIMIT %s
                """,
                (user_id, limit),
            )
            return [str(r[0]) for r in cur.fetchall()]

    with sqlite3.connect(dsn) as conn:
        cur = conn.execute(
            """
            SELECT canonical_name, SUM(cnt) AS total
            FROM name_aliases
            WHERE user_id = ?
            GROUP BY canonical_name
            ORDER BY total DESC, canonical_name ASC
            LIMIT ?
            """,
            (user_id, limit),
        )
        return [str(r[0]) for r in cur.fetchall()]


def list_aliases(dsn: str, *, user_id: str, limit: int = 200) -> list[dict]:
    ensure_db(dsn)
    if _is_postgres(dsn):
        with psycopg.connect(dsn) as conn:
            cur = conn.execute(
                """
                SELECT raw_name, canonical_name, cnt, last_seen_at::text AS last_seen_at
                FROM name_aliases
                WHERE user_id = %s
                ORDER BY cnt DESC, last_seen_at DESC
                LIMIT %s
                """,
                (user_id, limit),
            )
            cols = [d.name for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

    with sqlite3.connect(dsn) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            """
            SELECT raw_name, canonical_name, cnt, last_seen_at
            FROM name_aliases
            WHERE user_id = ?
            ORDER BY cnt DESC, last_seen_at DESC
            LIMIT ?
            """,
            (user_id, limit),
        )
        return [dict(r) for r in cur.fetchall()]


def top_items(dsn: str, *, user_id: str, limit: int = 30) -> list[dict]:
    ensure_db(dsn)
    if _is_postgres(dsn):
        with psycopg.connect(dsn) as conn:
            cur = conn.execute(
                """
                SELECT name, COUNT(*) AS cnt, ROUND(SUM(COALESCE(amount, 0))::numeric, 2) AS total_amount
                FROM items
                WHERE user_id = %s
                GROUP BY name
                ORDER BY cnt DESC, total_amount DESC, name ASC
                LIMIT %s
                """,
                (user_id, limit),
            )
            cols = [d.name for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

    with sqlite3.connect(dsn) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            """
            SELECT name, COUNT(*) AS cnt, ROUND(SUM(COALESCE(amount, 0)), 2) AS total_amount
            FROM items
            WHERE user_id = ?
            GROUP BY name
            ORDER BY cnt DESC, total_amount DESC, name ASC
            LIMIT ?
            """,
            (user_id, limit),
        )
        return [dict(r) for r in cur.fetchall()]


def stats_items(
    dsn: str,
    *,
    user_id: str,
    name_like: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
) -> dict:
    ensure_db(dsn)
    if _is_postgres(dsn):
        where_pg = ["i.user_id = %s"]
        params_pg: list[object] = [user_id]
        if name_like:
            where_pg.append("i.name ILIKE %s")
            params_pg.append(f"%{name_like}%")
        if from_date:
            where_pg.append("r.created_at >= %s")
            params_pg.append(from_date)
        if to_date:
            where_pg.append("r.created_at <= %s")
            params_pg.append(to_date)
        where_sql_pg = "WHERE " + " AND ".join(where_pg)
        with psycopg.connect(dsn) as conn:
            cur = conn.execute(
                f"""
                SELECT
                  COUNT(*) AS cnt,
                  ROUND(SUM(COALESCE(i.amount, 0))::numeric, 2) AS total_amount
                FROM items i
                JOIN receipts r ON r.id = i.receipt_id
                {where_sql_pg}
                """,
                tuple(params_pg),
            )
            row = cur.fetchone()
            return {"cnt": int(row[0] or 0), "total_amount": float(row[1] or 0.0)}

    where = ["i.user_id = ?"]
    params: list[object] = [user_id]
    if name_like:
        where.append("i.name LIKE ?")
        params.append(f"%{name_like}%")
    if from_date:
        where.append("r.created_at >= ?")
        params.append(from_date)
    if to_date:
        where.append("r.created_at <= ?")
        params.append(to_date)

    where_sql = "WHERE " + " AND ".join(where)
    with sqlite3.connect(dsn) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            f"""
            SELECT
              COUNT(*) AS cnt,
              ROUND(SUM(COALESCE(i.amount, 0)), 2) AS total_amount
            FROM items i
            JOIN receipts r ON r.id = i.receipt_id
            {where_sql}
            """,
            tuple(params),
        )
        row = cur.fetchone()
        return {"cnt": int(row["cnt"] or 0), "total_amount": float(row["total_amount"] or 0.0)}


def top_items_filtered(
    dsn: str,
    *,
    user_id: str,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    limit: int = 30,
) -> list[dict]:
    ensure_db(dsn)
    if _is_postgres(dsn):
        where_pg = ["i.user_id = %s"]
        params_pg: list[object] = [user_id]
        if from_date:
            where_pg.append("r.created_at >= %s")
            params_pg.append(from_date)
        if to_date:
            where_pg.append("r.created_at <= %s")
            params_pg.append(to_date)
        where_sql_pg = "WHERE " + " AND ".join(where_pg)
        with psycopg.connect(dsn) as conn:
            cur = conn.execute(
                f"""
                SELECT
                  i.name,
                  COUNT(*) AS cnt,
                  ROUND(SUM(COALESCE(i.amount, 0))::numeric, 2) AS total_amount
                FROM items i
                JOIN receipts r ON r.id = i.receipt_id
                {where_sql_pg}
                GROUP BY i.name
                ORDER BY cnt DESC, total_amount DESC, i.name ASC
                LIMIT %s
                """,
                (*params_pg, limit),
            )
            cols = [d.name for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

    where = ["i.user_id = ?"]
    params: list[object] = [user_id]
    if from_date:
        where.append("r.created_at >= ?")
        params.append(from_date)
    if to_date:
        where.append("r.created_at <= ?")
        params.append(to_date)
    where_sql = "WHERE " + " AND ".join(where)

    with sqlite3.connect(dsn) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            f"""
            SELECT
              i.name,
              COUNT(*) AS cnt,
              ROUND(SUM(COALESCE(i.amount, 0)), 2) AS total_amount
            FROM items i
            JOIN receipts r ON r.id = i.receipt_id
            {where_sql}
            GROUP BY i.name
            ORDER BY cnt DESC, total_amount DESC, i.name ASC
            LIMIT ?
            """,
            (*params, limit),
        )
        return [dict(r) for r in cur.fetchall()]

