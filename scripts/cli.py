#!/usr/bin/env python3
"""
CLI for the completion skill.

Usage:
    python3 cli.py add "Task title" --role Work --priority p1 --status to-do
    python3 cli.py list [--role Work] [--status in_progress] [--entity oneeleven]
    python3 cli.py update <id> --status done --notes "Shipped it"
    python3 cli.py done <id>
    python3 cli.py show <id>
    python3 cli.py roles
    python3 cli.py standup [--format detailed|summary]
    python3 cli.py review
"""

import argparse
import json
import sys
import os

# Ensure scripts/ is on path for local imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from db import (
    open_db, get_roles, find_role_by_name, get_tasks, get_task_by_id,
    add_task, update_task, load_entity_registry, build_entity_index,
    enrich_entities, infer_role_from_entities, format_task_table,
    format_task_line, STATUS_ORDER,
)
from datetime import datetime, timedelta


def cmd_add(args):
    """Add a new task."""
    conn = open_db(args.db)

    # Resolve role
    role = find_role_by_name(conn, args.role)
    if not role:
        roles = get_roles(conn)
        print(f"Unknown role '{args.role}'. Available: {', '.join(r['name'] for r in roles.values())}")
        sys.exit(1)

    # Entity enrichment
    registry = load_entity_registry()
    entity_index = build_entity_index(registry)
    text = args.title + (" " + args.notes if args.notes else "")
    entities = enrich_entities(text, entity_index)

    # If no role explicitly set and entities suggest one, use it
    # (only if user passed the default)
    if args.role == "Work" and entities:
        inferred = infer_role_from_entities(conn, entities, registry)
        if inferred and inferred != role["id"]:
            inferred_role = get_roles(conn).get(inferred)
            if inferred_role:
                role = {"id": inferred, "name": inferred_role["name"]}

    task_id = add_task(
        conn,
        args.title,
        role["id"],
        status=args.status,
        priority=args.priority,
        due_date=args.due,
        notes=args.notes,
        tags=args.tags,
        entities=entities if entities else None,
    )

    entity_note = f" (entities: {', '.join(entities)})" if entities else ""
    print(f"Added #{task_id}: [{args.priority.upper()}] {args.title}")
    print(f"  Role: {role['name']} | Status: {args.status}{entity_note}")
    conn.close()


def cmd_list(args):
    """List tasks with filters."""
    conn = open_db(args.db)
    
    statuses = None
    if args.status:
        statuses = None  # single status handled by status param
    elif args.all:
        pass  # no status filter
    
    tasks = get_tasks(
        conn,
        role=args.role,
        status=args.status,
        entity=args.entity,
        priority=args.priority,
        exclude_done=not args.all,
    )
    
    show_ent = getattr(args, 'entities', False) if hasattr(args, 'entities') else False
    print(format_task_table(tasks, show_entities=show_ent))
    print(f"\n({len(tasks)} tasks)")
    conn.close()


def cmd_update(args):
    """Update a task by ID."""
    conn = open_db(args.db)
    
    task = get_task_by_id(conn, args.id)
    if not task:
        print(f"Task #{args.id} not found.")
        sys.exit(1)

    fields = {}
    if args.status:
        fields["status"] = args.status
    if args.priority:
        fields["priority"] = args.priority
    if args.notes:
        fields["notes"] = args.notes
    if args.title:
        fields["title"] = args.title
    if args.due:
        fields["due_date"] = args.due
    if args.tags:
        fields["tags"] = args.tags
    if args.role:
        role = find_role_by_name(conn, args.role)
        if role:
            fields["role_id"] = role["id"]
        else:
            print(f"Unknown role '{args.role}'.")
            sys.exit(1)

    if not fields:
        print("Nothing to update. Use --status, --priority, --notes, --title, --due, --tags, or --role.")
        sys.exit(1)

    success = update_task(conn, args.id, **fields)
    if success:
        updated = get_task_by_id(conn, args.id)
        print(f"Updated #{args.id}: {format_task_line(updated)}")
        if "status" in fields:
            print(f"  {task['status']} → {fields['status']}")
    else:
        print(f"Failed to update #{args.id}.")
    
    conn.close()


def cmd_done(args):
    """Mark a task as done."""
    conn = open_db(args.db)
    
    task = get_task_by_id(conn, args.id)
    if not task:
        print(f"Task #{args.id} not found.")
        sys.exit(1)

    update_task(conn, args.id, status="done")
    print(f"✅ Done: {task['title']}")
    conn.close()


def cmd_show(args):
    """Show detailed info for a single task."""
    conn = open_db(args.db)
    
    task = get_task_by_id(conn, args.id)
    if not task:
        print(f"Task #{args.id} not found.")
        sys.exit(1)

    print(f"Task #{task['id']}")
    print(f"  Title:    {task['title']}")
    print(f"  Status:   {task['status']}")
    print(f"  Priority: {task['priority'].upper()}")
    print(f"  Role:     {task['role_name']}")
    if task["due_date"]:
        print(f"  Due:      {task['due_date']}")
    if task["notes"]:
        print(f"  Notes:    {task['notes']}")
    if task["tags"]:
        print(f"  Tags:     {task['tags']}")
    if task["entities"]:
        print(f"  Entities: {task['entities']}")
    print(f"  Created:  {task['created_at']}")
    print(f"  Updated:  {task['updated_at']}")
    conn.close()


def cmd_roles(args):
    """List all roles with weights."""
    conn = open_db(args.db)
    roles = get_roles(conn)
    
    total_weight = sum(r["weight"] for r in roles.values())
    print("Roles:")
    for rid, role in roles.items():
        pct = (role["weight"] / total_weight * 100) if total_weight else 0
        desc = f" — {role['description']}" if role["description"] else ""
        print(f"  [{rid}] {role['name']} (weight: {role['weight']}, {pct:.0f}%){desc}")
    conn.close()


def cmd_standup(args):
    """
    Generate standup output.
    
    Formats:
      detailed — Yesterday/Today/Blockers/Questions (for direct reports/team)
      summary  — High-level themes and key asks (for peers/manager)
    """
    conn = open_db(args.db)
    fmt = args.format or "detailed"
    
    yesterday = (datetime.now() - timedelta(days=1)).isoformat()
    
    # Tasks completed recently (yesterday/today)
    done_recent = get_tasks(
        conn, status="done", exclude_done=False,
        order_by="t.updated_at DESC"
    )
    done_recent = [t for t in done_recent if t["updated_at"] >= yesterday]
    
    # Currently in progress
    in_progress = get_tasks(conn, statuses=["in_progress"])
    
    # To-do (on deck)
    on_deck = get_tasks(conn, statuses=["to-do"],
                        order_by="priority ASC, due_date ASC NULLS LAST")
    
    # Blocked
    blocked = get_tasks(conn, status="blocked")
    
    if fmt == "detailed":
        _standup_detailed(done_recent, in_progress, on_deck, blocked)
    else:
        _standup_summary(done_recent, in_progress, on_deck, blocked)
    
    conn.close()


def _standup_detailed(done, in_progress, on_deck, blocked):
    """Full standup: Yesterday / Today / Blockers / Questions."""
    print("## Standup (Detailed)\n")
    
    print("**Yesterday / Recently Completed:**")
    if done:
        for t in done:
            print(f"  ✅ {t['title']} ({t['role_name']})")
    else:
        print("  (nothing closed recently)")
    
    print("\n**Today / In Progress:**")
    if in_progress:
        for t in in_progress:
            pri = t["priority"].upper()
            print(f"  🔵 [{pri}] {t['title']} ({t['role_name']})")
    else:
        print("  (nothing in progress)")
    
    if on_deck:
        print("\n**On Deck (to-do):**")
        for t in on_deck[:5]:  # Show top 5
            pri = t["priority"].upper()
            print(f"  ⚪ [{pri}] {t['title']} ({t['role_name']})")
        if len(on_deck) > 5:
            print(f"  ... and {len(on_deck) - 5} more")
    
    print("\n**Blockers:**")
    if blocked:
        for t in blocked:
            blocker = t["notes"][:80] if t["notes"] else "no details"
            print(f"  🔴 {t['title']} — {blocker}")
    else:
        print("  (none)")
    
    print()


def _standup_summary(done, in_progress, on_deck, blocked):
    """Summary standup: themes and key asks (for peers/manager)."""
    print("## Standup (Summary)\n")
    
    # Group in-progress by role for theme extraction
    by_role = {}
    for t in in_progress:
        role = t["role_name"]
        if role not in by_role:
            by_role[role] = []
        by_role[role].append(t)
    
    completed_count = len(done)
    active_count = len(in_progress)
    blocked_count = len(blocked)
    
    if completed_count:
        titles = ", ".join(t["title"] for t in done[:3])
        more = f" (+{completed_count - 3} more)" if completed_count > 3 else ""
        print(f"**Completed:** {titles}{more}")
    
    print(f"\n**Focus today:** {active_count} active items")
    for role, tasks in by_role.items():
        titles = ", ".join(t["title"] for t in tasks[:2])
        more = f" +{len(tasks) - 2}" if len(tasks) > 2 else ""
        print(f"  • {role}: {titles}{more}")
    
    if blocked:
        print(f"\n**Blocked ({blocked_count}):**")
        for t in blocked:
            print(f"  • {t['title']}")
    
    # Key asks
    p1_tasks = [t for t in in_progress + on_deck if t["priority"] == "p1"]
    if p1_tasks:
        print(f"\n**Key priorities:** {', '.join(t['title'] for t in p1_tasks[:3])}")
    
    print()


def cmd_review(args):
    """Weekly review: what moved, what's stuck, what needs attention."""
    conn = open_db(args.db)
    
    week_ago = (datetime.now() - timedelta(days=7)).isoformat()
    
    # Completed this week
    done_this_week = get_tasks(
        conn, status="done", exclude_done=False,
        order_by="t.role_id, t.updated_at DESC"
    )
    done_this_week = [t for t in done_this_week if t["updated_at"] >= week_ago]
    
    # Stuck items
    stuck = get_tasks(conn, statuses=["in_progress", "blocked"])
    three_days_ago = (datetime.now() - timedelta(days=3)).isoformat()
    stuck = [t for t in stuck if t["updated_at"] < three_days_ago]
    
    # Roles with no active work
    roles = get_roles(conn)
    active_by_role = {}
    all_active = get_tasks(conn, statuses=["in_progress", "to-do"])
    for t in all_active:
        active_by_role[t["role_id"]] = active_by_role.get(t["role_id"], 0) + 1
    neglected = [r for rid, r in roles.items() if rid not in active_by_role]
    
    # Upcoming due dates
    two_weeks = (datetime.now() + timedelta(days=14)).strftime("%Y-%m-%d")
    upcoming = get_tasks(conn, exclude_done=True)
    upcoming = [t for t in upcoming if t.get("due_date") and t["due_date"] <= two_weeks]
    
    print("## Weekly Review\n")
    
    print(f"**What moved** ({len(done_this_week)} completed):")
    if done_this_week:
        for t in done_this_week:
            print(f"  ✅ {t['title']} ({t['role_name']})")
    else:
        print("  (nothing completed this week)")
    
    print(f"\n**What's stuck** ({len(stuck)} items):")
    if stuck:
        for t in stuck:
            days = (datetime.now() - datetime.fromisoformat(t["updated_at"])).days
            status_note = f"blocked" if t["status"] == "blocked" else "stalled"
            print(f"  🔴 {t['title']} — {status_note} for {days}d ({t['role_name']})")
    else:
        print("  (nothing stuck — nice)")
    
    if neglected:
        print(f"\n**Neglected roles:**")
        for r in neglected:
            print(f"  ⚠️  {r['name']} — no active tasks")
    
    if upcoming:
        print(f"\n**Upcoming deadlines:**")
        for t in upcoming:
            print(f"  📅 {t['due_date']}: {t['title']}")
    
    print()
    conn.close()


def main():
    parser = argparse.ArgumentParser(description="Completion skill CLI")
    parser.add_argument("--db", default=None, help="Database path override")
    
    sub = parser.add_subparsers(dest="command")
    
    # add
    p_add = sub.add_parser("add", help="Add a task")
    p_add.add_argument("title", help="Task title")
    p_add.add_argument("--role", default="Work", help="Role name (default: Work)")
    p_add.add_argument("--status", default="backlog", 
                       choices=["backlog", "to-do", "in_progress", "blocked", "done"])
    p_add.add_argument("--priority", default="p2", choices=["p1", "p2", "p3"])
    p_add.add_argument("--due", default=None, help="Due date (YYYY-MM-DD)")
    p_add.add_argument("--notes", default=None, help="Notes / context")
    p_add.add_argument("--tags", default=None, help="Comma-separated tags")
    
    # list
    p_list = sub.add_parser("list", help="List tasks")
    p_list.add_argument("--role", default=None, help="Filter by role")
    p_list.add_argument("--status", default=None, help="Filter by status")
    p_list.add_argument("--priority", default=None, help="Filter by priority")
    p_list.add_argument("--entity", default=None, help="Filter by entity ID")
    p_list.add_argument("--all", action="store_true", help="Include done tasks")
    p_list.add_argument("--entities", action="store_true", help="Show entity details")
    
    # update
    p_update = sub.add_parser("update", help="Update a task")
    p_update.add_argument("id", type=int, help="Task ID")
    p_update.add_argument("--status", default=None,
                          choices=["backlog", "to-do", "in_progress", "blocked", "done"])
    p_update.add_argument("--priority", default=None, choices=["p1", "p2", "p3"])
    p_update.add_argument("--notes", default=None)
    p_update.add_argument("--title", default=None)
    p_update.add_argument("--due", default=None)
    p_update.add_argument("--tags", default=None)
    p_update.add_argument("--role", default=None)
    
    # done
    p_done = sub.add_parser("done", help="Mark task as done")
    p_done.add_argument("id", type=int, help="Task ID")
    
    # show
    p_show = sub.add_parser("show", help="Show task details")
    p_show.add_argument("id", type=int, help="Task ID")
    
    # roles
    sub.add_parser("roles", help="List roles")
    
    # standup
    p_standup = sub.add_parser("standup", help="Generate standup")
    p_standup.add_argument("--format", choices=["detailed", "summary"], default="detailed",
                           help="Standup format")
    
    # review
    sub.add_parser("review", help="Weekly review")
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        sys.exit(1)
    
    commands = {
        "add": cmd_add,
        "list": cmd_list,
        "update": cmd_update,
        "done": cmd_done,
        "show": cmd_show,
        "roles": cmd_roles,
        "standup": cmd_standup,
        "review": cmd_review,
    }
    
    commands[args.command](args)


if __name__ == "__main__":
    main()
