#!/usr/bin/env bash
# The public demo at artel.run, brought up and taken down on purpose.
#
# It used to redeploy itself from every push to master, which meant the demo was
# running — and billing — whether or not anyone meant it to be. CI now only deploys
# the sandbox when someone asks (workflow_dispatch); this is the same operation from
# a laptop.
#
#   ./scripts/sandbox.sh up [version]   deploy and verify (default image: edge)
#   ./scripts/sandbox.sh down           destroy the machine, keep the volume
#   ./scripts/sandbox.sh status         what is running, what it costs to wake
set -euo pipefail

APP="artel-sandbox"
VOLUME="artel_data"
# The demo's own hostname, not artel.run. artel.run used to be a CNAME onto this app;
# it is now a registrar redirect to the GitHub repo, so probing it would report the
# repo's availability rather than the demo's — and a successful deploy would look
# like a failed one. If artel.run is ever pointed back here, change this too.
URL="https://artel-sandbox.fly.dev"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

die() { echo "error: $*" >&2; exit 1; }

command -v flyctl >/dev/null 2>&1 || export PATH="$HOME/.fly/bin:$PATH"
command -v flyctl >/dev/null 2>&1 || die "flyctl not found (looked in PATH and ~/.fly/bin)"

# The token lives in .env with every other secret rather than in this file.
if [[ -z "${FLY_API_TOKEN:-}" ]]; then
    [[ -f "$REPO_ROOT/.env" ]] || die "no FLY_API_TOKEN in the environment and no $REPO_ROOT/.env to read it from"
    FLY_API_TOKEN="$(grep -m1 '^FLY_API_TOKEN=' "$REPO_ROOT/.env" | cut -d= -f2-)"
    [[ -n "$FLY_API_TOKEN" ]] || die "FLY_API_TOKEN is not set in $REPO_ROOT/.env"
    export FLY_API_TOKEN
fi

machine_ids() { flyctl machines list -a "$APP" --json 2>/dev/null | python3 -c 'import json,sys; print(" ".join(m["id"] for m in json.load(sys.stdin)))'; }
volume_exists() { flyctl volumes list -a "$APP" --json 2>/dev/null | python3 -c 'import json,sys; print("yes" if any(v["name"]=="'"$VOLUME"'" for v in json.load(sys.stdin)) else "no")'; }

cmd_status() {
    echo "app     : $APP"
    local ids
    ids="$(machine_ids)"
    if [[ -z "$ids" ]]; then
        echo "machines: none — the demo is down"
    else
        flyctl machines list -a "$APP" 2>/dev/null | tail -n +4 | sed 's/^/          /'
    fi
    echo "volume  : $VOLUME $( [[ "$(volume_exists)" == yes ]] && echo present || echo "MISSING — 'up' cannot recreate it" )"
    # curl prints 000 on a connection failure AND exits non-zero, so a "|| echo 000"
    # fallback prints it twice. Let the code stand on its own.
    local code
    code="$(curl -s -o /dev/null -w '%{http_code}' -m 15 "$URL/health" 2>/dev/null)" || true
    echo "$URL   : ${code:-000}$( [[ "$code" == 200 ]] || echo " (not serving)" )"
}

cmd_up() {
    local version="${1:-edge}" image expect=""
    if [[ "$version" == "edge" ]]; then
        image="ghcr.io/nicolasprimeau/artel:edge"
    else
        image="ghcr.io/nicolasprimeau/artel:${version#v}"
        expect="${version#v}"
    fi

    # Check the volume BEFORE deploying. Without it fly fails deep in the deploy with
    # "creating a new machine in group 'app' requires an unattached volume", which
    # reads like a fly outage rather than a missing prerequisite.
    [[ "$(volume_exists)" == yes ]] || die "volume '$VOLUME' does not exist on $APP. The demo's data is gone; create it with 'flyctl volumes create $VOLUME -a $APP --size 3 --region yyz' and expect an empty database."

    echo "deploying $image to $APP…"
    flyctl deploy --app "$APP" --image "$image" --yes --wait-timeout=10m || true

    # Deliberately ignoring flyctl's exit code above. It returns non-zero on a
    # health-check wait timeout that the deploy survived, and — worse — returns zero
    # the moment the API accepts the release, before the new image is actually
    # serving. Neither answers "is the demo up", so ask the demo.
    echo "verifying $URL…"
    for _ in $(seq 1 30); do
        local health live
        health="$(curl -fsS -m 10 "$URL/health" 2>/dev/null || true)"
        live="$(curl -fsS -m 10 "$URL/openapi.json" 2>/dev/null | python3 -c 'import sys,json; print(json.load(sys.stdin).get("info",{}).get("version",""))' 2>/dev/null || true)"
        if [[ "$health" == *'"status":"ok"'* ]] && { [[ -z "$expect" ]] || [[ "$live" == "$expect" ]]; }; then
            # /health comes up a moment before /openapi.json, so re-read the version
            # rather than reporting the empty string that satisfied an unpinned deploy.
            [[ -n "$live" ]] || live="$(curl -fsS -m 10 "$URL/openapi.json" 2>/dev/null | python3 -c 'import sys,json; print(json.load(sys.stdin).get("info",{}).get("version",""))' 2>/dev/null || true)"
            echo "demo is up at $URL (version ${live:-unknown})"
            return 0
        fi
        sleep 10
    done
    die "deploy finished but $URL never reported healthy${expect:+ on version $expect}. Check 'flyctl logs -a $APP'."
}

cmd_down() {
    local ids
    ids="$(machine_ids)"
    if [[ -z "$ids" ]]; then
        echo "no machines on $APP — already down"
    else
        for id in $ids; do
            echo "destroying machine $id…"
            flyctl machine destroy "$id" -a "$APP" --force
        done
    fi

    # Destroying a machine detaches its volume but never deletes it, and this script
    # never deletes one either — the demo's database is the only copy of whatever was
    # written to it. Assert that rather than trust it: a silent volume loss is only
    # discovered on the next 'up', when it is far too late.
    [[ "$(volume_exists)" == yes ]] || die "machines are gone but volume '$VOLUME' is NOT present — the demo's data may have been lost. Do not run 'up' until this is understood."
    echo "demo is down; volume '$VOLUME' preserved (bring it back with '$0 up')"
}

case "${1:-status}" in
    up)     shift; cmd_up "$@" ;;
    down)   cmd_down ;;
    status) cmd_status ;;
    *)      die "usage: $0 {up [version]|down|status}" ;;
esac
