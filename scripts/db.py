#!/usr/bin/env python3
"""
Shared database utilities for the completion skill.

All scripts (cli, rituals, init_db) should use these helpers
for consistent DB access, entity loading, and common queries.
"""

import json
import os
import sqlite3
from datetime import datetime
from typing import Dict, List, Optional, Tuple


DEFAULT_DB_PATH = os.path.expanduser("~/.openclaw/completion/tasks.db")
ENTITIES_PATH = os.path.expanduser("~/.openclaw/entities.json")


def open_db(db_path: str = None) -> sqlite3.Connection:
    """Open the completion database with row_factory enabled."""
    path = db_path or DEFAULT_DB_PATH
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Database not found at {path}. Run: python3 scripts/init_db.py"
        )
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def get_roles(conn: sqlite3.Connection) -> Dict[int, dict]:
    """Return {role_id: {name, weight, description}}."""
    c = conn.execute("SELECT id, name, weight, description FROM roles ORDER BY weight DESC")
    return {
        row["id"]: {"name": row["name"], "weight": row["weight"], "description": row["description"]}
        for row in c.fetchall()
    }


def find_role_by_name(conn: sqlite3.Connection, name: str) -> Optional[dict]:
    """Case-insensitive role lookup. Returns {id, name, weight} or None."""
    c = conn.execute(
        "SELECT id, name, weight FROM roles WHERE LOWER(name) = LOWER(?)", (name,)
    )
    row = c.fetchone()
    return dict(row) if row else None


def get_tasks(
    conn: sqlite3.Connection,
    *,
    role: str = None,
    status: str = None,
    statuses: List[str] = None,
    priority: str = None,
    entity: str = None,
    exclude_done: bool = True,
    order_by: str = "t.priority ASC, t.due_date ASC NULLS LAST, t.created_at ASC",
) -> List[dict]:
    """
    Flexible task query. Returns list of dicts with role_name included.
    
    Filters:
      role       — filter by role name (case-insensitive)
      status     — single status filter
      statuses   — list of statuses to include
      priority   — filter by priority level
      entity     — filter by entity ID (LIKE match on JSON array)
      exclude_done — exclude 'done' tasks (default True)
    """
    clauses = []
    params = []

    if role:
        clauses.append("LOWER(r.name) = LOWER(?)")
        params.append(role)

    if status:
        clauses.append("t.status = ?")
        params.append(status)
    elif statuses:
        placeholders = ",".join("?" for _ in statuses)
        clauses.append(f"t.status IN ({placeholders})")
        params.extend(statuses)
    elif exclude_done:
        clauses.append("t.status != 'done'")

    if priority:
        clauses.append("t.priority = ?")
        params.append(priority)

    if entity:
        clauses.append("t.entities LIKE ?")
        params.append(f'%"{entity}"%')

    where = " AND ".join(clauses) if clauses else "1=1"

    sql = f"""
        SELECT t.id, t.title, t.status, t.priority, t.role_id, r.name as role_name,
               t.due_date, t.notes, t.tags, t.entities, t.created_at, t.updated_at
        FROM tasks t
        JOIN roles r ON t.role_id = r.id
        WHERE {where}
        ORDER BY {order_by}
    """
    rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def get_task_by_id(conn: sqlite3.Connection, task_id: int) -> Optional[dict]:
    """Fetch a single task by ID, with role_name."""
    row = conn.execute("""
        SELECT t.id, t.title, t.status, t.priority, t.role_id, r.name as role_name,
               t.due_date, t.notes, t.tags, t.entities, t.created_at, t.updated_at
        FROM tasks t
        JOIN roles r ON t.role_id = r.id
        WHERE t.id = ?
    """, (task_id,)).fetchone()
    return dict(row) if row else None


def add_task(
    conn: sqlite3.Connection,
    title: str,
    role_id: int,
    *,
    status: str = "backlog",
    priority: str = "p2",
    due_date: str = None,
    notes: str = None,
    tags: str = None,
    entities: List[str] = None,
) -> int:
    """Insert a task. Returns the new task ID."""
    entities_json = json.dumps(entities) if entities else None
    c = conn.execute(
        """INSERT INTO tasks (title, status, priority, role_id, due_date, notes, tags, entities)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (title, status, priority, role_id, due_date, notes, tags, entities_json),
    )
    conn.commit()
    return c.lastrowid


def update_task(conn: sqlite3.Connection, task_id: int, **fields) -> bool:
    """
    Update task fields by ID. Supports: title, status, priority, due_date,
    notes, tags, entities, role_id. Returns True if task was found.
    """
    allowed = {"title", "status", "priority", "due_date", "notes", "tags", "entities", "role_id"}
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    
    if not updates:
        return False

    if "entities" in updates and isinstance(updates["entities"], list):
        updates["entities"] = json.dumps(updates["entities"])

    set_clause = ", ".join(f"{k} = ?" for k in updates)
    params = list(updates.values()) + [task_id]

    c = conn.execute(f"UPDATE tasks SET {set_clause} WHERE id = ?", params)
    conn.commit()
    return c.rowcount > 0


# --- Entity helpers ---

def load_entity_registry() -> Dict:
    """Load the shared entity registry. Returns empty dict if not found."""
    if os.path.exists(ENTITIES_PATH):
        with open(ENTITIES_PATH) as f:
            return json.load(f)
    return {}


def build_entity_index(registry: Dict) -> Dict[str, str]:
    """
    Build a case-insensitive lookup: {lowercase_name: entity_id}.
    Covers all aliases across people, organizations, projects.
    """
    index = {}
    for category in ("people", "organizations", "projects"):
        for entity in registry.get(category, []):
            eid = entity.get("id", "")
            for name in entity.get("names", []):
                index[name.lower()] = eid
    return index


def enrich_entities(text: str, entity_index: Dict[str, str]) -> List[str]:
    """
    Scan text for known entity names/aliases. Returns deduplicated list of entity IDs.
    Matches whole words only (case-insensitive).
    """
    if not entity_index or not text:
        return []

    text_lower = text.lower()
    found = set()

    # Sort by length descending so longer names match first (e.g., "Dani Pascarella" before "Dani")
    for name in sorted(entity_index.keys(), key=len, reverse=True):
        # Simple word-boundary check
        idx = text_lower.find(name)
        if idx != -1:
            # Check boundaries (start/end of string or non-alnum char)
            before = idx == 0 or not text_lower[idx - 1].isalnum()
            after = (idx + len(name) >= len(text_lower)) or not text_lower[idx + len(name)].isalnum()
            if before and after:
                found.add(entity_index[name])

    return sorted(found)


def infer_role_from_entities(
    conn: sqlite3.Connection, entity_ids: List[str], registry: Dict
) -> Optional[int]:
    """
    Given entity IDs, try to infer the most likely role.
    Returns role_id or None if ambiguous/unknown.
    
    Heuristic: look at entity contexts and map to role names.
    """
    if not entity_ids or not registry:
        return None

    # Build entity_id -> contexts mapping
    contexts = set()
    for category in ("people", "organizations", "projects"):
        for entity in registry.get(category, []):
            if entity.get("id") in entity_ids:
                for ctx in entity.get("contexts", []):
                    contexts.add(ctx.lower())

    # Try to match contexts to role names
    roles = get_roles(conn)
    for role_id, role_info in roles.items():
        if role_info["name"].lower() in contexts:
            return role_id

    return None


# --- Formatting helpers ---

STATUS_ORDER = {"in_progress": 0, "to-do": 1, "blocked": 2, "backlog": 3, "done": 4}


def format_task_line(task: dict, number: int = None) -> str:
    """Format a single task as a compact one-liner."""
    prefix = f"#{number}" if number else f"[{task['id']}]"
    status_icon = {
        "in_progress": "🔵",
        "to-do": "⚪",
        "blocked": "🔴",
        "backlog": "⏳",
        "done": "✅",
    }.get(task["status"], "❓")
    
    priority = task["priority"].upper()
    due = f" 📅 {task['due_date']}" if task.get("due_date") else ""
    
    return f"{prefix} {status_icon} [{priority}] {task['title']}{due}"


def format_task_table(tasks: List[dict], numbered: bool = True) -> str:
    """Format tasks as a numbered list grouped by role."""
    if not tasks:
        return "No tasks found."

    # Group by role
    by_role = {}
    for t in tasks:
        role = t["role_name"]
        if role not in by_role:
            by_role[role] = []
        by_role[role].append(t)

    lines = []
    n = 1
    for role_name, role_tasks in by_role.items():
        lines.append(f"\n**{role_name}**")
        # Sort within role by status order, then priority
        role_tasks.sort(key=lambda t: (STATUS_ORDER.get(t["status"], 9), t["priority"]))
        for t in role_tasks:
            num = n if numbered else None
            lines.append(format_task_line(t, number=num))
            if numbered:
                n += 1

    return "\n".join(lines)
