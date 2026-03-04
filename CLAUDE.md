# CLAUDE.md

Instructions for AI agents working on this repository.

## What this is

`todo` is an OpenCLAW skill for task management across multiple roles. The skill teaches an agent how to maintain a SQLite-backed task list with role-weighted priorities, daily standups, and weekly reviews.

## Repository structure

```
SKILL.md              # The skill itself — OpenCLAW reads this at runtime
references/
  schema.sql          # SQLite schema (source of truth for DB structure)
scripts/
  init_db.py          # Idempotent DB initializer, seeds default roles
```

## Key design principles

- **Surface conflicts, don't resolve them.** When priorities compete across roles, present the conflict to the user and let them decide. Never silently pick a winner.
- **Conversations, not reports.** Standups and weekly reviews are short interactive dialogues, not data dumps.
- **Default to action.** When a user says "I need to do X," capture it immediately with sensible defaults. Don't ask five clarifying questions first.
- **Keep it simple.** This is a v0 skill focused on task CRUD, standup, and weekly review. Calendar integration, flow metrics, and scheduling are future work — don't add them.

## Working with SKILL.md

SKILL.md has two parts:
1. **YAML frontmatter** (between `---` markers) — contains `name` and `description`. The description is how OpenCLAW decides whether to invoke this skill, so it matters a lot. Keep it grounded in the phrases real people actually use.
2. **Markdown body** — instructions the agent follows when the skill is invoked. Write in imperative form. Explain *why* things matter, not just what to do.

When editing SKILL.md, keep the total length under 500 lines. If it's growing past that, consider moving reference material into `references/`.

## Working with the schema

`references/schema.sql` is the source of truth for the database structure. If you change the schema:
1. Update `schema.sql` first
2. Update `scripts/init_db.py` to match (including any new default data)
3. Update the relevant sections of `SKILL.md` that describe task fields or statuses
4. All three files must stay in sync

The valid statuses are: `backlog`, `todo`, `in_progress`, `blocked`, `done`.
The valid priorities are: `p1`, `p2`, `p3`.

## Testing changes

After making changes, verify by running the init script in a clean environment:

```bash
rm -rf ~/.todo
python3 scripts/init_db.py
```

Then test basic operations:

```python
import sqlite3
conn = sqlite3.connect('~/.todo/tasks.db')
c = conn.cursor()
# Insert a task, update its status, verify history triggers fire
```

## Pull request guidelines

- One concern per PR. Don't combine schema changes with SKILL.md prose edits.
- PR title should be a short imperative sentence: "Add tag filtering to task list" not "Updated SKILL.md".
- If changing the schema, the PR description must explain what changes and why.
- If changing SKILL.md instructions, include a before/after example of the agent behavior you're targeting.

## What not to do

- Don't add calendar integration — that's a separate skill for a future release.
- Don't add flow metrics or cycle time tracking to SKILL.md instructions — the schema tracks status history for future use, but the skill doesn't surface it yet.
- Don't add dependencies beyond Python standard library and SQLite.
- Don't change the database path (`~/.todo/tasks.db`) without updating all three files.
- Don't use the word "dropped" as a status — we deliberately consolidated it into `done`.
