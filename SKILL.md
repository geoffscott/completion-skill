---
name: completion
description: >
  Manage tasks across multiple roles and contexts using a local SQLite database as the single
  source of truth. Use this skill whenever the user wants to add, update, list, prioritize,
  or review tasks — including when they say things like "remind me to," "I need to," "add to
  my list," "what should I be working on," "what's on my plate," "standup," "daily review,"
  "what's stuck," "what carried over," or any reference to tracking work, commitments, or
  priorities across their roles. Also trigger when the user mentions specific role names
  (e.g., "work tasks," "personal stuff," or custom role names) in the context of work to be done.
  This skill owns the task data model — do not use external task managers.
---

# completion

A task completion skill for OpenCLAW-based AI agents that maintains a single, consolidated
view of commitments across multiple roles, with role-weighted priorities and conflict surfacing.

## Core Concepts

### Roles

Most people split their attention across at least two domains — work and personal life —
and many have more (side projects, volunteer commitments, caregiving). Each role represents
a domain of responsibility with a default attention weight. Weights are relative — they
express how the user wants to allocate focus across roles when tasks compete for the same
time window.

The skill ships with two default roles (Work and Personal), but users can add, rename, or
remove roles at any time. When the skill is first initialized, prompt the user to confirm
or adjust the default roles. Common additions might include a side project, a volunteer
board, or a freelance practice.

### Tasks

Every task belongs to exactly one role. Tasks have:

- **title**: short description of what needs to happen
- **status**: one of `backlog`, `todo`, `in_progress`, `blocked`, `done`
- **priority**: `p1` (must do), `p2` (should do), `p3` (nice to do) — within a role
- **role**: which role this task belongs to
- **due_date**: optional hard deadline (ISO 8601 date)
- **notes**: optional longer context, blockers, or next steps
- **created_at**, **updated_at**: timestamps managed automatically
- **tags**: optional comma-separated labels for cross-cutting concerns

Status meanings:
- `backlog` — captured and triaged, but not committed to yet. The "I know about this but not now" bucket.
- `todo` — committed to doing soon. The short list of what's actually on deck.
- `in_progress` — actively working on this right now.
- `blocked` — waiting on someone or something external. Record who/what in notes.
- `done` — completed or intentionally abandoned. If dropped rather than finished, note why.

### Priority Across Roles

A `p1` in one role is not automatically more important than a `p1` in another. When tasks
at the same priority level compete, role weights inform which one surfaces first. But the
skill should never silently resolve these conflicts — instead, surface them to the user:

> "You have two P1s competing for attention: [Work task] and [Personal task].
> Work currently has higher weight. Want to keep that ordering, or does the personal
> task need to win this week?"

This is the most important design principle: **surface conflicts, don't resolve them.**

## Database

The skill uses a SQLite database stored at `~/.openclaw/completion/tasks.db`. Initialize it on first use
by running `scripts/init_db.py`. The script is idempotent — safe to run if the database
already exists.

Before any task operation, check if the database exists:

```bash
python3 /path/to/skill/scripts/init_db.py
```

Then interact with it using SQL via Python or the `sqlite3` CLI. The schema is documented
in `references/schema.sql`.

## Workflows

### Adding Tasks

When the user says something like "I need to..." or "remind me to..." or "add a task for...":

1. Extract the task title from what they said
2. Infer the role from context (ask if ambiguous)
3. Default status to `backlog` unless context suggests otherwise (e.g., "I need to do this today" → `todo`)
4. Default priority to `p2` unless the user signals urgency
5. Confirm back to the user what was captured, including the inferred role and priority
6. Insert into the database

If the user rattles off several tasks at once, capture all of them and confirm the batch.

### Listing and Filtering Tasks

When the user asks "what's on my plate" or "show me my tasks" or similar:

1. Query the database, excluding `done` by default
2. Group by role
3. Within each role, sort by priority (p1 first), then by due date (soonest first)
4. Display with role weights so the user can see relative importance
5. Call out any conflicts: same-priority tasks across roles that compete

If the user asks for a specific role, filter to that role. If they ask for a specific
status (e.g., "what am I waiting on"), filter to that status.

Keep the output concise. Don't dump a wall of text — if there are many tasks, summarize
by role and offer to drill into any specific role.

### Updating Tasks

When the user says "mark X as done" or "move X to blocked" or "change priority of X":

1. Find the task (by title match, partial match, or ID if they specify one)
2. If ambiguous (multiple matches), show the options and ask which one
3. Update the record
4. Confirm the change

For status transitions, be conversational:
- Moving to `done`: "Nice — marked [task] as done."
- Moving to `blocked`: "Got it. Who or what are you waiting on?" (capture in notes)
- Moving to `backlog`: "Moved [task] back to the backlog."
- Moving to `todo`: "On deck — [task] is in your to-do list."

### Daily Standup

When the user says "standup" or "what should I work on today" or "morning review":

Have a short conversation, not a data dump. Walk through this sequence:

1. **Carryover**: "Yesterday you had [X, Y] in progress. Did those close out, or are they carrying over?"
2. **Newly urgent**: Surface any tasks with approaching due dates or that recently moved to p1.
3. **Conflicts**: If p1 tasks across roles compete, surface the conflict.
4. **The one thing**: "Across everything, what's the one thing that moves the needle most today?"

The standup should feel like a 2-minute conversation with a thoughtful colleague, not a
report printout. Be brief. Ask questions. Let the user drive.

### Weekly Review

When the user says "weekly review" or "how did my week go" or "what's stuck":

Walk through three questions:

1. **What moved?** — Tasks that transitioned to `done` this week, grouped by role.
2. **What's stuck?** — Tasks that have been `in_progress` or `blocked` for a long time, or tasks
   that were expected this week but didn't get done. Call out any task that's been `blocked`
   for more than a week.
3. **What needs attention?** — Backlog items that might deserve promotion to `todo`, roles that
   have no active tasks (might be getting neglected), upcoming due dates in the next two weeks.

Again: conversational, not a report. Surface observations, ask questions, let the user decide
what to do about each finding.

## Interaction Style

- Be concise. Task management is a utility, not a ceremony.
- Default to action. If the user says "I need to update the deployment docs," just capture
  it — don't ask five clarifying questions first. Infer sensible defaults and confirm.
- Surface conflicts but don't nag. Mention competing priorities once, clearly, then move on.
- Remember that the user has context you don't. If something seems off (a p3 with a deadline
  tomorrow), ask about it briefly rather than assuming it's wrong.
- When listing tasks, keep it scannable. Use brief formatting, not paragraphs.

## Error Handling

- If the database doesn't exist, initialize it silently and proceed.
- If a task query finds no matches, say so plainly: "I don't see a task matching [X]. Want
  to add it?"
