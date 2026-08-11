#!/usr/bin/env python3
"""Run SWE-bench instances through Claude Code with Artel on and off.

The only variable between arms is whether the Artel plugin is active. Same model,
same prompt, same instances, same order, interleaved in time so a model-side
change cannot land on one arm.

    uv run python bench/run.py --instances bench/instances.json --arm both \\
        --model sonnet --out bench/runs/pilot

Writes, per arm, a SWE-bench predictions file plus a metrics row per instance.
Evaluate the predictions with the official harness (see bench/README.md) — this
script deliberately does not score patches itself.

THE PLACEBO GATE
An Artel plugin with bad credentials returns nothing, logs nothing and injects
nothing, while still costing its latency. A treatment arm in that state is a
placebo and produces a clean, meaningless null. So before the run and before
every treatment instance this script checks that Artel AUTHENTICATES, and aborts
if it stops doing so.

It does not gate on how much the plugin injects. An isolated run starts with an
empty project and legitimately injects nothing early on; failing that would abort
a valid run. Injection is recorded per instance as telemetry instead, and only
--memory fleet — where memory definitely exists — treats zero as a setup error.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ARMS = ("control", "treatment")

PROMPT = """You are fixing a bug in the {repo} repository.

{problem_statement}

Make the minimal source change that resolves this. Do not modify tests. When you
are done, stop — the working tree diff is the answer."""


def sh(cmd: list[str], cwd: Path | None = None, timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False
    )


def repo_cache(workdir: Path, repo: str) -> Path:
    """One clone per repo, reused across its instances."""
    dest = workdir / "repos" / repo.replace("/", "__")
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        print(f"    cloning {repo} (first instance of this repo)", flush=True)
        res = sh(["git", "clone", f"https://github.com/{repo}.git", str(dest)], timeout=1800)
        if res.returncode != 0:
            raise RuntimeError(f"clone failed for {repo}: {res.stderr[-400:]}")
    return dest


def checkout(repo_dir: Path, commit: str) -> None:
    sh(["git", "fetch", "--all", "--quiet"], cwd=repo_dir, timeout=1800)
    for cmd in (
        ["git", "reset", "--hard", "--quiet"],
        ["git", "clean", "-fdq"],
        ["git", "checkout", "--quiet", commit],
    ):
        res = sh(cmd, cwd=repo_dir, timeout=600)
        if res.returncode != 0 and "checkout" in cmd:
            raise RuntimeError(f"checkout {commit} failed: {res.stderr[-300:]}")


def artel_reachable(env: dict) -> tuple[bool, str]:
    """Can the plugin authenticate at all?

    This is the placebo check, and it is deliberately separate from how much the
    plugin injects. An isolated run starts with an empty project and legitimately
    injects nothing on early instances — treating that as failure would abort a
    perfectly valid run. Broken credentials are a different thing entirely, and
    only an auth check distinguishes them.
    """
    url, agent, key = (
        env.get("ARTEL_URL"),
        env.get("ARTEL_AGENT_ID"),
        env.get("ARTEL_API_KEY"),
    )
    if not (url and agent and key):
        return False, "ARTEL_URL / ARTEL_AGENT_ID / ARTEL_API_KEY not all set"
    import urllib.error
    import urllib.request

    req = urllib.request.Request(
        f"{url.rstrip('/')}/memory/search?q=probe&limit=1",
        headers={"x-agent-id": agent, "x-api-key": key},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status == 200, f"HTTP {resp.status}"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code} — credentials rejected"
    except Exception as e:
        return False, f"unreachable: {type(e).__name__}"


def probe_injection(env: dict) -> int:
    """Tokens a recall probe injects. Telemetry, not a gate — zero is legal."""
    payload = json.dumps(
        {
            "hook_event_name": "UserPromptSubmit",
            "session_id": f"bench-probe-{time.time()}",
            "cwd": str(ROOT),
            "prompt": "fix the failing test in the authentication middleware",
        }
    )
    res = subprocess.run(
        [str(ROOT / "scripts" / "artel-recall.sh")],
        input=payload,
        capture_output=True,
        text=True,
        env={**env, "CLAUDE_PLUGIN_ROOT": str(ROOT)},
        timeout=120,
    )
    return round(len(res.stdout) / 4)


def prepare_config_dir(arm: str, root: Path) -> Path:
    """Seed an arm's CLAUDE_CONFIG_DIR.

    A fresh config dir has no credentials and the CLI exits with "Not logged in",
    which looks exactly like a fast empty patch. So both arms inherit the real
    login and settings, and differ ONLY in whether the Artel plugin is installed.
    """
    dest = root / arm
    dest.mkdir(parents=True, exist_ok=True)
    source = Path.home() / ".claude"

    for name in (".credentials.json", "settings.json"):
        src = source / name
        if src.exists():
            shutil.copy2(src, dest / name)

    plugins = dest / "plugins"
    if plugins.exists():
        shutil.rmtree(plugins)
    if arm == "treatment":
        if (source / "plugins").exists():
            shutil.copytree(source / "plugins", plugins, symlinks=True)
    else:
        # Control gets an explicitly empty registry rather than an absent one, so
        # the CLI cannot fall back to a user-level install.
        plugins.mkdir(parents=True, exist_ok=True)
        (plugins / "installed_plugins.json").write_text(json.dumps({"version": 2, "plugins": {}}))
    return dest


def verify_arm(arm: str, env: dict, cwd: Path) -> tuple[bool, str]:
    """One trivial call per arm before spending a run on it."""
    proc = subprocess.run(
        [
            "claude",
            "-p",
            "reply with the single word READY",
            "--model",
            "sonnet",
            "--output-format",
            "json",
            "--dangerously-skip-permissions",
        ],
        cwd=cwd,
        capture_output=True,
        text=True,
        env=env,
        timeout=300,
        check=False,
    )
    try:
        payload = json.loads(proc.stdout)
    except (ValueError, TypeError):
        return False, f"unparseable CLI output: {proc.stdout[:120]}"
    if payload.get("is_error"):
        return False, str(payload.get("result"))[:160]
    return True, str(payload.get("result", ""))[:60]


def arm_env(arm: str, project: str, base: dict) -> dict:
    env = dict(base)
    env["CLAUDE_CONFIG_DIR"] = str(Path(env["BENCH_CONFIG_ROOT"]) / arm)
    if arm == "control":
        # Not merely idle — absent. No credentials means no hook can do anything,
        # and the config dir for this arm has no plugin installed.
        for key in ("ARTEL_URL", "ARTEL_AGENT_ID", "ARTEL_API_KEY", "MCP_PROJECT"):
            env.pop(key, None)
    else:
        env["MCP_PROJECT"] = project
    return env


def run_instance(inst: dict, arm: str, workdir: Path, env: dict, model: str, timeout: int) -> dict:
    repo_dir = repo_cache(workdir, inst["repo"])
    checkout(repo_dir, inst["base_commit"])

    prompt = PROMPT.format(repo=inst["repo"], problem_statement=inst["problem_statement"])
    started = time.time()
    proc = subprocess.run(
        [
            "claude",
            "-p",
            prompt,
            "--model",
            model,
            "--output-format",
            "json",
            "--dangerously-skip-permissions",
        ],
        cwd=repo_dir,
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
        check=False,
    )
    elapsed = time.time() - started

    result: dict = {}
    try:
        result = json.loads(proc.stdout)
    except (ValueError, TypeError):
        pass

    diff = sh(["git", "diff"], cwd=repo_dir, timeout=300).stdout

    return {
        "instance_id": inst["instance_id"],
        "repo": inst["repo"],
        "position_in_repo": inst["position_in_repo"],
        "arm": arm,
        "patch": diff,
        "empty_patch": not diff.strip(),
        "wall_seconds": round(elapsed, 1),
        "cli_exit": proc.returncode,
        "num_turns": result.get("num_turns"),
        "cost_usd": result.get("total_cost_usd"),
        "usage": result.get("usage"),
        "stderr_tail": proc.stderr[-400:] if proc.returncode != 0 else "",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instances", default="bench/instances.json")
    parser.add_argument("--arm", choices=(*ARMS, "both"), default="both")
    parser.add_argument("--model", default="sonnet")
    parser.add_argument("--out", default="bench/runs/pilot")
    parser.add_argument("--limit", type=int, default=0, help="first N instances (smoke test)")
    parser.add_argument("--timeout", type=int, default=1800, help="per-instance seconds")
    parser.add_argument(
        "--memory",
        choices=("isolated", "fleet"),
        default="isolated",
        help="isolated: a fresh project, so only benchmark-accumulated memory exists."
        " fleet: point at the real store — irrelevant memory, zero possible benefit,"
        " which is the adversarial overhead condition.",
    )
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    workdir = out / "work"
    workdir.mkdir(exist_ok=True)

    instances = json.loads(Path(args.instances).read_text())
    if args.limit:
        instances = instances[: args.limit]

    run_id = time.strftime("%Y%m%d-%H%M%S")
    project = (
        os.environ.get("MCP_PROJECT", "artel") if args.memory == "fleet" else f"bench-{run_id}"
    )

    base_env = dict(os.environ)
    config_root = out / "config"
    base_env["BENCH_CONFIG_ROOT"] = str(config_root)
    arms = ARMS if args.arm == "both" else (args.arm,)
    for arm in arms:
        prepare_config_dir(arm, config_root)

    treatment_env = arm_env("treatment", project, base_env)
    if "treatment" in arms:
        reachable, detail = artel_reachable(treatment_env)
        injected = probe_injection(treatment_env) if reachable else 0
        print(
            f"pre-flight: artel {'OK' if reachable else 'FAILED'} ({detail});"
            f" recall probe injects {injected} tokens"
        )
        if not reachable:
            print(
                "\nREFUSING TO RUN: the plugin cannot authenticate, so the treatment arm\n"
                "would be a placebo and any result a meaningless null. Fix credentials\n"
                "and try again.",
                file=sys.stderr,
            )
            return 1
        if injected == 0 and args.memory == "fleet":
            print(
                "\nREFUSING TO RUN: --memory fleet should surface existing memory, but the\n"
                "probe injected nothing. Check MCP_PROJECT scoping before spending a run.",
                file=sys.stderr,
            )
            return 1
        if injected == 0:
            print("  (empty project — expected for --memory isolated; it fills as the run goes)")

    for arm in arms:
        ok, detail = verify_arm(arm, arm_env(arm, project, base_env), ROOT)
        print(f"pre-flight: {arm} CLI {'OK' if ok else 'FAILED'} — {detail}")
        if not ok:
            print(
                f"\nREFUSING TO RUN: the {arm} arm's CLI is not usable, so every instance"
                "\nwould return an empty patch that looks like a failed fix rather than a"
                "\nbroken harness.",
                file=sys.stderr,
            )
            return 1

    print(f"run {run_id}: {len(instances)} instances x {len(arms)} arm(s), model={args.model}")
    print(f"memory mode: {args.memory} (project={project})\n")

    metrics_path = out / f"metrics-{run_id}.jsonl"
    inert_streak = 0
    with metrics_path.open("w") as metrics_fh:
        for i, inst in enumerate(instances, 1):
            for arm in arms:
                env = arm_env(arm, project, base_env)
                injected = None
                if arm == "treatment":
                    reachable, detail = artel_reachable(env)
                    if not reachable:
                        inert_streak += 1
                        print(f"      artel unreachable ({detail})", file=sys.stderr)
                        if inert_streak >= 3:
                            print(
                                "\nABORTING: artel unreachable for three consecutive instances."
                                " The arm has become a placebo.",
                                file=sys.stderr,
                            )
                            return 1
                    else:
                        inert_streak = 0
                    injected = probe_injection(env) if reachable else 0
                print(f"[{i}/{len(instances)}] {inst['instance_id']} :: {arm}", flush=True)
                try:
                    row = run_instance(inst, arm, workdir, env, args.model, args.timeout)
                except subprocess.TimeoutExpired:
                    row = {
                        "instance_id": inst["instance_id"],
                        "repo": inst["repo"],
                        "position_in_repo": inst["position_in_repo"],
                        "arm": arm,
                        "patch": "",
                        "empty_patch": True,
                        "timeout": True,
                    }
                except Exception as e:  # a broken instance must not kill the run
                    row = {
                        "instance_id": inst["instance_id"],
                        "arm": arm,
                        "error": str(e)[:300],
                        "patch": "",
                        "empty_patch": True,
                    }
                row["run_id"] = run_id
                row["model"] = args.model
                row["memory_mode"] = args.memory
                row["injected_tokens"] = injected
                metrics_fh.write(json.dumps(row) + "\n")
                metrics_fh.flush()
                status = "empty" if row.get("empty_patch") else f"{len(row['patch'])}B patch"
                print(f"      -> {status}, {row.get('wall_seconds', '?')}s", flush=True)

    for arm in arms:
        preds = []
        for line in metrics_path.read_text().splitlines():
            row = json.loads(line)
            if row["arm"] != arm:
                continue
            preds.append(
                {
                    "instance_id": row["instance_id"],
                    "model_name_or_path": f"claude-code-{args.model}-{arm}",
                    "model_patch": row.get("patch", ""),
                }
            )
        path = out / f"preds-{arm}-{run_id}.jsonl"
        path.write_text("\n".join(json.dumps(p) for p in preds) + "\n")
        print(f"wrote {len(preds)} predictions -> {path}")

    print(f"\nmetrics -> {metrics_path}")
    print("evaluate with the official harness, then: uv run python bench/analyze.py")
    return 0


if __name__ == "__main__":
    if shutil.which("claude") is None:
        sys.exit("claude CLI not found on PATH")
    raise SystemExit(main())
