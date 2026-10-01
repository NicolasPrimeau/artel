import argparse
import asyncio
import json
import os
import subprocess
import time
from pathlib import Path

import httpx
import openai

WORK = Path.home() / ".cache" / "artel-bakeoff"
MODELS = [
    "google/gemini-3.7-flash",
    "deepseek/deepseek-v4-flash",
    "qwen/qwen3.7-flash",
    "openai/gpt-oss-120b",
    "google/gemini-2.5-flash-lite",
]
BASE_URL = "https://openrouter.ai/api/v1"


def model_info():
    data = httpx.get(f"{BASE_URL}/models", timeout=30).json()["data"]
    return {m["id"]: m for m in data}


def api_key():
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if key:
        return key
    return subprocess.check_output(
        ["docker", "exec", "artel-archivist-1", "printenv", "OPENROUTER_API_KEY"], text=True
    ).strip()


def estimate(prompt, rates):
    tokens_in = (len(prompt["system"]) + len(prompt["user"])) / 3.5
    return tokens_in * rates[0] + prompt["max_tokens"] * rates[1]


REASONING = {
    "low": [{"effort": "low"}],
    "off": [{"enabled": False}, {"effort": "minimal"}, {"effort": "low"}],
    "default": [None],
}


async def call(client, model, prompt, reasoning, sem, mode="low"):
    for setting in REASONING[mode] if reasoning else [None]:
        row = await _call(client, model, prompt, setting, sem)
        if "error" not in row or "400" not in row["error"]:
            row["reasoning_setting"] = setting
            return row
    return row


async def _call(client, model, prompt, setting, sem):
    extra = {"usage": {"include": True}}
    if setting:
        extra["reasoning"] = setting
    async with sem:
        start = time.monotonic()
        try:
            resp = await client.chat.completions.create(
                model=model,
                max_tokens=prompt["max_tokens"],
                messages=[
                    {"role": "system", "content": prompt["system"]},
                    {"role": "user", "content": prompt["user"]},
                ],
                extra_body=extra,
                timeout=180,
            )
        except Exception as e:
            return {"n": prompt["n"], "error": f"{type(e).__name__}: {str(e)[:200]}"}
        usage = resp.usage.model_dump() if resp.usage else {}
        choice = resp.choices[0]
        return {
            "n": prompt["n"],
            "latency": time.monotonic() - start,
            "finish": choice.finish_reason,
            "content": choice.message.content or "",
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "reasoning_tokens": (usage.get("completion_tokens_details") or {}).get(
                "reasoning_tokens"
            ),
            "cost": usage.get("cost"),
        }


async def main(prompts_path, out_path, models, budget, mode):
    prompts = json.loads(prompts_path.read_text())
    info = model_info()
    client = openai.AsyncOpenAI(api_key=api_key(), base_url=BASE_URL)
    results = json.loads(out_path.read_text()) if out_path.exists() else {}
    spent = sum(r.get("cost") or 0 for rows in results.values() for r in rows)
    sem = asyncio.Semaphore(6)
    for model in models:
        key = model if mode == "low" else f"{model}@{mode}"
        if key in results:
            continue
        m = info[model]
        rates = (float(m["pricing"]["prompt"]), float(m["pricing"]["completion"]))
        projected = sum(estimate(p, rates) for p in prompts)
        if spent + projected > budget:
            print(f"skip {model}: projected ${projected:.3f} would pass the ${budget} budget")
            continue
        reasoning = "reasoning" in (m.get("supported_parameters") or [])
        rows = await asyncio.gather(
            *(call(client, model, p, reasoning, sem, mode) for p in prompts)
        )
        results[key] = rows
        cost = sum(r.get("cost") or 0 for r in rows)
        spent += cost
        out_path.write_text(json.dumps(results))
        errors = sum(1 for r in rows if "error" in r)
        print(f"{key}: ${cost:.4f} errors={errors} running total ${spent:.4f}", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompts", default=str(WORK / "prompts-all.json"))
    ap.add_argument("--out", default=str(WORK / "results.json"))
    ap.add_argument("--models", nargs="*", default=MODELS)
    ap.add_argument("--budget", type=float, default=1.0)
    ap.add_argument("--reasoning", choices=sorted(REASONING), default="low")
    a = ap.parse_args()
    asyncio.run(main(Path(a.prompts), Path(a.out), a.models, a.budget, a.reasoning))
