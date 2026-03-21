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
    Registry format: {category: {entity_id: {names: [...], ...}}}
    """
    index = {}
    for category in ("people", "organizations", "projects"):
        for eid, entity in registry.get(category, {}).items():
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
        for eid, entity in registry.get(category, {}).items():
            if eid in entity_ids:
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


STATUS_ICONS = {
    "in_progress": "🔵",
    "to-do": "⚪",
    "blocked": "🔴",
    "backlog": "⏳",
    "done": "✅",
}

PRIORITY_ICONS = {
    "p1": "🔴",
    "p2": "🟡",
    "p3": "⚪",
}

STATUS_LABELS = {
    "in_progress": "IN PROGRESS",
    "to-do": "TO-DO",
    "blocked": "BLOCKED",
    "backlog": "BACKLOG",
    "done": "DONE",
}


def format_task_line(task: dict, show_role: bool = False, show_entities: bool = False, registry: Dict = None) -> str:
    """Format a single task as an indented bullet line. Always uses DB ID."""
    due = f" 📅 {task['due_date']}" if task.get("due_date") else ""
    role = f" ({task['role_name']})" if show_role else ""
    line = f"  #{task['id']}: {task['title']}{due}{role}"
    
    if show_entities and task.get("entities"):
        entity_ids = json.loads(task["entities"]) if isinstance(task["entities"], str) else task["entities"]
        if entity_ids and registry:
            details = []
            for eid in entity_ids:
                entity_info = _resolve_entity(eid, registry)
                if entity_info:
                    details.append(entity_info)
                else:
                    details.append(eid)
            line += f"\n       → {', '.join(details)}"
    
    return line


def _resolve_entity(eid: str, registry: Dict) -> Optional[str]:
    """Look up an entity ID and return a rich display string."""
    for category in ("people", "organizations", "projects"):
        cat_data = registry.get(category, {})
        if eid in cat_data:
            entity = cat_data[eid]
            name = entity.get("names", [eid])[0]
            cat_label = {"people": "👤", "organizations": "🏢", "projects": "📋"}.get(category, "")
            role_or_desc = entity.get("role", entity.get("description", ""))
            if role_or_desc:
                return f"{cat_label} {name} — {role_or_desc}"
            return f"{cat_label} {name}"
    return None


def format_task_table(
    tasks: List[dict],
    group_by: str = "status",
    show_entities: bool = False,
) -> str:
    """
    Format tasks for Discord/Slack-friendly output.
    
    group_by:
      "status"  — group by status+priority (default, Discord-friendly)
      "role"    — group by role, then sort by status+priority within
    """
    if not tasks:
        return "No tasks found."

    if group_by == "role":
        return _format_by_role(tasks, show_entities=show_entities)
    return _format_by_status(tasks, show_entities=show_entities)


def _format_by_status(tasks: List[dict], show_entities: bool = False) -> str:
    """Group by status, sub-group by priority. Bold headers with emoji."""
    # Build groups: (status, priority) -> [tasks]
    groups = {}
    for t in tasks:
        key = (t["status"], t["priority"])
        if key not in groups:
            groups[key] = []
        groups[key].append(t)

    # Sort groups by status order, then priority
    sorted_keys = sorted(
        groups.keys(),
        key=lambda k: (STATUS_ORDER.get(k[0], 9), k[1])
    )

    show_role = len(set(t["role_name"] for t in tasks)) > 1
    registry = load_entity_registry() if show_entities else None
    lines = []
    for status, priority in sorted_keys:
        icon = PRIORITY_ICONS.get(priority, "⚪")
        label = STATUS_LABELS.get(status, status.upper())
        lines.append(f"**{icon} {priority.upper()} {label}**")
        for t in groups[(status, priority)]:
            lines.append(format_task_line(t, show_role=show_role, show_entities=show_entities, registry=registry))
        lines.append("")  # blank line between groups

    return "\n".join(lines).rstrip()


def _format_by_role(tasks: List[dict], show_entities: bool = False) -> str:
    """Group by role, sort by status+priority within. For role-filtered views."""
    by_role = {}
    for t in tasks:
        role = t["role_name"]
        if role not in by_role:
            by_role[role] = []
        by_role[role].append(t)

    registry = load_entity_registry() if show_entities else None
    lines = []
    for role_name, role_tasks in by_role.items():
        lines.append(f"**{role_name}**")
        role_tasks.sort(key=lambda t: (STATUS_ORDER.get(t["status"], 9), t["priority"]))
        for t in role_tasks:
            lines.append(format_task_line(t, show_entities=show_entities, registry=registry))
        lines.append("")

    return "\n".join(lines).rstrip()
