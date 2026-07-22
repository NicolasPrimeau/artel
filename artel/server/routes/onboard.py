from fastapi import APIRouter, Query, Request
from fastapi.responses import PlainTextResponse

from ..config import settings

router = APIRouter(tags=["onboard"])


def _advertise_url(request: Request) -> str:
    if settings.public_url:
        return settings.public_url.rstrip("/")
    return str(request.base_url).rstrip("/")


_SCRIPT = r"""#!/bin/sh
set -e

ARTEL_URL="{artel_url}"
MCP_URL="{mcp_url}"
PROJECT="{project}"

_git_name() {{
    remote=$(git remote get-url origin 2>/dev/null) || true
    if [ -n "$remote" ]; then
        basename "$remote" .git
    fi
}}
_repo=$(_git_name)
_sanitize() {{ printf '%s' "$1" | tr -cs 'a-zA-Z0-9_-' '-' | sed 's/--*/-/g; s/^-//; s/-$//'; }}
if [ -n "$_repo" ]; then
    DEFAULT_ID="$(_sanitize "$(hostname -s)")-$(_sanitize "${{_repo}}")"
else
    DEFAULT_ID="$(_sanitize "$(hostname -s)")"
fi

_MCP=".mcp.json"

if [ -f "$_MCP" ] && command -v python3 >/dev/null 2>&1; then
    _EXISTING_ID=$(python3 -c "import json,sys; h=json.load(open('.mcp.json')).get('mcpServers',{{}}).get('artel',{{}}).get('headers',{{}}); print(h.get('x-agent-id',''))" 2>/dev/null || true)
fi

AGENT_ID="${{AGENT_ID:-${{_EXISTING_ID:-$DEFAULT_ID}}}}"

ARTEL_URL="$ARTEL_URL" MCP_URL="$MCP_URL" BASE_ID="$AGENT_ID" PROJECT="$PROJECT" ARTEL_REG_KEY="${{ARTEL_REG_KEY:-}}" python3 << 'PYEOF'
import os, json, urllib.request, urllib.error, sys, pathlib

url     = os.environ['ARTEL_URL']
mcp_url = os.environ['MCP_URL']
base_id = os.environ['BASE_ID']
project = os.environ.get('PROJECT') or None
reg_key = os.environ.get('ARTEL_REG_KEY') or None

creds_dir = pathlib.Path.home() / '.config' / 'artel'

def _creds_path(aid):
    return creds_dir / aid

def _load_creds():
    candidate = _creds_path(base_id)
    if candidate.exists():
        return _parse_creds(candidate)
    return None, None

def _parse_creds(path):
    text = path.read_text()
    aid = akey = None
    for line in text.splitlines():
        if line.startswith('MCP_AGENT_ID='): aid  = line.split('=', 1)[1].strip()
        if line.startswith('MCP_AGENT_KEY='): akey = line.split('=', 1)[1].strip()
    return aid, akey

def _valid(aid, akey):
    if not aid or not akey:
        return False
    req = urllib.request.Request(
        url + '/agents/me',
        headers={{'x-agent-id': aid, 'x-api-key': akey}},
    )
    try:
        with urllib.request.urlopen(req) as r:
            return r.status == 200
    except urllib.error.HTTPError:
        return False
    except Exception:
        return None

def _register(agent_id):
    headers = {{'content-type': 'application/json'}}
    if reg_key:
        headers['x-registration-key'] = reg_key
    req = urllib.request.Request(
        url + '/agents/self-register',
        data=json.dumps({{'agent_id': agent_id, 'project': project}}).encode(),
        headers=headers,
        method='POST',
    )
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        try:
            detail = json.loads(body).get('detail', body)
        except Exception:
            detail = body
        if e.code == 401:
            print('error: registration requires a key.')
            print('action: add this line to ~/.bashrc and re-run:')
            print('  export ARTEL_REG_KEY=<your-registration-key>')
        else:
            print('error: registration failed ({{}}) — {{}}'.format(e.code, detail))
        sys.exit(1)
    except urllib.error.URLError as e:
        print('error: could not reach {{}} — {{}}'.format(url, e.reason)); sys.exit(1)

def _write_mcp(aid, akey):
    mcp_config = {{
        'mcpServers': {{
            'artel': {{
                'type': 'http',
                'url': mcp_url + '/mcp/',
                'headers': {{'x-agent-id': aid, 'x-api-key': akey}},
            }}
        }}
    }}
    with open('.mcp.json', 'w') as f:
        json.dump(mcp_config, f, indent=2); f.write('\n')

aid, akey = _load_creds()
refreshed = False

valid = _valid(aid, akey)
if valid is None:
    print('error: cannot reach {{}} — is the server running?'.format(url)); sys.exit(1)
elif valid:
    _write_mcp(aid, akey)
    print('  agent    : ' + aid + '  (credentials valid, refreshed .mcp.json)')
    refreshed = True
else:
    data = _register(base_id)
    aid, akey = data['agent_id'], data['api_key']
    creds_dir.mkdir(parents=True, exist_ok=True)
    _creds_path(aid).write_text('MCP_AGENT_ID={{}}\nMCP_AGENT_KEY={{}}\n'.format(aid, akey))
    _write_mcp(aid, akey)
    print('  agent    : ' + aid)
    if project:
        print('  project  : ' + project)
    print('  creds    : ~/.config/artel/' + aid)

bashrc = pathlib.Path.home() / '.bashrc'
marker = '_artel_load()'
if bashrc.exists() and marker not in bashrc.read_text():
    with open(bashrc, 'a') as f:
        f.write(
            '\n_artel_load() {{\n'
            '    if [ -f ".mcp.json" ]; then\n'
            '        aid=$(python3 -c "import json; print(json.load(open(\'.mcp.json\'))[\'mcpServers\'][\'artel\'][\'headers\'].get(\'x-agent-id\', \'\'))" 2>/dev/null || true)\n'
            '        if [ -n "$aid" ]; then\n'
            '            creds="$HOME/.config/artel/$aid"\n'
            '            [ -f "$creds" ] && {{ set -a; source "$creds"; set +a; }}\n'
            '        fi\n'
            '    fi\n'
            '}}\n'
            'if [ -n "$PROMPT_COMMAND" ]; then\n'
            '    export PROMPT_COMMAND="_artel_load;$PROMPT_COMMAND"\n'
            'else\n'
            '    export PROMPT_COMMAND="_artel_load"\n'
            'fi\n'
        )

if not refreshed:
    print('  .mcp.json written')
    print()
    print('start a new Claude Code session to connect')
else:
    print()
    print('start a new Claude Code session to reconnect')

opencode_cfg = {{
    'mcp': {{
        'artel': {{
            'type': 'sse',
            'url': mcp_url + '/sse/',
            'headers': {{'x-agent-id': aid, 'x-api-key': akey}},
        }}
    }}
}}
import shutil
if shutil.which('opencode'):
    print()
    print('── OpenCode detected ──────────────────────────────────────────')
    print('Add to your OpenCode config (opencode.json or ~/.config/opencode/config.json):')
    print(json.dumps(opencode_cfg, indent=2))
    print()
    print('Wake daemon (spawns opencode when a message arrives):')
    print('  MCP_AGENT_ID={{}} MCP_AGENT_KEY={{}} ARTEL_URL={{}} artel-watch'.format(aid, akey, url))
PYEOF
"""


@router.get("/onboard", response_class=PlainTextResponse)
async def onboard(
    request: Request,
    project: str | None = Query(default=None),
):
    artel_url = _advertise_url(request)
    return _SCRIPT.format(artel_url=artel_url, project=project or "", mcp_url=artel_url)


# One-shot plugin installer. Registers an agent, persists ARTEL_* to a sourced env
# file (which the plugin's MCP server and hooks read via ${ARTEL_*} substitution),
# then installs the plugin through the `claude` CLI with a slash-command fallback.
# Uses __PLACEHOLDER__ sentinels + str.replace (no brace-doubling), so the embedded
# python heredoc stays readable.
_INSTALL = r"""#!/bin/sh
set -e

ARTEL_URL="__ARTEL_URL__"
REPO="__REPO__"
MKT="__MKT__"
PLUGIN="__PLUGIN__"

_sanitize() { printf '%s' "$1" | tr -cs 'a-zA-Z0-9_-' '-' | sed 's/--*/-/g; s/^-//; s/-$//'; }
_repo=$(git remote get-url origin 2>/dev/null | sed 's#.*/##; s/\.git$//') || true
if [ -n "$_repo" ]; then
    DEFAULT_ID="$(_sanitize "$(hostname -s)")-$(_sanitize "$_repo")"
else
    DEFAULT_ID="$(_sanitize "$(hostname -s)")"
fi
AGENT_ID="${AGENT_ID:-$DEFAULT_ID}"

ARTEL_URL="$ARTEL_URL" BASE_ID="$AGENT_ID" ARTEL_REG_KEY="${ARTEL_REG_KEY:-}" python3 << 'PYEOF'
import os, json, urllib.request, urllib.error, sys, pathlib

url = os.environ['ARTEL_URL']
base_id = os.environ['BASE_ID']
reg_key = os.environ.get('ARTEL_REG_KEY') or None
creds_dir = pathlib.Path.home() / '.config' / 'artel'

def parse(path):
    aid = akey = None
    for line in path.read_text().splitlines():
        if line.startswith('MCP_AGENT_ID='): aid = line.split('=', 1)[1].strip()
        if line.startswith('MCP_AGENT_KEY='): akey = line.split('=', 1)[1].strip()
    return aid, akey

def valid(aid, akey):
    if not aid or not akey:
        return False
    req = urllib.request.Request(url + '/agents/me', headers={'x-agent-id': aid, 'x-api-key': akey})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status == 200
    except urllib.error.HTTPError:
        return False
    except Exception:
        return None

def register(aid):
    headers = {'content-type': 'application/json'}
    if reg_key:
        headers['x-registration-key'] = reg_key
    req = urllib.request.Request(
        url + '/agents/self-register',
        data=json.dumps({'agent_id': aid}).encode(), headers=headers, method='POST',
    )
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        try:
            detail = json.loads(body).get('detail', body)
        except Exception:
            detail = body
        if e.code == 401:
            print('error: registration requires a key. add to your shell and re-run:')
            print('  export ARTEL_REG_KEY=<your-registration-key>')
        else:
            print('error: registration failed ({}) — {}'.format(e.code, detail))
        sys.exit(1)
    except urllib.error.URLError as e:
        print('error: could not reach {} — {}'.format(url, e.reason))
        sys.exit(1)

candidate = creds_dir / base_id
aid, akey = parse(candidate) if candidate.exists() else (None, None)
v = valid(aid, akey)
if v is None:
    print('error: cannot reach {} — is the server running?'.format(url))
    sys.exit(1)
if not v:
    data = register(base_id)
    aid, akey = data['agent_id'], data['api_key']
    creds_dir.mkdir(parents=True, exist_ok=True)
    (creds_dir / aid).write_text('MCP_AGENT_ID={}\nMCP_AGENT_KEY={}\n'.format(aid, akey))

(creds_dir / 'env.sh').write_text(
    'export ARTEL_URL="{}"\nexport ARTEL_AGENT_ID="{}"\nexport ARTEL_API_KEY="{}"\n'.format(url, aid, akey)
)

# Claude Code substitutes ${ARTEL_*} in the plugin's MCP config from its settings
# env block (reliable, launch-independent) — unlike a shell file, which only works
# when Claude Code is launched from a shell that sourced it. Merge, preserving any
# existing settings.
settings_path = pathlib.Path.home() / '.claude' / 'settings.json'
try:
    settings = json.loads(settings_path.read_text()) if settings_path.exists() else {}
except Exception:
    settings = {}
if not isinstance(settings, dict):
    settings = {}
settings.setdefault('env', {})
settings['env'].update({'ARTEL_URL': url, 'ARTEL_AGENT_ID': aid, 'ARTEL_API_KEY': akey})
settings_path.parent.mkdir(parents=True, exist_ok=True)
settings_path.write_text(json.dumps(settings, indent=2) + '\n')

print('  agent : ' + aid)
print('  env   : ~/.config/artel/env.sh + ~/.claude/settings.json')
PYEOF

. "$HOME/.config/artel/env.sh"
for rc in "$HOME/.bashrc" "$HOME/.zshrc" "$HOME/.profile"; do
    [ -f "$rc" ] || continue
    grep -q 'config/artel/env.sh' "$rc" 2>/dev/null || \
        printf '\n[ -f "$HOME/.config/artel/env.sh" ] && . "$HOME/.config/artel/env.sh"\n' >> "$rc"
done

echo
if command -v claude >/dev/null 2>&1; then
    echo "installing the Artel plugin via the claude CLI..."
    claude plugin marketplace add "$REPO" || echo "  (if this failed, run in Claude Code: /plugin marketplace add $REPO)"
    claude plugin install "$PLUGIN@$MKT" || echo "  (if this failed, run in Claude Code: /plugin install $PLUGIN@$MKT)"
    echo
    echo "done — start a new Claude Code session to activate the plugin."
else
    echo "agent registered and env written. claude CLI not found — in a Claude Code session run:"
    echo "  /plugin marketplace add $REPO"
    echo "  /plugin install $PLUGIN@$MKT"
fi
"""


@router.get("/plugin/install", response_class=PlainTextResponse)
async def plugin_install(request: Request):
    artel_url = _advertise_url(request)
    return (
        _INSTALL.replace("__ARTEL_URL__", artel_url)
        .replace("__REPO__", "NicolasPrimeau/artel")
        .replace("__MKT__", "artel")
        .replace("__PLUGIN__", "artel")
    )
