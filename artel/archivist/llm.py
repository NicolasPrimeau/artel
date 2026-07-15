import asyncio
import os

from .config import settings

_anthropic_client = None
_openai_client = None

# Hard ceiling on any single LLM call. The claude-sdk provider spawns a `claude`
# subprocess that can hang (e.g. when the Max plan is rate-limited); a plain
# asyncio.wait_for around a caller can't kill it, so we bound the call here and
# terminate the subprocess on timeout. Keeps a stalled model call from wedging the
# whole archivist cycle.
_LLM_TIMEOUT = 180.0
_CLEANUP_TIMEOUT = 10.0


def _api_key() -> str:
    if settings.archivist_api_key:
        return settings.archivist_api_key
    if settings.archivist_provider == "anthropic":
        return settings.anthropic_api_key
    if settings.archivist_provider == "claude-sdk":
        return os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "")
    return ""


def _default_model() -> str:
    if settings.archivist_provider == "anthropic":
        return "claude-sonnet-4-6"
    if settings.archivist_provider == "claude-sdk":
        return "haiku"
    return "gpt-4o"


def is_configured() -> bool:
    return bool(_api_key())


async def _claude_sdk(system: str, user: str, model: str) -> str:
    from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

    opts = ClaudeAgentOptions(
        model=model, system_prompt=system, max_turns=1, allowed_tools=[], tools=[]
    )
    agen = query(prompt=user, options=opts)
    result = None
    try:
        async for msg in agen:
            if isinstance(msg, ResultMessage):
                result = msg
    finally:
        # Always close the generator so a cancelled/timed-out call tears down the
        # subprocess instead of leaving it hung. Bounded so cleanup can't wedge either.
        aclose = getattr(agen, "aclose", None)
        if aclose is not None:
            try:
                await asyncio.wait_for(aclose(), timeout=_CLEANUP_TIMEOUT)
            except Exception:
                pass
    if result is None or getattr(result, "is_error", False):
        raise RuntimeError(f"claude-sdk: {getattr(result, 'result', 'no result')}")
    return result.result or ""


async def _anthropic(system: str, user: str, model: str, max_tokens: int, key: str) -> str:
    import anthropic

    global _anthropic_client
    if _anthropic_client is None:
        _anthropic_client = anthropic.AsyncAnthropic(api_key=key)
    msg = await _anthropic_client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    for block in msg.content:
        if getattr(block, "type", None) == "text":
            return block.text or ""
    return ""


async def _openai(system: str, user: str, model: str, max_tokens: int, key: str) -> str:
    import openai

    global _openai_client
    if _openai_client is None:
        kwargs: dict = {"api_key": key}
        if settings.archivist_base_url:
            kwargs["base_url"] = settings.archivist_base_url
        _openai_client = openai.AsyncOpenAI(**kwargs)
    resp = await _openai_client.chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
    )
    return resp.choices[0].message.content or ""


async def complete(
    system: str, user: str, max_tokens: int = 2048, timeout: float = _LLM_TIMEOUT
) -> str:
    model = settings.archivist_model or _default_model()
    key = _api_key()
    if settings.archivist_provider == "claude-sdk":
        coro = _claude_sdk(system, user, model)
    elif settings.archivist_provider == "anthropic":
        coro = _anthropic(system, user, model, max_tokens, key)
    else:
        coro = _openai(system, user, model, max_tokens, key)
    # Hard bound on every provider — a stalled call raises TimeoutError rather than
    # hanging the caller (and the claude-sdk finally tears down its subprocess).
    return await asyncio.wait_for(coro, timeout=timeout)
