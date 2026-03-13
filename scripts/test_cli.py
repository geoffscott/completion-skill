#!/usr/bin/env python3
"""Tests for the completion skill CLI and db module."""

import json
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from db import (
    open_db, get_roles, find_role_by_name, get_tasks, get_task_by_id,
    add_task, update_task, load_entity_registry, build_entity_index,
    enrich_entities, format_task_table, format_task_line, STATUS_ORDER,
)

SCHEMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "references", "schema.sql")

passed = 0
failed = 0


def setup_test_db():
    """Create a temporary test database."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    
    conn = sqlite3.connect(tmp.name)
    conn.row_factory = sqlite3.Row
    
    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())
    
    # Insert test roles
    conn.execute("INSERT INTO roles (id, name, weight, description) VALUES (1, 'Work', 1.5, 'Day job')")
    conn.execute("INSERT INTO roles (id, name, weight, description) VALUES (2, 'Personal', 1.0, 'Life')")
    conn.commit()
    
    return tmp.name, conn


def test(name, condition):
    global passed, failed
    if condition:
        passed += 1
        print(f"  ✅ {name}")
    else:
        failed += 1
        print(f"  ❌ {name}")


def test_roles():
    print("\n--- Roles ---")
    db_path, conn = setup_test_db()
    
    roles = get_roles(conn)
    test("get_roles returns 2 roles", len(roles) == 2)
    test("Work role has weight 1.5", roles[1]["weight"] == 1.5)
    
    work = find_role_by_name(conn, "work")  # lowercase
    test("find_role_by_name case-insensitive", work is not None and work["name"] == "Work")
    
    none = find_role_by_name(conn, "nonexistent")
    test("find_role_by_name returns None for unknown", none is None)
    
    conn.close()
    os.unlink(db_path)


def test_crud():
    print("\n--- CRUD ---")
    db_path, conn = setup_test_db()
    
    # Add
    tid = add_task(conn, "Test task", 1, status="to-do", priority="p1")
    test("add_task returns ID", tid is not None and tid > 0)
    
    task = get_task_by_id(conn, tid)
    test("get_task_by_id finds it", task is not None)
    test("task has correct title", task["title"] == "Test task")
    test("task has correct status", task["status"] == "to-do")
    test("task has correct priority", task["priority"] == "p1")
    test("task has role_name", task["role_name"] == "Work")
    
    # Add with entities
    tid2 = add_task(conn, "Meet with Dani", 1, entities=["dani-pascarella", "oneeleven"])
    task2 = get_task_by_id(conn, tid2)
    test("task with entities stored as JSON", '"dani-pascarella"' in task2["entities"])
    
    # Update
    update_task(conn, tid, status="in_progress", notes="Working on it")
    updated = get_task_by_id(conn, tid)
    test("update_task changes status", updated["status"] == "in_progress")
    test("update_task sets notes", updated["notes"] == "Working on it")
    
    # Update nonexistent
    result = update_task(conn, 9999, status="done")
    test("update nonexistent returns False", result is False)
    
    conn.close()
    os.unlink(db_path)


def test_list_filters():
    print("\n--- List & Filters ---")
    db_path, conn = setup_test_db()
    
    add_task(conn, "Work P1", 1, status="in_progress", priority="p1")
    add_task(conn, "Work P2", 1, status="to-do", priority="p2")
    add_task(conn, "Personal P1", 2, status="to-do", priority="p1")
    add_task(conn, "Done task", 1, status="done", priority="p2")
    add_task(conn, "Entity task", 1, entities=["oneeleven"])
    
    # Default: exclude done
    all_active = get_tasks(conn)
    test("exclude_done by default", len(all_active) == 4)
    
    # Include done
    all_tasks = get_tasks(conn, exclude_done=False)
    test("include done with flag", len(all_tasks) == 5)
    
    # Role filter
    work_tasks = get_tasks(conn, role="Work")
    test("filter by role", len(work_tasks) == 3)  # 3 active Work tasks
    
    # Status filter
    in_prog = get_tasks(conn, status="in_progress")
    test("filter by status", len(in_prog) == 1)
    
    # Multi-status
    active = get_tasks(conn, statuses=["in_progress", "to-do"])
    test("filter by multiple statuses", len(active) == 3)
    
    # Priority filter
    p1s = get_tasks(conn, priority="p1")
    test("filter by priority", len(p1s) == 2)
    
    # Entity filter
    entity_tasks = get_tasks(conn, entity="oneeleven")
    test("filter by entity", len(entity_tasks) == 1)
    
    conn.close()
    os.unlink(db_path)


def test_entity_enrichment():
    print("\n--- Entity Enrichment ---")
    
    # Mock registry
    registry = {
        "people": [
            {"id": "dani-pascarella", "names": ["Dani", "Dani Pascarella"], "contexts": ["work"]},
            {"id": "frank-petriello", "names": ["Frank", "Frank Petriello"], "contexts": ["personal"]},
        ],
        "organizations": [
            {"id": "oneeleven", "names": ["OneEleven", "OE", "1.11"], "contexts": ["work"]},
            {"id": "saranam", "names": ["Saranam"], "contexts": ["personal"]},
        ],
        "projects": [],
    }
    
    index = build_entity_index(registry)
    test("build_entity_index has entries", len(index) > 0)
    test("index is lowercase", "dani" in index)
    test("index maps to ID", index["dani"] == "dani-pascarella")
    
    # Enrichment
    entities = enrich_entities("Talk to Dani about OneEleven Q2 planning", index)
    test("enrich finds Dani", "dani-pascarella" in entities)
    test("enrich finds OneEleven", "oneeleven" in entities)
    
    # No match
    entities2 = enrich_entities("Buy groceries", index)
    test("enrich returns empty for no match", len(entities2) == 0)
    
    # Partial word shouldn't match
    entities3 = enrich_entities("Frankenstein movie", index)
    # "Frank" appears as substring but with "enstein" after — boundary check
    test("boundary check prevents partial match", "frank-petriello" not in entities3)


def test_formatting():
    print("\n--- Formatting ---")
    
    task = {
        "id": 1, "title": "Test task", "status": "in_progress",
        "priority": "p1", "role_name": "Work", "role_id": 1,
        "due_date": "2026-03-15", "notes": None, "tags": None,
        "entities": None, "created_at": "2026-03-13", "updated_at": "2026-03-13"
    }
    
    line = format_task_line(task, number=1)
    test("format_task_line includes number", "#1" in line)
    test("format_task_line includes priority", "P1" in line)
    test("format_task_line includes title", "Test task" in line)
    test("format_task_line includes due date", "2026-03-15" in line)
    
    table = format_task_table([task])
    test("format_task_table groups by role", "Work" in table)
    
    empty = format_task_table([])
    test("format_task_table handles empty", "No tasks" in empty)


def test_status_history():
    print("\n--- Status History ---")
    db_path, conn = setup_test_db()
    
    tid = add_task(conn, "Track me", 1, status="backlog")
    
    # Check initial status logged
    row = conn.execute(
        "SELECT * FROM status_history WHERE task_id = ?", (tid,)
    ).fetchone()
    test("initial status logged", row is not None and row["new_status"] == "backlog")
    
    # Update status — trigger should fire
    update_task(conn, tid, status="to-do")
    rows = conn.execute(
        "SELECT * FROM status_history WHERE task_id = ? ORDER BY id", (tid,)
    ).fetchall()
    test("status transition logged", len(rows) == 2)
    test("transition records old→new", rows[1]["old_status"] == "backlog" and rows[1]["new_status"] == "to-do")
    
    conn.close()
    os.unlink(db_path)


if __name__ == "__main__":
    test_roles()
    test_crud()
    test_list_filters()
    test_entity_enrichment()
    test_formatting()
    test_status_history()
    
    print(f"\n{'='*40}")
    print(f"Results: {passed} passed, {failed} failed")
    if failed:
        sys.exit(1)
