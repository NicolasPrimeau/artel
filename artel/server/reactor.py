import json
import logging
import sqlite3
from collections.abc import Callable

from . import blueprint as blueprint_module
from . import git_anchor
from .blueprint import (
    CHECK_PAYLOAD,
    ITEM,
    BlueprintDocument,
    DoneCheck,
    NodeAction,
    TemplateNode,
    dig,
    find_run_tag,
    node_by_id,
    render,
    run_tag,
    successors,
)
from .config import settings
from .models import new_id

log = logging.getLogger(__name__)

CHECK_SQLITE = "sqlite"
CHECK_GIT = "git"
EXPECT_ROWS = "rows"
EXPECT_NO_ROWS = "no_rows"
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
EVENT_EXPANDED = "blueprint.node.expanded"
EVENT_CHECK_FAILED = "blueprint.node.check_failed"
EVENT_RUN_COMPLETED = "blueprint.run.completed"
EVENT_ACTION_FAILED = "blueprint.node.action_failed"
REMEDIATION_TAG = "remediation"

CheckContext = dict
CheckFn = Callable[[DoneCheck, object, sqlite3.Connection, CheckContext], tuple[bool, str]]


def _check_payload(
    check: DoneCheck, payload: object, db: sqlite3.Connection, context: CheckContext
) -> tuple[bool, str]:
    value = dig(payload if payload is not None else {}, check.path or "")
    if value is None:
        return False, f"output has nothing at {check.path!r}"
    if check.min_items is not None:
        if not isinstance(value, list):
            return False, f"{check.path!r} is not a list"
        if len(value) < check.min_items:
            return False, f"{check.path!r} has {len(value)} items, expected {check.min_items}"
    if value == [] or value == {} or value == "":
        return False, f"{check.path!r} is empty"
    return True, ""


def _check_sqlite(
    check: DoneCheck, payload: object, db: sqlite3.Connection, context: CheckContext
) -> tuple[bool, str]:
    query = (check.query or "").strip().rstrip(";")
    if not query.lower().startswith("select"):
        return False, "sqlite done-check must be a single SELECT"
    if ";" in query:
        return False, "sqlite done-check must be a single statement"
    try:
        rows = db.execute(query).fetchall()
    except sqlite3.Error as e:
        return False, f"sqlite done-check failed: {e}"
    expect = check.expect or EXPECT_ROWS
    if expect == EXPECT_NO_ROWS:
        return (not rows), ("query returned rows" if rows else "")
    return bool(rows), ("" if rows else "query returned no rows")


def _check_git(
    check: DoneCheck, payload: object, db: sqlite3.Connection, context: CheckContext
) -> tuple[bool, str]:
    """Reads the repository, so a well-shaped payload cannot satisfy it.

    The baseline for a `changed` expectation is captured when the task is CREATED
    (see create_node_task). Comparing only against HEAD at completion would prove
    nothing — the file may have looked that way all along.
    """
    anchor = check.anchor or check.path
    if not anchor:
        return False, "a git done-check needs an anchor"
    try:
        return git_anchor.evaluate(
            settings.blueprint_repo_root,
            anchor,
            check.expect or git_anchor.EXPECT_CHANGED,
            check.value,
            context.get("baseline"),
        )
    except git_anchor.GitAnchorError as e:
        return False, str(e)


CHECKS: dict[str, CheckFn] = {
    CHECK_PAYLOAD: _check_payload,
    CHECK_SQLITE: _check_sqlite,
    CHECK_GIT: _check_git,
}


def register_check(kind: str, fn: CheckFn) -> None:
    CHECKS[kind] = fn


# --- actions: node bodies the server runs itself -----------------------------------
# The reactor could already VERIFY mechanically but not ACT mechanically. These are
# the executors. Same registry shape as CHECKS deliberately: a deployment adds git,
# http or shell backends by registering them, and core ships only what is safe to
# run against a model-authored document.

ACTION_SQLITE = "sqlite"
ACTION_CONSTANT = "constant"

ActionFn = Callable[[NodeAction, dict, sqlite3.Connection], object]


def _action_sqlite(action: NodeAction, context: dict, db: sqlite3.Connection) -> object:
    query = (action.query or "").strip().rstrip(";")
    rendered = render(query, context)
    if not rendered.lower().startswith("select"):
        raise ValueError("sqlite action must be a single SELECT")
    if ";" in rendered:
        raise ValueError("sqlite action must be a single statement")
    rows = db.execute(rendered).fetchall()
    return [dict(r) for r in rows]


def _action_constant(action: NodeAction, context: dict, db: sqlite3.Connection) -> object:
    if isinstance(action.value, str):
        return render(action.value, context)
    return action.value


ACTIONS: dict[str, ActionFn] = {
    ACTION_SQLITE: _action_sqlite,
    ACTION_CONSTANT: _action_constant,
}


def register_action(kind: str, fn: ActionFn) -> None:
    ACTIONS[kind] = fn
    blueprint_module.ACTION_KINDS.add(kind)


blueprint_module.ACTION_KINDS.update(ACTIONS)


def evaluate(
    check: DoneCheck | None,
    payload: object,
    db: sqlite3.Connection,
    context: CheckContext | None = None,
) -> tuple[bool, str]:
    if check is None:
        return True, ""
    fn = CHECKS.get(check.kind)
    if fn is None:
        return False, f"unknown done-check kind {check.kind!r}"
    return fn(check, payload, db, context or {})


def _load_run(db: sqlite3.Connection, run_id: str) -> sqlite3.Row | None:
    return db.execute("SELECT * FROM blueprint_runs WHERE id=?", (run_id,)).fetchone()


def _load_document(db: sqlite3.Connection, blueprint_id: str) -> BlueprintDocument | None:
    row = db.execute("SELECT document FROM blueprints WHERE id=?", (blueprint_id,)).fetchone()
    if not row:
        return None
    return BlueprintDocument(**json.loads(row["document"]))


def _node_tasks(db: sqlite3.Connection, run_id: str, node_id: str) -> list[sqlite3.Row]:
    return db.execute(
        """SELECT t.* FROM blueprint_run_nodes n JOIN tasks t ON t.id=n.task_id
           WHERE n.run_id=? AND n.node_id=? AND n.superseded=0""",
        (run_id, node_id),
    ).fetchall()


def _deps_satisfied(db: sqlite3.Connection, run_id: str, node: TemplateNode) -> bool:
    for dep in node.deps:
        tasks = _node_tasks(db, run_id, dep)
        if not tasks or any(t["status"] != STATUS_COMPLETED for t in tasks):
            return False
    return True


def _collect_items(db: sqlite3.Connection, run_id: str, foreach: str) -> list:
    source, _, path = foreach.partition(".")
    items: list = []
    for task in _node_tasks(db, run_id, source):
        payload = json.loads(task["completion_payload"]) if task["completion_payload"] else {}
        value = dig(payload, path)
        if isinstance(value, list):
            items.extend(value)
        elif value is not None:
            items.append(value)
    return items


def _emit(db: sqlite3.Connection, event_type: str, agent_id: str, payload: dict) -> None:
    db.execute(
        "INSERT INTO events (id, type, agent_id, payload) VALUES (?,?,?,?)",
        (new_id(), event_type, agent_id, json.dumps(payload)),
    )


def create_node_task(
    db: sqlite3.Connection,
    run: sqlite3.Row,
    node: TemplateNode,
    context: dict,
    item: object | None = None,
    title_prefix: str = "",
) -> str:
    task_id = new_id()
    baseline = _capture_baseline(node)
    tags = list(node.tags) + [run_tag(run["id"], node.id)]
    if title_prefix:
        tags.append(REMEDIATION_TAG)
    db.execute(
        """INSERT INTO tasks (id, title, description, expected_outcome, created_by,
           project, priority, tags, completion_contract)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            task_id,
            title_prefix + render(node.title, context),
            render(node.description, context),
            render(node.expected_outcome, context),
            run["created_by"],
            run["project"],
            node.priority,
            json.dumps(tags),
            json.dumps(node.completion_contract) if node.completion_contract else None,
        ),
    )
    db.execute(
        """INSERT INTO blueprint_run_nodes (run_id, node_id, task_id, item, baseline)
           VALUES (?,?,?,?,?)""",
        (run["id"], node.id, task_id, json.dumps(item) if item is not None else None, baseline),
    )
    return task_id


def _capture_baseline(node: TemplateNode) -> str | None:
    """Snapshot what the repo looks like BEFORE the work starts.

    Without this a `changed` check is unfalsifiable: it can only compare HEAD to
    itself. The snapshot is what makes "the agent actually edited this function"
    a claim about the work rather than about the file.
    """
    check = node.done_check
    if check is None or check.kind != CHECK_GIT:
        return None
    if (check.expect or git_anchor.EXPECT_CHANGED) != git_anchor.EXPECT_CHANGED:
        return None
    anchor = check.anchor or check.path
    if not anchor:
        return None
    try:
        return git_anchor.anchor_sha(settings.blueprint_repo_root, anchor)
    except git_anchor.GitAnchorError as e:
        log.warning("could not capture git baseline for %s: %s", node.id, e)
        return None


def _context(run: sqlite3.Row, item: object | None = None, index: int | None = None) -> dict:
    context = dict(json.loads(run["params"] or "{}"))
    if item is not None:
        context[ITEM] = item
    if index is not None:
        context["index"] = index
    return context


def _expand(db: sqlite3.Connection, run: sqlite3.Row, doc: BlueprintDocument, node_id: str) -> list:
    created: list[dict] = []
    for node in successors(doc, node_id):
        if _node_tasks(db, run["id"], node.id):
            continue
        if not _deps_satisfied(db, run["id"], node):
            continue
        if node.foreach:
            items = _collect_items(db, run["id"], node.foreach)
            for index, item in enumerate(items):
                task_id = create_node_task(db, run, node, _context(run, item, index), item)
                created.append({"node_id": node.id, "task_id": task_id})
        else:
            task_id = create_node_task(db, run, node, _context(run))
            created.append({"node_id": node.id, "task_id": task_id})
    return created


def _pending(db: sqlite3.Connection, run_id: str) -> bool:
    row = db.execute(
        """SELECT COUNT(*) AS n FROM blueprint_run_nodes n JOIN tasks t ON t.id=n.task_id
           WHERE n.run_id=? AND n.superseded=0 AND t.status NOT IN ('completed','failed')""",
        (run_id,),
    ).fetchone()
    return bool(row["n"])


def _expandable(db: sqlite3.Connection, run_id: str, doc: BlueprintDocument) -> bool:
    for node in doc.nodes:
        if _node_tasks(db, run_id, node.id):
            continue
        if _deps_satisfied(db, run_id, node):
            return True
    return False


def _finish_if_done(
    db: sqlite3.Connection, run: sqlite3.Row, doc: BlueprintDocument, agent_id: str
) -> bool:
    if _pending(db, run["id"]) or _expandable(db, run["id"], doc):
        return False
    db.execute(
        """UPDATE blueprint_runs SET status=?,
           updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?""",
        (STATUS_COMPLETED, run["id"]),
    )
    _emit(db, EVENT_RUN_COMPLETED, agent_id, {"run_id": run["id"], "blueprint": run["name"]})
    return True


def _machine_nodes(doc: BlueprintDocument) -> dict[str, TemplateNode]:
    return {n.id: n for n in doc.nodes if n.run is not None}


def _run_action(node: TemplateNode, context: dict, db: sqlite3.Connection) -> tuple[bool, object]:
    fn = ACTIONS.get(node.run.kind) if node.run else None
    if fn is None:
        return False, f"unknown action kind {node.run.kind!r}" if node.run else "no action"
    try:
        return True, fn(node.run, context, db)
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def _settle(
    db: sqlite3.Connection,
    run: sqlite3.Row,
    doc: BlueprintDocument,
    agent_id: str,
    limit: int = 200,
) -> None:
    """Execute every open machine node, then let the reactor react to it.

    A lowered node still gets a task row, a backpointer, a contract check and a
    done-check — it is completed by the server rather than claimed by an agent.
    Keeping the bookkeeping identical is what makes `foreach` over a machine
    node's output work with no special cases.
    """
    machine = _machine_nodes(doc)
    if not machine:
        return
    for _ in range(limit):
        rows = db.execute(
            """SELECT n.task_id, n.node_id, n.item FROM blueprint_run_nodes n
               JOIN tasks t ON t.id = n.task_id
               WHERE n.run_id=? AND n.superseded=0 AND t.status='open'""",
            (run["id"],),
        ).fetchall()
        pending = [r for r in rows if r["node_id"] in machine]
        if not pending:
            return
        for row in pending:
            node = machine[row["node_id"]]
            item = json.loads(row["item"]) if row["item"] else None
            ok, result = _run_action(node, _context(run, item), db)
            if not ok:
                db.execute(
                    """UPDATE tasks SET status='failed',
                       updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?""",
                    (row["task_id"],),
                )
                _emit(
                    db,
                    EVENT_ACTION_FAILED,
                    agent_id,
                    {"run_id": run["id"], "node_id": node.id, "error": str(result)[:300]},
                )
                continue
            payload = json.dumps(result) if result is not None else None
            db.execute(
                """UPDATE tasks SET status='completed', completion_payload=?,
                   updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?""",
                (payload, row["task_id"]),
            )
            on_task_completed(db, row["task_id"], agent_id, settle=False)
    log.warning("blueprint run %s hit the settle limit", run["id"])


def on_task_completed(
    db: sqlite3.Connection, task_id: str, agent_id: str, settle: bool = True
) -> None:
    task = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not task:
        return
    ref = find_run_tag(json.loads(task["tags"] or "[]"))
    if not ref:
        return
    run_id, node_id = ref
    run = _load_run(db, run_id)
    if not run or run["status"] != STATUS_RUNNING:
        return
    doc = _load_document(db, run["blueprint_id"])
    if doc is None:
        return
    node = node_by_id(doc, node_id)
    if node is None:
        return
    payload = json.loads(task["completion_payload"]) if task["completion_payload"] else None
    node_row = db.execute(
        "SELECT baseline FROM blueprint_run_nodes WHERE run_id=? AND task_id=?",
        (run_id, task_id),
    ).fetchone()
    context = {
        "baseline": node_row["baseline"] if node_row else None,
        "task_id": task_id,
        "run_id": run_id,
    }
    passed, reason = evaluate(node.done_check, payload, db, context)
    if not passed:
        item_row = db.execute(
            "SELECT item FROM blueprint_run_nodes WHERE run_id=? AND task_id=?",
            (run_id, task_id),
        ).fetchone()
        item = json.loads(item_row["item"]) if item_row and item_row["item"] else None
        db.execute(
            "UPDATE blueprint_run_nodes SET superseded=1 WHERE run_id=? AND task_id=?",
            (run_id, task_id),
        )
        remediation_id = create_node_task(
            db, run, node, _context(run, item), item, title_prefix="Remediate: "
        )
        db.execute(
            """UPDATE tasks SET description = description || ?
               WHERE id=?""",
            (f"\n\n---\nDone-check failed on {task_id}: {reason}", remediation_id),
        )
        _emit(
            db,
            EVENT_CHECK_FAILED,
            agent_id,
            {
                "run_id": run_id,
                "node_id": node_id,
                "task_id": task_id,
                "reason": reason,
                "remediation_task_id": remediation_id,
            },
        )
        return
    created = _expand(db, run, doc, node_id)
    if created and settle:
        _settle(db, run, doc, agent_id)
    if created:
        _emit(
            db,
            EVENT_EXPANDED,
            agent_id,
            {"run_id": run_id, "node_id": node_id, "created": created},
        )
    _finish_if_done(db, run, doc, agent_id)


def start_run(
    db: sqlite3.Connection,
    blueprint_row: sqlite3.Row,
    doc: BlueprintDocument,
    params: dict,
    project: str | None,
    agent_id: str,
) -> str:
    run_id = new_id()
    db.execute(
        """INSERT INTO blueprint_runs (id, blueprint_id, name, params, project, created_by)
           VALUES (?,?,?,?,?,?)""",
        (run_id, blueprint_row["id"], doc.name, json.dumps(params), project, agent_id),
    )
    run = _load_run(db, run_id)
    for node in doc.nodes:
        if not node.deps:
            create_node_task(db, run, node, _context(run))
    _settle(db, run, doc, agent_id)
    return run_id
