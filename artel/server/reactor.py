import json
import sqlite3
from collections.abc import Callable

from .blueprint import (
    ITEM,
    BlueprintDocument,
    DoneCheck,
    TemplateNode,
    dig,
    find_run_tag,
    node_by_id,
    render,
    run_tag,
    successors,
)
from .models import new_id

CHECK_PAYLOAD = "payload"
CHECK_SQLITE = "sqlite"
EXPECT_ROWS = "rows"
EXPECT_NO_ROWS = "no_rows"
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
EVENT_EXPANDED = "blueprint.node.expanded"
EVENT_CHECK_FAILED = "blueprint.node.check_failed"
EVENT_RUN_COMPLETED = "blueprint.run.completed"
REMEDIATION_TAG = "remediation"

CheckFn = Callable[[DoneCheck, dict | None, sqlite3.Connection], tuple[bool, str]]


def _check_payload(
    check: DoneCheck, payload: dict | None, db: sqlite3.Connection
) -> tuple[bool, str]:
    value = dig(payload or {}, check.path or "")
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
    check: DoneCheck, payload: dict | None, db: sqlite3.Connection
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


CHECKS: dict[str, CheckFn] = {CHECK_PAYLOAD: _check_payload, CHECK_SQLITE: _check_sqlite}


def register_check(kind: str, fn: CheckFn) -> None:
    CHECKS[kind] = fn


def evaluate(
    check: DoneCheck | None, payload: dict | None, db: sqlite3.Connection
) -> tuple[bool, str]:
    if check is None:
        return True, ""
    fn = CHECKS.get(check.kind)
    if fn is None:
        return False, f"unknown done-check kind {check.kind!r}"
    return fn(check, payload, db)


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
        "INSERT INTO blueprint_run_nodes (run_id, node_id, task_id, item) VALUES (?,?,?,?)",
        (run["id"], node.id, task_id, json.dumps(item) if item is not None else None),
    )
    return task_id


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


def on_task_completed(db: sqlite3.Connection, task_id: str, agent_id: str) -> None:
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
    passed, reason = evaluate(node.done_check, payload, db)
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
    return run_id
