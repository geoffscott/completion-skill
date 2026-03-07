-- todo skill schema
-- SQLite database at ~/.openclaw/completion/tasks.db

CREATE TABLE IF NOT EXISTS roles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    weight REAL NOT NULL DEFAULT 1.0,  -- relative attention weight
    description TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'backlog'
        CHECK (status IN ('backlog', 'todo', 'in_progress', 'blocked', 'done')),
    priority TEXT NOT NULL DEFAULT 'p2'
        CHECK (priority IN ('p1', 'p2', 'p3')),
    role_id INTEGER NOT NULL REFERENCES roles(id),
    due_date TEXT,           -- ISO 8601 date, nullable
    notes TEXT,              -- free-form context, blockers, waiting-on info
    tags TEXT,               -- comma-separated labels
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Status history for future flow metrics
CREATE TABLE IF NOT EXISTS status_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    old_status TEXT,
    new_status TEXT NOT NULL,
    changed_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Trigger to track status changes automatically
CREATE TRIGGER IF NOT EXISTS track_status_change
AFTER UPDATE OF status ON tasks
WHEN OLD.status != NEW.status
BEGIN
    INSERT INTO status_history (task_id, old_status, new_status)
    VALUES (NEW.id, OLD.status, NEW.status);
    UPDATE tasks SET updated_at = datetime('now') WHERE id = NEW.id;
END;

-- Trigger to log initial status on insert
CREATE TRIGGER IF NOT EXISTS track_initial_status
AFTER INSERT ON tasks
BEGIN
    INSERT INTO status_history (task_id, old_status, new_status)
    VALUES (NEW.id, NULL, NEW.status);
END;

-- Useful indexes
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_role ON tasks(role_id);
CREATE INDEX IF NOT EXISTS idx_tasks_priority ON tasks(priority);
CREATE INDEX IF NOT EXISTS idx_tasks_due_date ON tasks(due_date);
CREATE INDEX IF NOT EXISTS idx_status_history_task ON status_history(task_id);
