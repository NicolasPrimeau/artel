#!/usr/bin/env bash
# Artel drainer: compress spooled session slices and ship them to /captures, off the
# agent's hot path. Spawned detached by artel-capture.sh; safe to spawn often because
# the drainer takes an flock and a per-session cursor, so concurrent spawns and already
# shipped content are no-ops. Config-gated inside the module.
exec python3 "$(dirname "$0")/_artel_hooks.py" drain
