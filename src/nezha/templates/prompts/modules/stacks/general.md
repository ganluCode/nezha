### GENERAL CODING CONVENTIONS

Before implementing anything, read these files to understand the project:
- Build/dependency file (e.g. `package.json`, `pyproject.toml`, `pom.xml`, `Makefile`)
- Existing source code — naming conventions, module structure, patterns used
- Test files — understand what testing framework and patterns are used

**Implementation approach**:
1. **Understand** the acceptance criteria from the target feature
2. **Implement** the code changes needed following existing project conventions
3. **Test** it — run the relevant tests
4. **Update** task_list.json: set `passes: true` for the completed feature
5. **Commit** your changes: `git add -A && git commit -m "<feature-id>: <brief description>"`

**General best practices**:
- Follow the existing code style, naming conventions, and patterns
- Keep functions focused — one responsibility per function
- Write self-documenting code with clear variable and function names
- Handle errors explicitly — do not swallow exceptions silently
- Use the project's established logging mechanism (not print/console.log)
- Keep the workspace in a clean, working state at all times
