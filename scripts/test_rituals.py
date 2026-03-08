#!/usr/bin/env python3
"""
Acceptance test suite for completion-skill rituals.

Creates test scenarios, runs each ritual, and validates outputs.
"""

import sqlite3
import os
import sys
import tempfile
import shutil
from datetime import datetime, timedelta
import traceback

# Add scripts directory to path
sys.path.insert(0, os.path.dirname(__file__))

import rituals


class RitualTestSuite:
    """Test framework for ritual functions."""
    
    def __init__(self):
        """Initialize test database and environment."""
        self.test_dir = tempfile.mkdtemp(prefix="completion_test_")
        self.test_db = os.path.join(self.test_dir, "tasks.db")
        self.passed = 0
        self.failed = 0
        self.tests = []
        
    def setup_db(self):
        """Initialize test database with schema."""
        schema_path = os.path.join(
            os.path.dirname(__file__), 
            "..", 
            "references", 
            "schema.sql"
        )
        
        conn = sqlite3.connect(self.test_db)
        with open(schema_path, "r") as f:
            conn.executescript(f.read())
        
        # Insert default roles
        conn.execute("INSERT INTO roles (name, weight, description) VALUES (?, ?, ?)",
                    ("Work", 1.5, "Work tasks"))
        conn.execute("INSERT INTO roles (name, weight, description) VALUES (?, ?, ?)",
                    ("Personal", 1.0, "Personal tasks"))
        conn.commit()
        conn.close()
    
    def add_task(self, title, status, priority, role="Work", notes="", due_date=None):
        """Add a task to test database."""
        conn = sqlite3.connect(self.test_db)
        c = conn.cursor()
        
        c.execute("SELECT id FROM roles WHERE name = ?", (role,))
        role_row = c.fetchone()
        if not role_row:
            conn.close()
            print(f"      ⚠️ Role '{role}' not found in test DB")
            return
        role_id = role_row[0]
        
        c.execute("""
            INSERT INTO tasks (title, status, priority, role_id, notes, due_date)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (title, status, priority, role_id, notes, due_date))
        
        conn.commit()
        
        # Verify insert
        c.execute("SELECT COUNT(*) FROM tasks WHERE title = ?", (title,))
        count = c.fetchone()[0]
        conn.close()
        
        if count > 0:
            print(f"      ✓ Added task: {title} [{status}] [{priority}]")
    
    def test(self, name, ritual_func, expected_output_type, verbose=False):
        """Run a test and record result."""
        try:
            # Call ritual with test DB
            result = ritual_func(self.test_db)
            
            if verbose:
                print(f"      Raw output: {result}")
            
            # Validate output
            if expected_output_type == "none":
                success = result is None
                msg = "No alert" if success else f"Expected None, got {result}"
            elif expected_output_type == "alert":
                success = result is not None and isinstance(result, tuple) and len(result) == 2
                msg = "Alert with (text, action)" if success else f"Expected alert tuple, got {type(result).__name__}: {result}"
            else:
                success = False
                msg = "Unknown expectation"
            
            if success:
                self.passed += 1
                status = "✅ PASS"
            else:
                self.failed += 1
                status = "❌ FAIL"
            
            self.tests.append((name, status, msg, result))
            print(f"{status} — {name}")
            print(f"      {msg}")
            if result and isinstance(result, tuple):
                print(f"      Alert: {result[0][:70]}")
            print()
            
        except Exception as e:
            self.failed += 1
            self.tests.append((name, "❌ ERROR", str(e), None))
            print(f"❌ ERROR — {name}")
            print(f"      {type(e).__name__}: {e}")
            if verbose:
                traceback.print_exc()
            print()
    
    def run_all(self):
        """Execute all test scenarios."""
        print("\n" + "="*70)
        print("COMPLETION-SKILL RITUAL ACCEPTANCE TESTS")
        print("="*70 + "\n")
        
        self.setup_db()
        
        # Scenario 1: WIP overflow
        print("Scenario 1: WIP Overflow Detection\n")
        self.add_task("Task 1", "todo", "p2", "Work")
        self.add_task("Task 2", "in_progress", "p2", "Work")
        self.add_task("Task 3", "in_progress", "p2", "Work")
        self.add_task("Task 4", "in_progress", "p2", "Work")
        
        self.test(
            "Morning Nudge detects WIP overflow (4 tasks, limit 2)",
            rituals.morning_nudge,
            "alert",
            verbose=True
        )
        
        # Clear for next scenario
        conn = sqlite3.connect(self.test_db)
        conn.execute("DELETE FROM tasks")
        conn.commit()
        conn.close()
        
        # Scenario 2: P1 conflict across roles
        print("Scenario 2: P1 Conflict Detection\n")
        self.add_task("Work P1", "todo", "p1", "Work")
        self.add_task("Personal P1", "todo", "p1", "Personal")
        
        self.test(
            "Morning Nudge detects P1 conflict across roles",
            rituals.morning_nudge,
            "alert"
        )
        
        conn = sqlite3.connect(self.test_db)
        conn.execute("DELETE FROM tasks")
        conn.commit()
        conn.close()
        
        # Scenario 3: Blocked task detection
        print("Scenario 3: Blocked Task Detection\n")
        now = datetime.now()
        old_date = (now - timedelta(days=6)).isoformat()
        
        conn = sqlite3.connect(self.test_db)
        c = conn.cursor()
        c.execute("SELECT id FROM roles WHERE name = 'Work'")
        role_id = c.fetchone()[0]
        
        c.execute("""
            INSERT INTO tasks (title, status, priority, role_id, notes, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, ("Waiting on API response", "blocked", "p2", role_id, "Blocked by API team", old_date))
        conn.commit()
        
        # Verify
        c.execute("SELECT COUNT(*) FROM tasks WHERE status='blocked'")
        print(f"      ✓ Added blocked task (count: {c.fetchone()[0]})")
        conn.close()
        
        self.test(
            "Stuck Alert detects blocked task > 5 days",
            rituals.stuck_alert,
            "alert",
            verbose=True
        )
        
        conn = sqlite3.connect(self.test_db)
        conn.execute("DELETE FROM tasks")
        conn.commit()
        conn.close()
        
        # Scenario 4: Role starvation
        print("Scenario 4: Role Starvation Detection\n")
        self.add_task("Work task", "todo", "p2", "Work")
        # Personal has no tasks
        
        # Verify
        conn = sqlite3.connect(self.test_db)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM tasks")
        print(f"      ✓ Total tasks: {c.fetchone()[0]}")
        conn.close()
        
        self.test(
            "Stuck Alert detects role with no active tasks",
            rituals.stuck_alert,
            "alert"
        )
        
        conn = sqlite3.connect(self.test_db)
        conn.execute("DELETE FROM tasks")
        conn.commit()
        conn.close()
        
        # Scenario 5: All clear
        print("Scenario 5: All Clear Scenarios\n")
        # Balanced to match weights: 3 Work (60%) + 2 Personal (40%)
        # But keep WIP under limit per role
        self.add_task("Work 1", "todo", "p2", "Work")
        self.add_task("Personal 1", "todo", "p2", "Personal")
        
        self.test(
            "Morning Nudge: all clear (normal WIP, no P1 conflicts)",
            rituals.morning_nudge,
            "none"
        )
        
        self.test(
            "Stuck Alert: all clear (no blocked/stalled tasks)",
            rituals.stuck_alert,
            "none"
        )
        
        self.test(
            "Role Rebalance detects weight variance (50/50 actual vs 60/40 intended)",
            rituals.role_rebalance,
            "alert"
        )
        
        # Print summary
        print("\n" + "="*70)
        print(f"RESULTS: {self.passed} passed, {self.failed} failed")
        print("="*70 + "\n")
        
        # Cleanup
        shutil.rmtree(self.test_dir)
        
        return self.failed == 0


if __name__ == "__main__":
    suite = RitualTestSuite()
    success = suite.run_all()
    exit(0 if success else 1)
