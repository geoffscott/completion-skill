#!/usr/bin/env python3
"""Initialize the completion skill database. Idempotent — safe to run repeatedly."""

import os
import sqlite3
import sys

DB_DIR = os.path.expanduser("~/.openclaw/completion")
DB_PATH = os.path.join(DB_DIR, "tasks.db")
SCHEMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "references", "schema.sql")

# Default roles — the user can add, rename, or remove these after initialization
DEFAULT_ROLES = [
    ("Work", 1.5, "Professional responsibilities, day job, career tasks"),
    ("Personal", 1.0, "Life admin, health, hobbies, relationships, learning"),
]


def init_db():
    os.makedirs(DB_DIR, exist_ok=True)

    is_new = not os.path.exists(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Read and execute schema
    with open(SCHEMA_PATH, "r") as f:
        schema_sql = f.read()
    cursor.executescript(schema_sql)

    # Insert default roles only if the roles table is empty
    cursor.execute("SELECT COUNT(*) FROM roles")
    if cursor.fetchone()[0] == 0:
        cursor.executemany(
            "INSERT INTO roles (name, weight, description) VALUES (?, ?, ?)",
            DEFAULT_ROLES,
        )
        conn.commit()
        if is_new:
            print(f"Created database at {DB_PATH}")
            print("Default roles:")
            for name, weight, desc in DEFAULT_ROLES:
                print(f"  {name} (weight: {weight}) — {desc}")
    else:
        if is_new:
            print(f"Database created at {DB_PATH} (roles already populated)")
        else:
            print(f"Database already exists at {DB_PATH}")

    conn.close()


if __name__ == "__main__":
    init_db()
