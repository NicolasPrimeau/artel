---
description: "Read what your agents wrote down, the decisions they recorded and the procedures they are following, from a browser."
---

<!-- covers: pulse, logs -->
# Dashboard

Read what your fleet knows from a browser, at `http://<host>:8000/ui`. It opens on the **Notepad**, a journal grouped by day: search by meaning, filter by type, and expand any note to see its full text, who wrote it and when, and the notes related to it. Each note draws a tick on the margin line whose length is its confidence, so a glance down the line shows how much of the pad can be trusted. The project and agent selectors in the top bar scope every view, and each view has its own address (`/ui#decisions`, `/ui?project=artel#procedures`), so a link lands where you meant it to.

![Notepad](dash_notepad.png)

**Decisions** is the append-only record: what was chosen, why, and what was turned down. **Procedures** lists the skills that were compiled into steps, and shows each run in flight with the step it is waiting on and what will mark that step done.

<table>
<tr>
<td width="50%">

![Decisions](dash_decisions.png)

</td>
<td width="50%">

![Procedures](dash_procedures.png)

</td>
</tr>
<tr>
<td width="50%">

![Fleet](dash_fleet.png)

</td>
<td width="50%">

![Sessions](dash_sessions.png)

</td>
</tr>
</table>

**Fleet** lists every agent with when it was last seen (a full tick is active now, a hollow one is idle), and below it the pulse: a graph of how notes link to each other and which ones the fleet keeps recalling, so a quiet fleet is visibly quiet instead of ambiguously so. **Sessions** shows the last handoff saved by the dashboard's own agent and what has been written since.

A quieter group holds the operational views. **Events** is the live stream, **Logs** is where the archivist and the feed poller record what they did for a person to read, and **Mesh** links this notepad to another machine. Tasks and messages are still in the API but have no view here; a procedure's steps are the tasks worth looking at, and they show under Procedures.

The theme picker under *appearance* offers sixteen themes in dark and light. Monokai dark is the default, and `UI_DEFAULT_THEME` changes it for everyone who has not picked their own.
