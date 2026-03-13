#!/usr/bin/env python3
"""
Autonomous rituals for the completion skill.

These functions are invoked by the agent's heartbeat scheduler and run
predictive health checks on task flow, surfacing insights and bottlenecks
without requiring user initiation.
"""

import json
import os
import sqlite3
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple


def load_config(skill_dir: str = None) -> Dict:
    """Load ritual configuration from metadata.json."""
    if skill_dir is None:
        skill_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    
    metadata_path = os.path.join(skill_dir, "metadata.json")
    
    # Default config
    default_config = {
        "rituals": {
            "morning_nudge": {
                "enabled": True,
                "time": "07:00",
                "wip_limit_per_role": 2,
                "only_on_conflict": False
            },
            "stuck_alert": {
                "enabled": True,
                "in_progress_threshold_days": 3,
                "blocked_threshold_days": 5,
                "check_frequency": "daily"
            },
            "weekly_kaizen": {
                "enabled": True,
                "day": "friday",
                "time": "17:00"
            },
            "role_rebalance": {
                "enabled": True,
                "after_review": True,
                "min_variance_percent": 10
            }
        }
    }
    
    # Load or merge with user config
    if os.path.exists(metadata_path):
        with open(metadata_path, "r") as f:
            user_config = json.load(f)
        # Merge: user config overrides defaults
        if "rituals" in user_config:
            default_config["rituals"].update(user_config["rituals"])
        return default_config
    
    return default_config


def open_db(db_path: str = None) -> sqlite3.Connection:
    """Open the completion skill database."""
    if db_path is None:
        db_path = os.path.expanduser("~/.openclaw/completion/tasks.db")
    
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def morning_nudge(db_path: str = None, config: Dict = None) -> Optional[Tuple[str, str]]:
    """
    Check WIP count, surface P1 conflicts, and confirm daily focus.
    
    Returns: (alert_text, suggested_action) or None if all clear
    """
    if config is None:
        config = load_config()
    
    if not config.get("rituals", {}).get("morning_nudge", {}).get("enabled"):
        return None
    
    conn = open_db(db_path)
    c = conn.cursor()
    
    # Get role info
    c.execute("SELECT id, name, weight FROM roles ORDER BY weight DESC")
    roles = {row['id']: {'name': row['name'], 'weight': row['weight']} for row in c.fetchall()}
    
    # Check WIP per role
    c.execute("""
        SELECT role_id, COUNT(*) as wip_count
        FROM tasks
        WHERE status IN ('to-do', 'in_progress')
        GROUP BY role_id
    """)
    
    wip_by_role = {row['role_id']: row['wip_count'] for row in c.fetchall()}
    wip_limit = config['rituals']['morning_nudge'].get('wip_limit_per_role', 2)
    
    alerts = []
    for role_id, wip_count in wip_by_role.items():
        if wip_count > wip_limit:
            role_name = roles[role_id]['name']
            alerts.append(f"{role_name}: {wip_count} tasks in flight (limit: {wip_limit})")
    
    # Check for P1 conflicts across roles
    c.execute("""
        SELECT role_id, COUNT(*) as p1_count
        FROM tasks
        WHERE priority = 'p1' AND status != 'done'
        GROUP BY role_id
    """)
    
    p1_by_role = {row['role_id']: row['p1_count'] for row in c.fetchall()}
    
    if sum(p1_by_role.values()) > 1:
        # Multiple P1s across roles
        p1_summary = ", ".join(
            f"{roles[rid]['name']} ({count})"
            for rid, count in p1_by_role.items()
            if count > 0
        )
        alerts.append(f"P1 conflict: {p1_summary}")
    
    conn.close()
    
    if alerts:
        alert_text = "Morning Nudge — Flow check:\n" + "\n".join(f"  • {a}" for a in alerts)
        suggested_action = "Review WIP and P1 priorities. What's the one thing moving the needle today?"
        return (alert_text, suggested_action)
    
    return None


def stuck_alert(db_path: str = None, config: Dict = None) -> Optional[Tuple[str, str]]:
    """
    Detect tasks stalled in progress or blocked for too long.
    
    Returns: (alert_text, suggested_action) or None if all clear
    """
    if config is None:
        config = load_config()
    
    if not config.get("rituals", {}).get("stuck_alert", {}).get("enabled"):
        return None
    
    conn = open_db(db_path)
    c = conn.cursor()
    
    in_progress_days = config['rituals']['stuck_alert'].get('in_progress_threshold_days', 3)
    blocked_days = config['rituals']['stuck_alert'].get('blocked_threshold_days', 5)
    
    cutoff_in_progress = datetime.now() - timedelta(days=in_progress_days)
    cutoff_blocked = datetime.now() - timedelta(days=blocked_days)
    
    alerts = []
    
    # Find stalled in-progress tasks
    c.execute("""
        SELECT id, title, updated_at
        FROM tasks
        WHERE status = 'in_progress' AND updated_at < ?
        ORDER BY updated_at ASC
    """, (cutoff_in_progress.isoformat(),))
    
    stalled = c.fetchall()
    if stalled:
        for task in stalled:
            days_stalled = (datetime.now() - datetime.fromisoformat(task['updated_at'])).days
            alerts.append(f"Stalled {days_stalled}d: {task['title']}")
    
    # Find long-blocked tasks
    c.execute("""
        SELECT id, title, notes, updated_at
        FROM tasks
        WHERE status = 'blocked' AND updated_at < ?
        ORDER BY updated_at ASC
    """, (cutoff_blocked.isoformat(),))
    
    blocked = c.fetchall()
    if blocked:
        for task in blocked:
            days_blocked = (datetime.now() - datetime.fromisoformat(task['updated_at'])).days
            blocker = task['notes'][:50] if task['notes'] else "unknown"
            alerts.append(f"Blocked {days_blocked}d on {blocker}: {task['title']}")
    
    # Check for role starvation (no active work)
    c.execute("""
        SELECT r.id, r.name
        FROM roles r
        WHERE NOT EXISTS (
            SELECT 1 FROM tasks WHERE role_id = r.id AND status IN ('to-do', 'in_progress')
        )
    """)
    
    starved = c.fetchall()
    if starved:
        for role in starved:
            alerts.append(f"Starved: {role['name']} has no active tasks")
    
    conn.close()
    
    if alerts:
        alert_text = "Stuck Alert — Flow blockage:\n" + "\n".join(f"  • {a}" for a in alerts)
        suggested_action = "Review blockers. Escalate, replan, or drop stuck items."
        return (alert_text, suggested_action)
    
    return None


def weekly_kaizen(db_path: str = None, config: Dict = None) -> Optional[Tuple[str, str]]:
    """
    Analyze weekly flow: completion rate, cycle time, bottlenecks, patterns.
    
    Returns: (analysis_text, improvement_question) or None if no data
    """
    if config is None:
        config = load_config()
    
    if not config.get("rituals", {}).get("weekly_kaizen", {}).get("enabled"):
        return None
    
    conn = open_db(db_path)
    c = conn.cursor()
    
    # Get this week's data (last 7 days)
    week_ago = (datetime.now() - timedelta(days=7)).isoformat()
    
    # Committed vs. completed
    c.execute("""
        SELECT COUNT(*) as count FROM tasks
        WHERE status = 'to-do' AND created_at > ?
    """, (week_ago,))
    committed = c.fetchone()['count']
    
    c.execute("""
        SELECT COUNT(*) as count FROM tasks
        WHERE status = 'done' AND updated_at > ?
    """, (week_ago,))
    completed = c.fetchone()['count']
    
    completion_rate = (completed / committed * 100) if committed > 0 else 0
    
    # Cycle time per priority
    c.execute("""
        SELECT priority, AVG(CAST((julianday(updated_at) - julianday(created_at)) AS FLOAT)) as avg_days
        FROM tasks
        WHERE status = 'done' AND updated_at > ?
        GROUP BY priority
    """, (week_ago,))
    
    cycle_times = {row['priority']: row['avg_days'] for row in c.fetchall()}
    
    # Blockers (tasks in blocked status)
    c.execute("""
        SELECT COUNT(*) as blocked_count FROM tasks
        WHERE status = 'blocked'
    """)
    blocked_count = c.fetchone()['blocked_count']
    
    conn.close()
    
    findings = [
        f"Completion rate: {completed}/{committed} ({completion_rate:.0f}%)",
        f"Blocked items: {blocked_count}",
    ]
    
    for priority, days in cycle_times.items():
        if days:
            findings.append(f"Cycle time ({priority}): {days:.1f} days")
    
    analysis = "Weekly Kaizen — Flow analysis:\n" + "\n".join(f"  • {f}" for f in findings)
    
    # Improvement question
    if completion_rate < 75:
        question = f"You completed {completion_rate:.0f}% of committed work. What would help get closer to 80%?"
    elif blocked_count > 2:
        question = f"You have {blocked_count} blocked items. What's the pattern? Can we remove the blocker or work around it?"
    else:
        question = "Flow looks good. Any patterns to improve further?"
    
    return (analysis, question)


def role_rebalance(db_path: str = None, config: Dict = None) -> Optional[Tuple[str, str]]:
    """
    Compare intended role weights to actual time allocation.
    
    Returns: (comparison_text, rebalance_question) or None if balanced
    """
    if config is None:
        config = load_config()
    
    if not config.get("rituals", {}).get("role_rebalance", {}).get("enabled"):
        return None
    
    conn = open_db(db_path)
    c = conn.cursor()
    
    # Get actual task distribution (this week)
    week_ago = (datetime.now() - timedelta(days=7)).isoformat()
    
    c.execute("""
        SELECT r.id, r.name, r.weight, COUNT(t.id) as task_count
        FROM roles r
        LEFT JOIN tasks t ON r.id = t.role_id AND t.updated_at > ?
        GROUP BY r.id
        ORDER BY r.weight DESC
    """, (week_ago,))
    
    rows = c.fetchall()
    
    # Calculate percentages
    total_tasks = sum(row['task_count'] for row in rows)
    
    if total_tasks == 0:
        conn.close()
        return None
    
    total_weight = sum(row['weight'] for row in rows)
    
    variance = []
    for row in rows:
        actual_pct = (row['task_count'] / total_tasks * 100)
        intended_pct = (row['weight'] / total_weight * 100)
        diff = abs(actual_pct - intended_pct)
        
        variance.append({
            'name': row['name'],
            'intended': intended_pct,
            'actual': actual_pct,
            'diff': diff
        })
    
    max_variance = max(v['diff'] for v in variance)
    min_variance_threshold = config['rituals']['role_rebalance'].get('min_variance_percent', 10)
    
    if max_variance < min_variance_threshold:
        conn.close()
        return None
    
    # Build comparison
    comparison = "Role Rebalance — Intended vs. Actual:\n"
    for v in variance:
        comparison += f"  • {v['name']}: {v['intended']:.0f}% intended, {v['actual']:.0f}% actual\n"
    
    question = "Weights seem misaligned. Want to adjust role priorities for next week?"
    
    conn.close()
    return (comparison, question)


if __name__ == "__main__":
    import sys
    
    ritual = sys.argv[1] if len(sys.argv) > 1 else "all"
    
    print("\n=== Completion Skill Rituals ===\n")
    
    if ritual in ("all", "morning"):
        result = morning_nudge()
        if result:
            print(f"{result[0]}\n→ {result[1]}\n")
        else:
            print("Morning Nudge: All clear ✅\n")
    
    if ritual in ("all", "stuck"):
        result = stuck_alert()
        if result:
            print(f"{result[0]}\n→ {result[1]}\n")
        else:
            print("Stuck Alert: No blockers ✅\n")
    
    if ritual in ("all", "kaizen"):
        result = weekly_kaizen()
        if result:
            print(f"{result[0]}\n→ {result[1]}\n")
        else:
            print("Weekly Kaizen: No data yet\n")
    
    if ritual in ("all", "rebalance"):
        result = role_rebalance()
        if result:
            print(f"{result[0]}\n→ {result[1]}\n")
        else:
            print("Role Rebalance: Balanced ✅\n")
