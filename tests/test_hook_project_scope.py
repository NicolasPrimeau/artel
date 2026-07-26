import importlib.util
import json
import pathlib

_MODULE_PATH = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "_artel_hooks.py"


def _load():
    spec = importlib.util.spec_from_file_location("artel_hooks_projscope", _MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


hooks = _load()


def _clear_env(monkeypatch):
    for k in ("MCP_PROJECT", "ARTEL_PROJECT", "CLAUDE_PLUGIN_OPTION_MCP_PROJECT"):
        monkeypatch.delenv(k, raising=False)


def test_resolve_project_prefers_env(monkeypatch):
    monkeypatch.setenv("MCP_PROJECT", "nimbus")
    assert hooks.resolve_project({"cwd": "/nonexistent"}) == "nimbus"


def test_resolve_project_reads_mcp_json_from_cwd(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"artel": {"headers": {"x-mcp-project": "nimbus"}}}})
    )
    assert hooks.resolve_project({"cwd": str(tmp_path)}) == "nimbus"


def test_resolve_project_reads_settings_local_env(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.local.json").write_text(
        json.dumps({"env": {"MCP_PROJECT": "blog"}})
    )
    assert hooks.resolve_project({"cwd": str(tmp_path)}) == "blog"


def test_resolve_project_none_when_unconfigured(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    assert hooks.resolve_project({"cwd": str(tmp_path)}) == ""


def test_resolve_project_uses_claude_project_dir(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"artel": {"headers": {"x-mcp-project": "longshot"}}}})
    )
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    assert hooks.resolve_project(None) == "longshot"


def test_search_includes_project_param(monkeypatch):
    captured = {}

    def fake_get(path):
        captured["path"] = path
        return []

    monkeypatch.setattr(hooks, "get", fake_get)
    hooks.search("deploy the api", project="nimbus")
    assert "project=nimbus" in captured["path"]


def test_search_omits_project_when_empty(monkeypatch):
    captured = {}
    monkeypatch.setattr(hooks, "get", lambda path: captured.update(path=path) or [])
    hooks.search("deploy the api", project="")
    assert "project=" not in captured["path"]


def test_recall_scopes_search_to_project(monkeypatch, capsys, tmp_path):
    _clear_env(monkeypatch)
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"artel": {"headers": {"x-mcp-project": "nimbus"}}}})
    )
    monkeypatch.setattr(
        hooks,
        "payload",
        lambda: {"prompt": "how do we deploy the api", "session_id": "s1", "cwd": str(tmp_path)},
    )
    seen = {}

    def fake_search(q, limit=6, project=""):
        seen["project"] = project
        return []

    monkeypatch.setattr(hooks, "search", fake_search)
    hooks.cmd_recall()
    assert seen["project"] == "nimbus"


def test_post_capture_includes_project(monkeypatch):
    monkeypatch.setenv("ARTEL_URL", "http://test.local")
    captured = {}

    class FakeResp:
        status = 201

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        captured["body"] = json.loads(req.data.decode())
        return FakeResp()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    hooks._post_capture("some session content", "sess-1", project="nimbus")
    assert captured["body"]["project"] == "nimbus"
    assert captured["body"]["session_id"] == "sess-1"


def test_identity_from_cwd_reads_mcp_json(tmp_path):
    (tmp_path / ".mcp.json").write_text(
        json.dumps(
            {"mcpServers": {"artel": {"headers": {"x-agent-id": "builddata", "x-api-key": "k1"}}}}
        )
    )
    assert hooks._identity_from_cwd(str(tmp_path)) == ("builddata", "k1")


def test_identity_from_cwd_ignores_env_placeholders(tmp_path):
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"artel": {"headers": {"x-agent-id": "${ARTEL_AGENT_ID}"}}}})
    )
    assert hooks._identity_from_cwd(str(tmp_path)) == ("", "")


def test_drain_attributes_each_session_to_its_own_repo(monkeypatch, tmp_path):
    # Two concurrent sessions in DIFFERENT repos share one detached drainer.
    # Each capture must be posted with its own repo's agent/project, not the
    # forking session's ambient env.
    import json as _json

    repo_a = tmp_path / "repoA"
    repo_b = tmp_path / "repoB"
    for repo, aid, proj in ((repo_a, "agentA", "projA"), (repo_b, "agentB", "projB")):
        repo.mkdir()
        (repo / ".mcp.json").write_text(
            _json.dumps(
                {
                    "mcpServers": {
                        "artel": {
                            "headers": {
                                "x-agent-id": aid,
                                "x-api-key": f"key-{aid}",
                                "x-mcp-project": proj,
                            }
                        }
                    }
                }
            )
        )

    spool = tmp_path / "spool"
    spool.mkdir()
    monkeypatch.setattr(hooks, "_spool_dir", lambda: str(spool))

    # two transcripts, each big enough to clear the size floor
    for name in ("ta", "tb"):
        (spool / f"{name}.jsonl").write_text(
            "\n".join(
                _json.dumps({"role": "user", "content": f"{name} message number {i} " * 8})
                for i in range(15)
            )
        )

    (spool / "incoming.jsonl").write_text(
        _json.dumps(
            {"session_id": "sA", "transcript_path": str(spool / "ta.jsonl"), "cwd": str(repo_a)}
        )
        + "\n"
        + _json.dumps(
            {"session_id": "sB", "transcript_path": str(spool / "tb.jsonl"), "cwd": str(repo_b)}
        )
        + "\n"
    )

    posted = {}
    monkeypatch.setattr(
        hooks,
        "_post_capture",
        lambda content, sid, project="", agent_id="", api_key="": (
            posted.update({sid: (agent_id, api_key, project)}) or True
        ),
    )

    hooks.cmd_drain()

    assert posted["sA"] == ("agentA", "key-agentA", "projA")
    assert posted["sB"] == ("agentB", "key-agentB", "projB")
