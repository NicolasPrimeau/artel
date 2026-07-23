import json
import os
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_PLUGIN = json.loads((_ROOT / ".claude-plugin" / "plugin.json").read_text())
_MARKET = json.loads((_ROOT / ".claude-plugin" / "marketplace.json").read_text())
_HOOKS = json.loads((_ROOT / "hooks" / "hooks.json").read_text())


def test_plugin_identity():
    assert _PLUGIN["name"] == "artel"
    assert _PLUGIN["license"] == "MIT"
    for field in ("description", "author", "homepage", "repository", "keywords"):
        assert _PLUGIN[field]


def test_env_configured_not_prompted():
    # configured from ARTEL_* env (scriptable, no interactive prompt), not userConfig
    assert "userConfig" not in _PLUGIN


def test_mcp_server_endpoint_has_trailing_slash():
    artel = _PLUGIN["mcpServers"]["artel"]
    assert artel["type"] == "http"
    # regression guard: the v0.4.0 manifest used "/mcp" with no slash, which
    # 400s behind a TLS-terminating proxy (redirect drops the POST body).
    assert artel["url"] == "${ARTEL_URL}/mcp/"
    assert artel["url"].endswith("/mcp/")
    assert artel["headers"]["x-agent-id"] == "${ARTEL_AGENT_ID}"
    assert artel["headers"]["x-api-key"] == "${ARTEL_API_KEY}"


def test_marketplace_entry():
    assert _MARKET["name"] == "artel"
    assert _MARKET["owner"]["name"]
    plugins = _MARKET["plugins"]
    assert len(plugins) == 1
    entry = plugins[0]
    assert entry["name"] == "artel"
    assert entry["source"] == "./"
    assert entry["version"]


def test_hooks_wired_and_scripts_executable():
    hooks = _HOOKS["hooks"]
    assert set(hooks) == {"SessionStart", "UserPromptSubmit", "PreToolUse", "Stop", "PreCompact"}
    referenced = []
    for groups in hooks.values():
        for group in groups:
            for h in group["hooks"]:
                assert h["type"] == "command"
                referenced.append(h["command"])
    # Claude Code auto-loads the standard hooks/hooks.json; declaring it in the
    # manifest double-loads it and the whole plugin fails to load. Regression
    # guard: the manifest must NOT reference the standard hooks file.
    assert "hooks" not in _PLUGIN
    for cmd in referenced:
        rel = cmd.replace("${CLAUDE_PLUGIN_ROOT}/", "")
        script = _ROOT / rel
        assert script.is_file(), f"missing hook script: {rel}"
        assert os.access(script, os.X_OK), f"hook script not executable: {rel}"
        assert script.read_text().startswith("#!"), f"hook script missing shebang: {rel}"


def test_session_start_hook_hits_header_scoped_handoff_endpoint():
    # GET /sessions/handoff resolves the agent from the x-agent-id header — there is no
    # /sessions/handoff/{agent_id} path form. A trailing /$aid 404s and the hook then
    # silently injects nothing. Regression guard for that bug.
    src = (_ROOT / "scripts" / "artel-session-start.sh").read_text()
    assert "/sessions/handoff" in src
    assert "/sessions/handoff/$aid" not in src
    assert "/sessions/handoff/${aid}" not in src


def test_doctor_self_loads_installer_env_file():
    # Hooks/diagnostics don't inherit Claude Code's settings.json env block, so the
    # doctor must self-load ~/.config/artel/env.sh (like the other hook scripts) or it
    # reports a false "not set" when the plugin is actually configured.
    src = (_ROOT / "scripts" / "artel-doctor.sh").read_text()
    assert "config/artel/env.sh" in src


def test_commands_reference_plugin_namespaced_mcp_tools():
    # Plugin-bundled MCP tools are callable only as mcp__plugin_<plugin>_<server>__<tool>.
    # The bare mcp__artel__ prefix (a plain project .mcp.json server) does not resolve
    # inside the plugin, so a command referencing it fails to find the tool.
    server = next(iter(_PLUGIN["mcpServers"]))
    expected = f"mcp__plugin_{_PLUGIN['name']}_{server}__"
    for cmd in (_ROOT / "commands").glob("*.md"):
        body = cmd.read_text()
        for token in re.findall(r"mcp__[A-Za-z0-9_]+__", body):
            assert token.startswith(expected), (
                f"{cmd.name}: {token} must use the plugin form {expected}<tool>"
            )


def test_pretool_hook_targets_edit_tools():
    groups = _HOOKS["hooks"]["PreToolUse"]
    matchers = [g["matcher"] for g in groups]
    assert any("Edit" in m and "Write" in m for m in matchers)


def test_prompt_hooks_include_recall():
    scripts = [h["command"] for g in _HOOKS["hooks"]["UserPromptSubmit"] for h in g["hooks"]]
    assert any("artel-check-inbox.sh" in c for c in scripts)
    assert any("artel-recall.sh" in c for c in scripts)


def test_slash_commands_present():
    cmd_dir = _ROOT / "commands"
    expected = {"artel-recall", "artel-remember", "artel-handoff", "artel-tasks"}
    found = {p.stem for p in cmd_dir.glob("*.md")}
    assert expected <= found, f"missing commands: {expected - found}"
    for p in cmd_dir.glob("*.md"):
        assert p.read_text().startswith("---"), f"command missing frontmatter: {p.name}"


def test_opencode_plugin_present():
    plugin = _ROOT / "integrations" / "opencode" / "artel.ts"
    assert plugin.is_file()
    body = plugin.read_text()
    assert "@opencode-ai/plugin" in body
    assert "session.created" in body and "tool.execute.before" in body


def test_shared_hook_module_and_helpers_present():
    scripts = _ROOT / "scripts"
    module = scripts / "_artel_hooks.py"
    assert module.is_file()
    body = module.read_text()
    for kind in ("recall", "gotcha", "inbox", "stop", "status"):
        assert f'"{kind}"' in body, f"module missing dispatch for {kind}"
    assert "seen_filter" in body, "module missing per-session dedup"
    assert '"drain"' in body, "module missing capture drain dispatch"
    for extra in ("artel-statusline.sh", "artel-doctor.sh", "artel-capture.sh", "artel-drain.sh"):
        script = scripts / extra
        assert script.is_file(), f"missing {extra}"
        assert os.access(script, os.X_OK), f"{extra} not executable"
        assert script.read_text().startswith("#!"), f"{extra} missing shebang"
