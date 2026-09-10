# -*- coding: utf-8 -*-
"""SQLite 连接与表结构。"""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "calibrator.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    chapter_policy TEXT NOT NULL DEFAULT 'any',       -- any | recto | even
    cross_chapter INTEGER NOT NULL DEFAULT 1,        -- 1: 允许跨章范围, 0: 不允许
    heading_regex TEXT NOT NULL DEFAULT '^\\s*(第[0-9零一二三四五六七八九十百]+[章节]|chapter\\s+[0-9ivxlcdm]+|\\d+\\.)',
    batch_threshold REAL NOT NULL DEFAULT 0.85
);

CREATE TABLE IF NOT EXISTS pages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    edition TEXT NOT NULL CHECK (edition IN ('old', 'new')),
    page_no INTEGER NOT NULL,
    label TEXT,
    content TEXT NOT NULL,
    UNIQUE (project_id, edition, page_no)
);

CREATE TABLE IF NOT EXISTS entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    term TEXT NOT NULL,
    subterm TEXT,
    kind TEXT NOT NULL DEFAULT 'term',               -- term | see | seealso
    ref_target TEXT,
    parent_id INTEGER REFERENCES entries(id) ON DELETE SET NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    UNIQUE (project_id, term, subterm, kind, ref_target)
);

CREATE TABLE IF NOT EXISTS locators (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    old_start INTEGER NOT NULL,
    old_end INTEGER,
    new_start INTEGER,
    new_end INTEGER,
    status TEXT NOT NULL DEFAULT 'pending',          -- pending | confirmed | rejected
    anchor TEXT,
    UNIQUE (entry_id, old_start, old_end)
);

CREATE TABLE IF NOT EXISTS candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    locator_id INTEGER NOT NULL REFERENCES locators(id) ON DELETE CASCADE,
    rank INTEGER NOT NULL,
    new_start INTEGER NOT NULL,
    new_end INTEGER NOT NULL,
    method TEXT NOT NULL,
    score REAL NOT NULL,
    reasons TEXT NOT NULL DEFAULT '[]',
    UNIQUE (locator_id, rank)
);

CREATE TABLE IF NOT EXISTS issues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    code TEXT NOT NULL,
    severity TEXT NOT NULL,
    entry_id INTEGER REFERENCES entries(id) ON DELETE CASCADE,
    locator_id INTEGER REFERENCES locators(id) ON DELETE CASCADE,
    message TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',             -- open | resolved
    UNIQUE (project_id, code, entry_id, locator_id, message)
);

CREATE TABLE IF NOT EXISTS action_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    ts TEXT NOT NULL DEFAULT (datetime('now')),
    kind TEXT NOT NULL,
    detail TEXT NOT NULL,
    undo_sql TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    ts TEXT NOT NULL DEFAULT (datetime('now')),
    payload TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (project_id, key)
);
"""


def get_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_db()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()
