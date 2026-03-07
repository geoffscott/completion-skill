# todo

A task management skill for [OpenCLAW](https://openclaw.ai) agents. Maintains a single view of commitments across multiple roles with priority weighting and conflict surfacing.

## What it does

- **Task CRUD** across multiple roles (default: Work and Personal, add your own)
- **Role-weighted priorities** — when P1s compete across roles, the skill surfaces the conflict instead of silently resolving it
- **Daily standup** — a short conversation to build your day: carryover, urgency, one thing that matters
- **Weekly review** — what moved, what's stuck, what needs attention
- **Status tracking** — tasks flow through `backlog → todo → in_progress → blocked → done` with full history

## Install

Clone into your OpenCLAW workspace skills directory:

```bash
git clone https://github.com/geoffscott/todo-skill.git <workspace>/skills/todo
```

Restart the gateway or ask your agent to refresh skills. Verify with:

```bash
openclaw skills list --eligible
```

## Usage

Talk to your agent naturally:

- "I need to finish the API docs by Friday" → captures a task
- "What's on my plate?" → lists tasks grouped by role
- "Let's do a standup" → interactive daily review
- "Weekly review" → retrospective on flow and stuck items
- "The budget proposal is blocked on finance" → status update with context

## How it works

The skill stores tasks in a local SQLite database at `~/.openclaw/completion/tasks.db`. The database is initialized automatically on first use with default roles. You can add, rename, or reweight roles at any time.

Tasks belong to exactly one role and have a priority (p1/p2/p3). When tasks at the same priority compete across roles, the skill uses role weights to suggest an ordering — but always surfaces the conflict for you to decide.

## File structure

```
SKILL.md            # Skill instructions (read by OpenCLAW)
references/
  schema.sql        # SQLite schema
scripts/
  init_db.py        # Database initialization (idempotent)
```

## License

[MIT](LICENSE)
