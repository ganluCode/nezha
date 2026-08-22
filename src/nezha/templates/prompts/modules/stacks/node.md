### PROJECT CONVENTIONS — NODE.JS

Before implementing anything, read these files to understand the project:
- `package.json` — scripts, module type, package manager, dependencies
- `package-lock.json` / `pnpm-lock.yaml` / `yarn.lock` — package manager and locked versions
- `tsconfig.json` — TypeScript target, module mode, strictness
- Existing `src/` and `test/` structure — naming, exports, layering, test style
- `.eslintrc*`, `eslint.config.*`, `.prettierrc*` — linting and formatting rules

Prefer project scripts over ad hoc commands:
- Tests: `npm test`, `npm run test`, `pnpm test`, or `yarn test`
- Build/typecheck: `npm run build`, `npm run typecheck`, or `npx tsc --noEmit`
- Lint: `npm run lint`

**Typical Node.js project structures**:

```
src/
  index.js|ts          <- public entrypoint
  cli.js|ts            <- CLI entrypoint, if any
  services/            <- business logic
  repositories/        <- persistence and external data access
  routes/              <- HTTP routes/controllers
  utils/               <- small reusable helpers
test/
  *.test.js|ts         <- unit/integration tests
```

### NODE.JS TESTING PRACTICES

- Use the project's existing test runner. Do not migrate Jest/Vitest/node:test unless asked.
- For Node's built-in test runner, use `node:test` and `node:assert/strict`.
- Test observable behavior through public exports, CLI commands, or HTTP handlers.
- Cover error cases, invalid input, and async rejection paths.
- Prefer deterministic tests: no real network calls, wall-clock sleeps, or shared global state.
- Reset in-memory stores or mocks between tests.

### NODE.JS BEST PRACTICES

- Keep business logic out of CLI/HTTP adapters; delegate to services.
- Prefer explicit named exports for reusable functions unless the project uses default exports.
- Preserve the existing module system (`type: "module"` ESM vs CommonJS).
- Avoid introducing new dependencies unless the task requires it and the project already accepts dependency changes.
- Handle async errors explicitly; never leave unhandled promises.
- Validate external inputs at boundaries and return clear errors.
- Do not commit debug `console.log` statements in library/service code.
- Run relevant tests after changes and update `task_list.json` only after verification passes.
