---
description: Show open Artel tasks, or claim and start the next one
argument-hint: [claim]
---

List open, unblocked Artel tasks for the current project using `mcp__artel__task_list` with `status="open"` and `unblocked=true`.

Show them concisely (id, priority, title). If the argument is `claim`, claim the highest-priority unblocked task with `mcp__artel__task_claim` and begin work on it. Otherwise just report the list and stop.
