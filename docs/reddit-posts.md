# Artel — Reddit launch post (r/ClaudeAI)

Copy-paste ready. Attach the demo GIF (`docs/reddit.gif`) as the post image.
Sandbox link: https://artel.run/ui (password: artel)

---

## r/ClaudeAI

**Title**

I built a self-hosted server so my Claude Code sessions stop starting from scratch

**Body**

I run Claude Code across a few machines and a lot of separate sessions, and every session starts from nothing. One session figures something out, the next has no idea it happened. I kept re-explaining the same context, and tasks slipped through the cracks.

So I built a self-hosted server to fix it. It has been running my own fleet for a while now and it works well, so I'm sharing it.

It gives a group of agents a few shared things:

- Shared memory with semantic search. One session writes down what it learned, any later session can find it by meaning.
- A task queue. Create work in one session, claim and finish it in another.
- Direct messages between agents.
- Session handoffs. A session saves a short summary before it ends, the next one loads it and picks up with full context.
- A web UI for browsing memory, tasks, and inboxes.

Claude Code connects with one line in .mcp.json. Anything that speaks HTTP can join, not just Claude Code.

Two parts go further than a plain shared database. A background archivist keeps the shared memory coherent on its own: it merges entries that conflict or overlap, synthesizes findings across sessions into higher-level notes, decays stale knowledge, and promotes the observations that prove stable over time. Agents just write what they know and the archivist does the curating. Servers can also mesh to form a self-organizing network: they replicate memory to each other as a CRDT, so it converges with no central coordinator.

Happy to answer questions, and curious whether others have approached this differently.

Sandbox to look around (password: artel): https://artel.run/ui
Repo: https://github.com/NicolasPrimeau/artel
