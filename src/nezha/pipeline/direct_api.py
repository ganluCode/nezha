"""Direct API mode: call LLM directly without Claude Code SDK subprocess.

Supports two API protocols (same pattern as nl2cypher.py in code-analysis-mcp):
  anthropic : Anthropic SDK — official api.anthropic.com or any compatible endpoint
              (env: ANTHROPIC_API_KEY, ANTHROPIC_BASE_URL)
  openai    : OpenAI-compatible SDK — MiniMax, DeepSeek, Moonshot, etc.
              (env: OPENAI_API_KEY, OPENAI_BASE_URL)

Suitable for single-round planning/management agents that only need
prompt→text output (no tool calls). Input files are injected into the prompt.
"""

import time
from pathlib import Path

from nezha.config import AgentConfig, ExecutorConfig
from nezha.engine import SessionResult
from nezha.pipeline.io import (
    build_input_context,
    ensure_output_dir,
    scan_input_files,
)
from nezha.pipeline.knowledge import (
    load_agent_context,
    load_knowledge,
    load_project_context,
)
from nezha.pipeline.prompt_template import load_and_render, resolve_prompt_path


async def run_direct_api(
    executor_config: ExecutorConfig,
    agent_config: AgentConfig,
    workspace: Path,
    env: dict[str, str] | None = None,
    target: Path | None = None,
    project_dir: Path | None = None,
    agent_workspace: Path | None = None,
    mode: str | None = None,
) -> SessionResult:
    """Run a single-round agent session via direct LLM API (no Claude Code SDK).

    Args:
        executor_config: Executor configuration
        agent_config: Agent configuration (engine.api_type selects the protocol)
        workspace: Metadata workspace path
        env: Merged environment variables (executor + agent)
        target: Optional code repository path (used as project_name and for CLAUDE.md)
        project_dir: Optional project-level shared knowledge directory
        agent_workspace: Agent workspace root for agent-context.md
        mode: Optional execution mode — selects alternate prompt key
    """
    cwd = target if target else workspace
    workspace.mkdir(parents=True, exist_ok=True)
    ensure_output_dir(agent_config, workspace)

    # ------------------------------------------------------------------
    # Build prompt (identical logic to run_single_round)
    # ------------------------------------------------------------------
    prompts_dir = Path(executor_config.prompts_dir)
    prompt_key = mode if (mode and agent_config.session.prompts.get(mode)) else "worker"
    worker_prompt_path = agent_config.session.prompts.get(prompt_key, "")
    if not worker_prompt_path:
        raise ValueError(
            f"Agent {agent_config.agent.name}: no prompt configured"
            + (f" for mode '{mode}'" if mode else "")
        )

    template_path = resolve_prompt_path(prompts_dir, worker_prompt_path)
    input_files = scan_input_files(agent_config, workspace)
    variables = {
        "workspace": str(workspace),
        "project_name": cwd.name,
        "input_files": build_input_context(input_files, workspace),
        "project_dir": str(project_dir) if project_dir else "",
    }
    prompt = load_and_render(template_path, variables)

    # Knowledge injection (same priority order as run_single_round)
    knowledge = load_knowledge(cwd)
    if knowledge:
        prompt = knowledge + "\n\n" + prompt

    _agent_ws = agent_workspace if agent_workspace else workspace
    agent_ctx = load_agent_context(_agent_ws)
    if agent_ctx:
        prompt = agent_ctx + "\n\n" + prompt

    if project_dir:
        project_context = load_project_context(project_dir)
        if project_context:
            prompt = project_context + "\n\n" + prompt

    # ------------------------------------------------------------------
    # Resolve credentials from merged env (executor + agent)
    # ------------------------------------------------------------------
    merged_env = {**(env or {}), **(agent_config.engine.env or {})}
    api_type = agent_config.engine.api_type.lower()
    model = agent_config.engine.model

    print(f"[direct_api] {agent_config.agent.name} | api_type={api_type} | model={model}")
    print(f"[direct_api] Workspace: {workspace}")

    start_ms = int(time.time() * 1000)

    try:
        if api_type == "anthropic":
            result_text = await _call_anthropic(prompt, model, merged_env)
        elif api_type == "openai":
            result_text = await _call_openai(prompt, model, merged_env)
        else:
            raise ValueError(
                f"Unknown api_type '{api_type}'. Expected 'anthropic' or 'openai'."
            )
    except Exception as e:
        duration_ms = int(time.time() * 1000) - start_ms
        print(f"[direct_api] ERROR: {e}")
        return SessionResult(
            status="error",
            duration_ms=duration_ms,
            error=str(e),
        )

    duration_ms = int(time.time() * 1000) - start_ms
    print(f"\n[direct_api] Done in {duration_ms}ms")

    # Write response to workspace as agent output
    output_path = workspace / "direct_api_response.md"
    output_path.write_text(result_text, encoding="utf-8")
    print(f"[direct_api] Response written to {output_path}")

    # ------------------------------------------------------------------
    # Extract artifacts from response (JSON in markdown code blocks)
    # ------------------------------------------------------------------
    artifacts_generated = _extract_and_write_artifacts(
        result_text, agent_config, workspace
    )
    if artifacts_generated:
        print(f"[direct_api] Artifacts generated: {artifacts_generated}")

    return SessionResult(
        status="completed",
        duration_ms=duration_ms,
        num_turns=1,
        result_text=result_text[:500],
    )


# ---------------------------------------------------------------------------
# Artifact extraction helpers
# ---------------------------------------------------------------------------

import re
import json


def _try_fix_json(text: str) -> str:
    """Try to fix common JSON formatting issues from LLM output.

    Handles:
    - Trailing commas before } or ]
    - Unescaped double quotes inside JSON string values
    - JavaScript-style comments
    """
    # 1. Remove single-line comments (// ...)
    text = re.sub(r'//[^\n]*', '', text)
    # 2. Remove multi-line comments (/* ... */)
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.DOTALL)
    # 3. Remove trailing commas before ] or }
    text = re.sub(r",\s*([}\]])", r"\1", text)
    # 4. Fix unescaped double quotes inside string values.
    text = _fix_unescaped_quotes(text)
    return text


def _fix_unescaped_quotes(text: str) -> str:
    """Fix unescaped double quotes inside JSON string values.

    Uses an error-driven iterative approach:
    1. Try to parse JSON
    2. On error, find the " that prematurely ended a string value
    3. Replace it with a Chinese bracket quote「or」
    4. Repeat until valid or unfixable
    """
    try:
        json.loads(text)
        return text
    except json.JSONDecodeError:
        pass

    use_open = True  # alternate 「 and 」
    max_attempts = 100  # safety limit

    for _ in range(max_attempts):
        try:
            json.loads(text)
            return text
        except json.JSONDecodeError as e:
            # The parser hit unexpected content after what it thought was
            # a complete string. The " just before the error position
            # is the inner quote that prematurely ended the string.
            pos = e.pos
            # Scan backwards from error position to find the problematic "
            i = pos - 1
            while i >= 0 and text[i] in ' \t\n\r':
                i -= 1
            if i < 0 or text[i] != '"':
                break  # Can't fix this error type

            # Safety check: the content after this " should look like
            # unescaped inner text, not a structural issue (missing comma).
            after_content = text[pos:].lstrip()
            if after_content and after_content[0] in ':}]':
                break  # Structural issue, not an unescaped quote

            replacement = '「' if use_open else '」'
            text = text[:i] + replacement + text[i + 1:]
            use_open = not use_open

    return text


def _extract_and_write_artifacts(
    result_text: str,
    agent_config: AgentConfig,
    workspace: Path,
) -> list[str]:
    """Extract JSON artifacts from response text and write to configured paths.

    Looks for JSON in markdown code blocks (```json ... ```) and writes them
    to the artifact paths configured in agent_config.artifacts.

    Returns list of artifact names that were successfully generated.
    """
    if not agent_config.artifacts:
        return []

    generated = []

    # Find all JSON code blocks in the response
    json_blocks = re.findall(
        r"```(?:json)?\s*\n([\s\S]*?)\n```", result_text, re.IGNORECASE
    )

    if not json_blocks:
        print("[direct_api] No JSON code blocks found in response")
        return []

    print(f"[direct_api] Found {len(json_blocks)} JSON code block(s)")

    for artifact in agent_config.artifacts:
        artifact_name = artifact.name
        artifact_path = Path(artifact.path)

        # Resolve relative to workspace if not absolute
        if not artifact_path.is_absolute():
            artifact_path = workspace / artifact_path

        # Try to find matching JSON content
        for i, block in enumerate(json_blocks):
            try:
                # Try to parse as-is first
                try:
                    parsed = json.loads(block.strip())
                except json.JSONDecodeError:
                    # Try to fix common issues
                    fixed_block = _try_fix_json(block.strip())
                    parsed = json.loads(fixed_block)

                # For feature_list, check if it looks like a feature list
                if artifact_name == "feature_list":
                    if isinstance(parsed, list) and len(parsed) > 0:
                        first_item = parsed[0]
                        if isinstance(first_item, dict) and (
                            "id" in first_item or "description" in first_item
                        ):
                            artifact_path.parent.mkdir(parents=True, exist_ok=True)
                            artifact_path.write_text(
                                json.dumps(parsed, ensure_ascii=False, indent=2),
                                encoding="utf-8",
                            )
                            print(
                                f"[direct_api] Extracted {artifact_name} "
                                f"({len(parsed)} items) -> {artifact_path}"
                            )
                            generated.append(artifact_name)
                            break
                else:
                    # For other artifacts, just write valid JSON
                    artifact_path.parent.mkdir(parents=True, exist_ok=True)
                    artifact_path.write_text(
                        json.dumps(parsed, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    print(
                        f"[direct_api] Extracted {artifact_name} -> {artifact_path}"
                    )
                    generated.append(artifact_name)
                    break
            except json.JSONDecodeError as e:
                print(f"[direct_api] Block {i+1} JSON parse error: {e}")
                continue

    return generated


# ---------------------------------------------------------------------------
# Protocol implementations
# ---------------------------------------------------------------------------

async def _call_anthropic(prompt: str, model: str, env: dict) -> str:
    """Call via Anthropic SDK (sync wrapped in asyncio)."""
    import asyncio
    from anthropic import Anthropic

    api_key = env.get("ANTHROPIC_API_KEY") or None
    base_url = env.get("ANTHROPIC_BASE_URL") or None  # None = official endpoint

    client = Anthropic(
        api_key=api_key,
        **({"base_url": base_url} if base_url else {}),
    )

    def _sync_call():
        resp = client.messages.create(
            model=model,
            max_tokens=8192,
            messages=[{"role": "user", "content": prompt}],
        )
        return next(
            block.text for block in resp.content if hasattr(block, "text")
        )

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _sync_call)


async def _call_openai(prompt: str, model: str, env: dict) -> str:
    """Call via OpenAI-compatible SDK."""
    try:
        from openai import AsyncOpenAI
    except ImportError:
        raise ImportError(
            "openai package is required for api_type='openai'. "
            "Install it with: pip install openai"
        )

    api_key = env.get("OPENAI_API_KEY") or "sk-placeholder"
    base_url = env.get("OPENAI_BASE_URL") or None

    client = AsyncOpenAI(
        api_key=api_key,
        **({"base_url": base_url} if base_url else {}),
    )

    resp = await client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
    )
    return resp.choices[0].message.content or ""
