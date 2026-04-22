"""Tests for pipeline/direct_api.py — Direct API mode."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nezha.config import AgentConfig, EngineConfig, ExecutorConfig, SessionConfig
from nezha.pipeline.direct_api import _call_anthropic, _call_openai, run_direct_api


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_executor_config(prompts_dir: str) -> ExecutorConfig:
    cfg = ExecutorConfig()
    cfg.prompts_dir = prompts_dir
    return cfg


def make_agent_config(api_type: str = "anthropic", model: str = "test-model") -> AgentConfig:
    cfg = AgentConfig()
    cfg.agent.name = "test-agent"
    cfg.engine.api_type = api_type
    cfg.engine.model = model
    cfg.session.prompts = {"worker": "worker.md"}
    return cfg


# ---------------------------------------------------------------------------
# _call_anthropic
# ---------------------------------------------------------------------------

def _make_anthropic_module(fake_client_cls):
    """Build a fake anthropic module with the given Anthropic class."""
    mod = MagicMock()
    mod.Anthropic = fake_client_cls
    return mod


class TestCallAnthropic:
    @pytest.mark.asyncio
    async def test_success_returns_text(self):
        """Returns text content from Anthropic response."""
        mock_block = MagicMock()
        mock_block.text = "Feature list output"
        mock_resp = MagicMock()
        mock_resp.content = [mock_block]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_resp

        fake_mod = _make_anthropic_module(MagicMock(return_value=mock_client))

        with patch.dict("sys.modules", {"anthropic": fake_mod}):
            result = await _call_anthropic("Hello", "claude-test", {})

        assert result == "Feature list output"
        mock_client.messages.create.assert_called_once_with(
            model="claude-test",
            max_tokens=8192,
            messages=[{"role": "user", "content": "Hello"}],
        )

    @pytest.mark.asyncio
    async def test_uses_api_key_from_env(self):
        """Picks up ANTHROPIC_API_KEY from env dict."""
        mock_block = MagicMock()
        mock_block.text = "ok"
        mock_resp = MagicMock()
        mock_resp.content = [mock_block]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_resp

        captured = {}

        def fake_anthropic_cls(**kwargs):
            captured.update(kwargs)
            return mock_client

        fake_mod = _make_anthropic_module(fake_anthropic_cls)

        with patch.dict("sys.modules", {"anthropic": fake_mod}):
            await _call_anthropic("prompt", "model", {"ANTHROPIC_API_KEY": "sk-test"})

        assert captured.get("api_key") == "sk-test"

    @pytest.mark.asyncio
    async def test_base_url_passed_when_set(self):
        """Passes base_url to Anthropic client when env var is set."""
        mock_block = MagicMock()
        mock_block.text = "ok"
        mock_resp = MagicMock()
        mock_resp.content = [mock_block]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_resp

        captured = {}

        def fake_anthropic_cls(**kwargs):
            captured.update(kwargs)
            return mock_client

        fake_mod = _make_anthropic_module(fake_anthropic_cls)

        with patch.dict("sys.modules", {"anthropic": fake_mod}):
            await _call_anthropic(
                "prompt", "model",
                {"ANTHROPIC_BASE_URL": "https://api.minimaxi.com/anthropic"}
            )

        assert captured.get("base_url") == "https://api.minimaxi.com/anthropic"

    @pytest.mark.asyncio
    async def test_no_base_url_when_empty(self):
        """Does not pass base_url when env var is empty."""
        mock_block = MagicMock()
        mock_block.text = "ok"
        mock_resp = MagicMock()
        mock_resp.content = [mock_block]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_resp

        captured = {}

        def fake_anthropic_cls(**kwargs):
            captured.update(kwargs)
            return mock_client

        fake_mod = _make_anthropic_module(fake_anthropic_cls)

        with patch.dict("sys.modules", {"anthropic": fake_mod}):
            await _call_anthropic("prompt", "model", {})

        assert "base_url" not in captured


# ---------------------------------------------------------------------------
# _call_openai
# ---------------------------------------------------------------------------

class TestCallOpenai:
    @pytest.mark.asyncio
    async def test_raises_import_error_when_not_installed(self):
        """Raises ImportError with install instructions when openai is missing."""
        with patch.dict("sys.modules", {"openai": None}):
            with pytest.raises(ImportError, match="pip install openai"):
                await _call_openai("prompt", "model", {})

    @pytest.mark.asyncio
    async def test_success_returns_text(self):
        """Returns message content from OpenAI response."""
        mock_choice = MagicMock()
        mock_choice.message.content = "OpenAI result"
        mock_resp = MagicMock()
        mock_resp.choices = [mock_choice]

        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_resp)

        fake_openai_module = MagicMock()
        fake_openai_module.AsyncOpenAI = MagicMock(return_value=mock_client)

        with patch.dict("sys.modules", {"openai": fake_openai_module}):
            result = await _call_openai("prompt", "gpt-model", {"OPENAI_API_KEY": "sk-x"})

        assert result == "OpenAI result"

    @pytest.mark.asyncio
    async def test_empty_content_returns_empty_string(self):
        """Returns empty string when response content is None."""
        mock_choice = MagicMock()
        mock_choice.message.content = None
        mock_resp = MagicMock()
        mock_resp.choices = [mock_choice]

        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_resp)

        fake_openai_module = MagicMock()
        fake_openai_module.AsyncOpenAI = MagicMock(return_value=mock_client)

        with patch.dict("sys.modules", {"openai": fake_openai_module}):
            result = await _call_openai("prompt", "gpt-model", {})

        assert result == ""


# ---------------------------------------------------------------------------
# run_direct_api
# ---------------------------------------------------------------------------

class TestRunDirectApi:
    @pytest.mark.asyncio
    async def test_anthropic_mode_success(self, tmp_path):
        """run_direct_api with api_type='anthropic' returns completed SessionResult."""
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "worker.md").write_text("You are a planner. Input: {{input_files}}")
        workspace = tmp_path / "ws"

        executor_cfg = make_executor_config(str(prompts_dir))
        agent_cfg = make_agent_config(api_type="anthropic")

        with patch(
            "nezha.pipeline.direct_api._call_anthropic",
            new=AsyncMock(return_value="feature_list output"),
        ):
            result = await run_direct_api(executor_cfg, agent_cfg, workspace)

        assert result.status == "completed"
        assert result.num_turns == 1
        assert "feature_list" in result.result_text
        assert (workspace / "direct_api_response.md").exists()

    @pytest.mark.asyncio
    async def test_openai_mode_success(self, tmp_path):
        """run_direct_api with api_type='openai' returns completed SessionResult."""
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "worker.md").write_text("Plan: {{input_files}}")
        workspace = tmp_path / "ws"

        executor_cfg = make_executor_config(str(prompts_dir))
        agent_cfg = make_agent_config(api_type="openai")

        with patch(
            "nezha.pipeline.direct_api._call_openai",
            new=AsyncMock(return_value="openai plan output"),
        ):
            result = await run_direct_api(executor_cfg, agent_cfg, workspace)

        assert result.status == "completed"
        assert "openai plan" in result.result_text

    @pytest.mark.asyncio
    async def test_unknown_api_type_returns_error(self, tmp_path):
        """Unknown api_type raises ValueError, returned as SessionResult error."""
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "worker.md").write_text("prompt")
        workspace = tmp_path / "ws"

        executor_cfg = make_executor_config(str(prompts_dir))
        agent_cfg = make_agent_config(api_type="unknown_protocol")

        result = await run_direct_api(executor_cfg, agent_cfg, workspace)

        assert result.status == "error"
        assert "unknown_protocol" in result.error.lower() or "Unknown" in result.error

    @pytest.mark.asyncio
    async def test_missing_prompt_raises_value_error(self, tmp_path):
        """ValueError raised when no prompt is configured for the agent."""
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        workspace = tmp_path / "ws"

        executor_cfg = make_executor_config(str(prompts_dir))
        agent_cfg = make_agent_config()
        agent_cfg.session.prompts = {}  # no prompts configured

        with pytest.raises(ValueError, match="no prompt configured"):
            await run_direct_api(executor_cfg, agent_cfg, workspace)

    @pytest.mark.asyncio
    async def test_response_written_to_file(self, tmp_path):
        """Response text is written to direct_api_response.md in workspace."""
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "worker.md").write_text("prompt")
        workspace = tmp_path / "ws"

        executor_cfg = make_executor_config(str(prompts_dir))
        agent_cfg = make_agent_config()

        with patch(
            "nezha.pipeline.direct_api._call_anthropic",
            new=AsyncMock(return_value="The response content"),
        ):
            await run_direct_api(executor_cfg, agent_cfg, workspace)

        content = (workspace / "direct_api_response.md").read_text()
        assert content == "The response content"

    @pytest.mark.asyncio
    async def test_result_text_truncated_to_500(self, tmp_path):
        """result_text in SessionResult is truncated to 500 chars."""
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "worker.md").write_text("prompt")
        workspace = tmp_path / "ws"

        executor_cfg = make_executor_config(str(prompts_dir))
        agent_cfg = make_agent_config()
        long_response = "x" * 2000

        with patch(
            "nezha.pipeline.direct_api._call_anthropic",
            new=AsyncMock(return_value=long_response),
        ):
            result = await run_direct_api(executor_cfg, agent_cfg, workspace)

        assert len(result.result_text) == 500

    @pytest.mark.asyncio
    async def test_api_error_returns_error_result(self, tmp_path):
        """When LLM call raises, returns SessionResult with status='error'."""
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "worker.md").write_text("prompt")
        workspace = tmp_path / "ws"

        executor_cfg = make_executor_config(str(prompts_dir))
        agent_cfg = make_agent_config()

        with patch(
            "nezha.pipeline.direct_api._call_anthropic",
            new=AsyncMock(side_effect=Exception("API timeout")),
        ):
            result = await run_direct_api(executor_cfg, agent_cfg, workspace)

        assert result.status == "error"
        assert "API timeout" in result.error

    @pytest.mark.asyncio
    async def test_mode_selects_alternate_prompt(self, tmp_path):
        """mode param selects alternate prompt key from session.prompts."""
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "worker.md").write_text("default prompt")
        (prompts_dir / "review.md").write_text("review prompt: {{input_files}}")
        workspace = tmp_path / "ws"

        executor_cfg = make_executor_config(str(prompts_dir))
        agent_cfg = make_agent_config()
        agent_cfg.session.prompts = {"worker": "worker.md", "review": "review.md"}

        captured_prompt = {}

        async def fake_call(prompt, model, env):
            captured_prompt["value"] = prompt
            return "reviewed"

        with patch("nezha.pipeline.direct_api._call_anthropic", new=fake_call):
            await run_direct_api(executor_cfg, agent_cfg, workspace, mode="review")

        assert "review prompt" in captured_prompt["value"]


# ---------------------------------------------------------------------------
# Executor branching: session.mode == "direct"
# ---------------------------------------------------------------------------

class TestExecutorDirectBranch:
    def test_session_mode_in_agent_yaml(self):
        """EngineConfig.api_type and session.mode are loaded from YAML."""
        from nezha.config import load_agent_config
        config_path = Path(__file__).parent.parent / "src" / "nezha" / "templates" / "agents" / "planner-agent.yaml"
        if not config_path.exists():
            pytest.skip("planner-agent.yaml not found")

        config = load_agent_config(config_path)
        assert config.session.mode in ("direct", "single_round")
        assert config.engine.api_type == "anthropic"

    def test_engine_config_default_api_type(self):
        """EngineConfig defaults to api_type='anthropic'."""
        cfg = EngineConfig()
        assert cfg.api_type == "anthropic"
