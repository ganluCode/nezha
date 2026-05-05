### 项目规范 — PYTHON

实现任何代码前，先阅读这些文件了解项目：
- `pyproject.toml` 或 `setup.py` 或 `setup.cfg` — 依赖、Python 版本、构建工具
- `requirements.txt` / `requirements-dev.txt` —（如有）
- 现有源代码 — 命名规范、模块结构、使用的模式
- `conftest.py`（如有）— 共享 fixtures 和测试配置
- `.ruff.toml` / `ruff.toml` / `pyproject.toml [tool.ruff]` — lint 规则

**典型 Python 项目结构**：

```
# src-layout（推荐用于库/包）
src/
  <package>/
    __init__.py
    models/          <- 数据模型（dataclass、Pydantic、SQLAlchemy）
    services/        <- 业务逻辑
    api/             <- API 路由（FastAPI/Flask/Django）
    repositories/    <- 数据访问层
    utils/           <- 工具函数
    config.py        <- 配置
    exceptions.py    <- 自定义异常
tests/
  conftest.py        <- 共享 fixtures
  test_<module>.py   <- 测试文件镜像源代码结构
  integration/       <- 集成测试

# flat-layout（应用程序常用）
<package>/
  __init__.py
  ...
tests/
  ...
```

测试命令：`pytest tests/ -x -v`

**Python 特定测试模式**：
- 用 `pytest` + fixtures（`conftest.py`）做 setup/teardown
- 用 `pytest.raises` 测异常
- 用 `pytest.mark.parametrize` 做数据驱动测试
- 用 `unittest.mock.patch` / `pytest-mock` mock 外部依赖
- API 测试：用框架的 test client（FastAPI 的 `TestClient`、Flask 的 `test_client()`）
- 异步代码：用 `pytest-asyncio` + `@pytest.mark.asyncio`

### PYTHON 最佳实践

- **分层**：API/CLI → Service → Repository/数据访问。保持层次分离。
- **API 路由不写业务逻辑** — 路由只校验输入并委托给 service
- **使用 Pydantic 模型（或 dataclasses）** 做数据校验和 API schema
- **类型注解**写在所有公共函数和方法上
- **Docstring** 写在公共 API 上（Google 或 NumPy 风格，与项目惯例一致）
- **异常处理**：service 层抛领域特定异常，在 API 边界统一捕获
- **依赖注入**：优先用构造函数/函数参数，避免全局状态
- **日志**：用 `logging.getLogger(__name__)` — 库/服务代码绝不用 `print()`
- **配置**：用环境变量或配置文件（python-dotenv、pydantic-settings），不要硬编码
- **导入**：优先用绝对导入；用 `__all__` 声明公共 API
- 如果项目用了 lint 工具（`ruff`、`mypy`），提交前要运行
