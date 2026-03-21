"""Tests for pipeline/security.py — command allowlist and sensitive-command validation."""

import pytest

from nezha.pipeline.security import (
    _validate_chmod,
    _validate_pkill,
    _validate_rm,
    create_security_hook,
    extract_commands,
)


# ---------------------------------------------------------------------------
# extract_commands
# ---------------------------------------------------------------------------


class TestExtractCommands:
    def test_simple(self):
        assert extract_commands("ls -la") == ["ls"]

    def test_chained_and(self):
        cmds = extract_commands("npm install && npm run build")
        assert "npm" in cmds
        assert cmds.count("npm") == 2

    def test_pipe(self):
        cmds = extract_commands("cat file.txt | grep hello")
        assert "cat" in cmds
        assert "grep" in cmds

    def test_semicolon(self):
        cmds = extract_commands("mkdir dist; cp -r src dist")
        assert "mkdir" in cmds
        assert "cp" in cmds


# ---------------------------------------------------------------------------
# _validate_rm
# ---------------------------------------------------------------------------


class TestValidateRm:
    # --- Allowed cases ---

    def test_allows_relative_file(self):
        ok, reason = _validate_rm("rm src/old-component.tsx")
        assert ok, reason

    def test_allows_relative_with_flags(self):
        ok, reason = _validate_rm("rm -f dist/bundle.js")
        assert ok, reason

    def test_allows_recursive_relative_dir(self):
        ok, reason = _validate_rm("rm -rf dist/")
        assert ok, reason

    def test_allows_glob_in_relative_dir(self):
        ok, reason = _validate_rm("rm src/*.js")
        assert ok, reason

    def test_allows_dist_glob(self):
        ok, reason = _validate_rm("rm -rf dist/*")
        assert ok, reason

    def test_allows_nested_relative(self):
        ok, reason = _validate_rm("rm -rf src/components/Old/")
        assert ok, reason

    def test_allows_multiple_relative_paths(self):
        ok, reason = _validate_rm("rm old.js old2.js")
        assert ok, reason

    # --- Blocked cases: absolute paths ---

    def test_blocks_absolute_path(self):
        ok, _ = _validate_rm("rm /Users/glen/important.txt")
        assert not ok

    def test_blocks_slash_root(self):
        ok, _ = _validate_rm("rm -rf /")
        assert not ok

    def test_blocks_absolute_tmp(self):
        ok, _ = _validate_rm("rm /tmp/myfile")
        assert not ok

    def test_blocks_absolute_usr(self):
        ok, _ = _validate_rm("rm -rf /usr/local/bin/something")
        assert not ok

    # --- Blocked cases: home directory ---

    def test_blocks_tilde(self):
        ok, _ = _validate_rm("rm ~/Documents/file.txt")
        assert not ok

    def test_blocks_home_env_var(self):
        ok, _ = _validate_rm("rm $HOME/file.txt")
        assert not ok

    def test_blocks_home_brace_env_var(self):
        ok, _ = _validate_rm("rm ${HOME}/file.txt")
        assert not ok

    # --- Blocked cases: path traversal ---

    def test_blocks_dotdot_prefix(self):
        ok, _ = _validate_rm("rm ../../etc/passwd")
        assert not ok

    def test_blocks_dotdot_in_path(self):
        ok, _ = _validate_rm("rm src/../../../etc/passwd")
        assert not ok

    # --- Blocked cases: dangerous globs ---

    def test_blocks_bare_star(self):
        ok, _ = _validate_rm("rm -rf *")
        assert not ok

    def test_blocks_bare_doublestar(self):
        ok, _ = _validate_rm("rm **")
        assert not ok

    # --- Error cases ---

    def test_requires_path_argument(self):
        ok, reason = _validate_rm("rm -rf")
        assert not ok
        assert "path" in reason.lower()


# ---------------------------------------------------------------------------
# _validate_chmod (existing, sanity checks)
# ---------------------------------------------------------------------------


class TestValidateChmod:
    def test_allows_plus_x(self):
        ok, _ = _validate_chmod("chmod +x script.sh")
        assert ok

    def test_blocks_minus_flags(self):
        ok, _ = _validate_chmod("chmod -x script.sh")
        assert not ok

    def test_blocks_numeric_mode(self):
        ok, _ = _validate_chmod("chmod 777 secret")
        assert not ok


# ---------------------------------------------------------------------------
# _validate_pkill (existing, sanity checks)
# ---------------------------------------------------------------------------


class TestValidatePkill:
    def test_allows_node(self):
        ok, _ = _validate_pkill("pkill node")
        assert ok

    def test_allows_vite(self):
        ok, _ = _validate_pkill("pkill vite")
        assert ok

    def test_blocks_unknown_process(self):
        ok, _ = _validate_pkill("pkill some-random-process")
        assert not ok


# ---------------------------------------------------------------------------
# create_security_hook — integration
# ---------------------------------------------------------------------------


class TestSecurityHookWithRm:
    """Test that the hook correctly validates rm when it's in the allowlist."""

    def _make_hook(self):
        return create_security_hook(allowed_commands={"rm", "ls", "npm"})

    @pytest.mark.asyncio
    async def test_allows_relative_rm(self):
        hook = self._make_hook()
        result = await hook({"tool_name": "Bash", "tool_input": {"command": "rm -f old.js"}})
        assert result.get("decision") != "block"

    @pytest.mark.asyncio
    async def test_blocks_absolute_rm(self):
        hook = self._make_hook()
        result = await hook({"tool_name": "Bash", "tool_input": {"command": "rm /etc/hosts"}})
        assert result.get("decision") == "block"

    @pytest.mark.asyncio
    async def test_blocks_home_rm(self):
        hook = self._make_hook()
        result = await hook({"tool_name": "Bash", "tool_input": {"command": "rm ~/Documents/file"}})
        assert result.get("decision") == "block"

    @pytest.mark.asyncio
    async def test_blocks_bare_star_rm(self):
        hook = self._make_hook()
        result = await hook({"tool_name": "Bash", "tool_input": {"command": "rm -rf *"}})
        assert result.get("decision") == "block"

    @pytest.mark.asyncio
    async def test_rm_not_in_allowlist_is_blocked(self):
        """If rm is NOT in allowed_commands, it's blocked before validation."""
        hook = create_security_hook(allowed_commands={"ls", "npm"})
        result = await hook({"tool_name": "Bash", "tool_input": {"command": "rm -f file.js"}})
        assert result.get("decision") == "block"

    @pytest.mark.asyncio
    async def test_non_bash_tool_passes_through(self):
        hook = self._make_hook()
        result = await hook({"tool_name": "Read", "tool_input": {}})
        assert result.get("decision") != "block"
