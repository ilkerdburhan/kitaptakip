import sqlite3
from pathlib import Path


DB_PATH = Path(__file__).resolve().parent / "data" / "kitap.db"


def get_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_db()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            yearly_goal INTEGER NOT NULL DEFAULT 24
        );

        INSERT OR IGNORE INTO settings (id, yearly_goal) VALUES (1, 24);

        CREATE TABLE IF NOT EXISTS books (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            author TEXT NOT NULL DEFAULT '',
            publisher TEXT NOT NULL DEFAULT '',
            cover TEXT NOT NULL DEFAULT '',
            source_url TEXT NOT NULL DEFAULT '',
            page_count INTEGER NOT NULL CHECK (page_count > 0),
            isbn TEXT NOT NULL DEFAULT '',
            publication_year TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL CHECK (status IN ('okunacak', 'okunuyor', 'okundu')),
            rating INTEGER NOT NULL DEFAULT 0 CHECK (rating BETWEEN 0 AND 5),
            note TEXT NOT NULL DEFAULT '',
            finished_at TEXT,
            created_at TEXT NOT NULL
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_books_source_url
            ON books(source_url) WHERE source_url <> '';
        CREATE UNIQUE INDEX IF NOT EXISTS idx_books_isbn
            ON books(isbn) WHERE isbn <> '';

        CREATE TABLE IF NOT EXISTS reading_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            book_id INTEGER NOT NULL,
            pages_read INTEGER NOT NULL,
            logged_at TEXT NOT NULL,
            FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE
        );
        """
    )
    conn.commit()
    conn.close()
