"""Ask-your-fleet: an admin asks a question, the fleet's own record answers.

The demo Langfuse cannot run: it has traces, not a record of what the work produced.

Split by construction — arithmetic is computed in facts.py and handed to the model
already rendered; the model writes prose over it and cites entry ids. See llm.SYSTEM.
"""

from __future__ import annotations

import os
import pathlib
import sqlite3

import facts
import llm
import retrieve
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

DB = os.environ.get("ASK_DB", "/tmp/artel-ro.db")
app = FastAPI(title="Ask your fleet", docs_url=None, redoc_url=None)


class Question(BaseModel):
    question: str
    days: int = 7


def _facts_block(days: int) -> tuple[str, list[dict]]:
    rows = facts.fleet_table(DB, days)
    head = (
        f"Window: last {days} days.\n"
        f"{'project':14}{'sessions':>9}{'turns':>8}{'out_tokens':>12}"
        f"{'commits':>9}{'tok/commit':>12}{'notes':>7}{'decisions':>10}"
    )
    lines = [head]
    for r in rows:
        tpc = f"{r['tokens_per_commit']:,}" if r["tokens_per_commit"] is not None else "n/a"
        lines.append(
            f"{r['project']:14}{r['sessions']:>9}{r['turns']:>8}{r['output_tokens']:>12,}"
            f"{r['commits']:>9}{tpc:>12}{r['notes']:>7}{r['decisions']:>10}"
        )
    return "\n".join(lines), rows


@app.get("/api/fleet")
async def fleet(days: int = 7):
    block, rows = _facts_block(days)
    return {"days": days, "rows": rows, "rendered": block}


@app.post("/api/ask")
async def ask(q: Question):
    block, rows = _facts_block(q.days)
    try:
        passages = await retrieve.search(q.question, limit=8)
    except Exception as e:
        passages = []
        block += f"\n\n(retrieval unavailable: {e})"
    result = await llm.answer(q.question, block, passages)
    return JSONResponse(
        {
            "question": q.question,
            "facts": block,
            "fleet": rows,
            "passages": passages,
            "answer": result,
        }
    )


@app.get("/health")
async def health():
    ok = pathlib.Path(DB).exists()
    try:
        sqlite3.connect(f"file:{DB}?mode=ro", uri=True).execute("SELECT 1")
    except Exception as e:
        return {"status": "degraded", "db": str(e)}
    return {"status": "ok", "db_readable": ok, "dry_run": llm.dry_run(), "model": llm.MODEL}


PAGE = """<!doctype html><meta charset=utf-8><title>Ask your fleet</title>
<style>
:root{--bg:#0f1115;--fg:#e6e6e6;--dim:#8b93a1;--acc:#7aa2f7;--card:#171a21;--warn:#e0af68}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.55 ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto}
.wrap{max-width:1020px;margin:0 auto;padding:32px 20px 80px}
h1{font-size:20px;margin:0 0 4px} .sub{color:var(--dim);margin:0 0 24px;font-size:14px}
form{display:flex;gap:8px;margin-bottom:10px}
input[type=text]{flex:1;padding:12px 14px;border-radius:8px;border:1px solid #2a2f3a;background:var(--card);color:var(--fg);font-size:15px}
button{padding:12px 18px;border-radius:8px;border:0;background:var(--acc);color:#0b0d12;font-weight:600;cursor:pointer}
.ex{color:var(--dim);font-size:13px;margin-bottom:26px}
.ex a{color:var(--acc);text-decoration:none;margin-right:14px;cursor:pointer}
.card{background:var(--card);border:1px solid #232833;border-radius:10px;padding:16px;margin:14px 0}
.card h2{font-size:12px;text-transform:uppercase;letter-spacing:.08em;color:var(--dim);margin:0 0 10px}
pre{margin:0;overflow-x:auto;font:12.5px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;white-space:pre}
.note{color:var(--warn);font-size:13px}
.p{border-left:2px solid #2a2f3a;padding:6px 0 6px 12px;margin:10px 0;color:#c8cdd6;font-size:13.5px}
.p .m{color:var(--dim);font-size:12px;font-family:ui-monospace,monospace}
.spin{color:var(--dim)}
</style>
<div class=wrap>
<h1>Ask your fleet</h1>
<p class=sub>Numbers are computed from transcripts, git and the Artel store. Prose is written over them and cited. The model never produces a figure.</p>
<form onsubmit="go(event)">
  <input id=q type=text placeholder="e.g. which project is least efficient, and why?" autofocus>
  <button>Ask</button>
</form>
<div class=ex>
  <a onclick="fill(this)">What is everyone working on?</a>
  <a onclick="fill(this)">Which project is least efficient?</a>
  <a onclick="fill(this)">What did last week cost us?</a>
  <a onclick="fill(this)">Why did we choose the archivist design?</a>
</div>
<div id=out></div>
</div>
<script>
function fill(a){document.getElementById('q').value=a.textContent;}
async function go(e){
  e.preventDefault();
  const q=document.getElementById('q').value.trim(); if(!q)return;
  const out=document.getElementById('out');
  out.innerHTML='<p class=spin>thinking…</p>';
  const r=await fetch('/api/ask',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({question:q,days:7})});
  const d=await r.json();
  let h='';
  const a=d.answer||{};
  h+='<div class=card><h2>Answer</h2>'+(a.dry_run?'<p class=note>Dry run — no LLM call was made. Set ASK_DRY_RUN=0 to answer for real.</p>':'')+
     '<div>'+(a.text||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/\\n/g,'<br>')+'</div></div>';
  h+='<div class=card><h2>Facts (computed, not generated)</h2><pre>'+(d.facts||'')+'</pre></div>';
  h+='<div class=card><h2>Records retrieved ('+(d.passages||[]).length+')</h2>';
  for(const p of d.passages||[]){
    h+='<div class=p><div class=m>['+p.id.slice(0,8)+'] '+(p.project||'unscoped')+' · '+p.type+' · '+p.agent+'</div>'+
       p.content.slice(0,320).replace(/&/g,'&amp;').replace(/</g,'&lt;')+'…</div>';
  }
  h+='</div>';
  out.innerHTML=h;
}
</script>"""


@app.get("/", response_class=HTMLResponse)
async def index():
    return PAGE
