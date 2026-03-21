### PROJECT CONVENTIONS — PYTHON

Before implementing anything, read these files to understand the project:
- `pyproject.toml` or `setup.py` or `setup.cfg` — dependencies, Python version, build tool
- `requirements.txt` / `requirements-dev.txt` — if present
- Existing source code — naming conventions, module structure, patterns used
- `conftest.py` (if exists) — shared fixtures and test configuration
- `.ruff.toml` / `ruff.toml` / `pyproject.toml [tool.ruff]` — linting rules

**Typical Python project structures**:

```
# src-layout (preferred for libraries/packages)
src/
  <package>/
    __init__.py
    models/          <- Data models (dataclass, Pydantic, SQLAlchemy)
    services/        <- Business logic
    api/             <- API routes (FastAPI/Flask/Django)
    repositories/    <- Data access layer
    utils/           <- Helpers
    config.py        <- Configuration
    exceptions.py    <- Custom exceptions
tests/
  conftest.py        <- Shared fixtures
  test_<module>.py   <- Test files mirror source structure
  integration/       <- Integration tests

# flat-layout (common for applications)
<package>/
  __init__.py
  ...
tests/
  ...
```

Test command: `pytest tests/ -x -v`

**Python-specific test patterns**:
- Use `pytest` with fixtures (`conftest.py`) for setup/teardown
- Use `pytest.raises` for exception testing
- Use `pytest.mark.parametrize` for data-driven tests
- Use `unittest.mock.patch` / `pytest-mock` for mocking external dependencies
- For API testing: use framework test client (`TestClient` for FastAPI, `test_client()` for Flask)
- For async code: use `pytest-asyncio` with `@pytest.mark.asyncio`

### PYTHON BEST PRACTICES

- **Layering**: API/CLI → Service → Repository/Data access. Keep layers separated.
- **No business logic in API routes** — routes validate input and delegate to services
- **Use Pydantic models (or dataclasses)** for data validation and API schemas
- **Type hints** on all public functions and methods
- **Docstrings** on public APIs (Google or NumPy style, match project convention)
- **Exception handling**: raise domain-specific exceptions in services, catch at API boundary
- **Dependency injection**: prefer constructor/function parameters over global state
- **Logging**: use `logging.getLogger(__name__)` — never `print()` in library/service code
- **Config**: use environment variables or config files (python-dotenv, pydantic-settings), not hardcoded values
- **Imports**: absolute imports preferred; use `__all__` for public API
- If the project uses a linter (`ruff`, `mypy`), run it before committing
