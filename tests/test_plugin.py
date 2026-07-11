import json
import os
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


def test_user_config_shape():
    uc = _PLUGIN["userConfig"]
    assert set(uc) == {"artel_url", "agent_id", "api_key"}
    for v in uc.values():
        assert v["required"] is True
    assert uc["api_key"]["sensitive"] is True
    assert uc["artel_url"].get("sensitive", False) is False


def test_mcp_server_endpoint_has_trailing_slash():
    artel = _PLUGIN["mcpServers"]["artel"]
    assert artel["type"] == "http"
    # regression guard: the v0.4.0 manifest used "/mcp" with no slash, which
    # 400s behind a TLS-terminating proxy (redirect drops the POST body).
    assert artel["url"] == "${user_config.artel_url}/mcp/"
    assert artel["url"].endswith("/mcp/")
    assert artel["headers"]["x-agent-id"] == "${CLAUDE_PLUGIN_OPTION_AGENT_ID}"
    assert artel["headers"]["x-api-key"] == "${CLAUDE_PLUGIN_OPTION_API_KEY}"


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
    assert set(hooks) == {"SessionStart", "UserPromptSubmit", "PreToolUse", "Stop"}
    referenced = []
    for groups in hooks.values():
        for group in groups:
            for h in group["hooks"]:
                assert h["type"] == "command"
                referenced.append(h["command"])
    assert _PLUGIN["hooks"] == "./hooks/hooks.json"
    for cmd in referenced:
        rel = cmd.replace("${CLAUDE_PLUGIN_ROOT}/", "")
        script = _ROOT / rel
        assert script.is_file(), f"missing hook script: {rel}"
        assert os.access(script, os.X_OK), f"hook script not executable: {rel}"
        assert script.read_text().startswith("#!"), f"hook script missing shebang: {rel}"


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
