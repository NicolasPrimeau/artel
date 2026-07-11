import type { Plugin } from "@opencode-ai/plugin"

// Artel opencode plugin — the push layer that mirrors the Claude Code plugin:
// on session start it surfaces the last handoff + unread inbox, when the session
// goes idle it re-surfaces unread messages, and before an edit it surfaces memory
// anchored to the file. Config comes from env: ARTEL_URL, ARTEL_AGENT_ID,
// ARTEL_API_KEY. All calls are read-only, timeout-bounded, and swallow errors so a
// missing or down Artel server is harmless.

const EDIT_TOOLS = new Set(["edit", "write", "patch", "multiedit"])

const baseUrl = () => (process.env.ARTEL_URL || "").replace(/\/+$/, "")
const headers = () => ({
  "x-agent-id": process.env.ARTEL_AGENT_ID || "",
  "x-api-key": process.env.ARTEL_API_KEY || "",
})
const configured = () =>
  Boolean(process.env.ARTEL_URL && process.env.ARTEL_AGENT_ID && process.env.ARTEL_API_KEY)

async function artelGet(path: string): Promise<unknown | null> {
  try {
    const res = await fetch(baseUrl() + path, {
      headers: headers(),
      signal: AbortSignal.timeout(6000),
    })
    if (!res.ok) return null
    return await res.json()
  } catch {
    return null
  }
}

const clip = (s: unknown, n: number) => String(s ?? "").replace(/\s+/g, " ").slice(0, n)

export const ArtelPlugin: Plugin = async ({ client }) => {
  const surface = async (message: string) => {
    try {
      await client.app.log({ body: { service: "artel", level: "info", message } })
    } catch {
      /* logging is best-effort */
    }
  }

  if (!configured()) {
    await surface("Artel plugin loaded but ARTEL_URL/ARTEL_AGENT_ID/ARTEL_API_KEY are unset; inactive.")
    return {}
  }

  const inboxLine = (msgs: unknown): string | null => {
    if (!Array.isArray(msgs) || msgs.length === 0) return null
    const lines = msgs.slice(0, 10).map((m: any) => `${m.from_agent ?? "?"}: ${m.body ?? ""}`)
    return `[Artel] ${msgs.length} unread message(s): ${lines.join(" | ")}`
  }

  return {
    "session.created": async () => {
      const handoff: any = await artelGet(`/sessions/handoff/${process.env.ARTEL_AGENT_ID}`)
      if (handoff?.last_handoff?.summary) {
        await surface(`[Artel] Last session: ${clip(handoff.last_handoff.summary, 300)}`)
      }
      const line = inboxLine(await artelGet("/messages/inbox"))
      if (line) await surface(line)
    },

    "session.idle": async () => {
      const line = inboxLine(await artelGet("/messages/inbox"))
      if (line) await surface(line)
    },

    "tool.execute.before": async (input: any, output: any) => {
      if (!EDIT_TOOLS.has(String(input?.tool || "").toLowerCase())) return
      const path = output?.args?.file_path || output?.args?.path || ""
      const name = String(path).split("/").pop() || ""
      if (!name) return
      const stem = name.replace(/\.[^.]+$/, "")
      const q = encodeURIComponent(`${name} ${stem}`.trim())
      const results = await artelGet(
        `/memory/search?q=${q}&limit=4&confidence_min=0.5&max_content_length=300`,
      )
      if (!Array.isArray(results)) return
      const hits = results.filter((e: any) => {
        const c = String(e?.content || "").toLowerCase()
        return c.includes(name.toLowerCase()) || (stem.length >= 4 && c.includes(stem.toLowerCase()))
      })
      if (hits.length === 0) return
      await surface(`[Artel] Notes on ${name}: ${hits.slice(0, 2).map((e: any) => clip(e.content, 180)).join(" | ")}`)
    },
  }
}
