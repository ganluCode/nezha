### PROJECT CONVENTIONS — FRONTEND

Before implementing anything, read these files to understand the project:
- `package.json` — dependencies, scripts, framework version
- `tech_stack.yaml` (if exists) — UI library, CSS solution, component conventions
- Existing source structure in `src/components/` and `src/pages/`

Test command: `npm test` or `npx vitest run`

**Testing approach (Testing Library)**:
- Query elements with `getByRole`, `getByText`, `getByLabelText` (user/a11y perspective)
- Simulate interactions with `userEvent` (not `fireEvent`)
- Mock API requests with MSW (intercept at network layer, not mock `fetch`)
- Prefer integration tests (page/feature level) over per-component unit tests
- Test custom hooks with `renderHook` only when they contain business logic

**Frontend-specific test patterns**:
- User interaction flows ("user clicks button → sees result")
- Conditional rendering ("when data is empty → shows empty state")
- Form validation ("invalid email → shows error message")
- Async operation results ("after loading → list shows 3 items")
- Error boundaries ("API fails → shows error message")

**MUST NOT test**:
- Internal component state variables
- CSS class names or DOM structure
- Props passing between parent/child components
- Third-party library behavior (React/Vue rendering, router internals)
- Specific pixel values or styling details

### FRONTEND BEST PRACTICES

- **Responsiveness**: Ensure the UI adapts to mobile/tablet/desktop viewports
- **Accessibility**: Add basic ARIA attributes (`aria-label`, `role`) and ensure keyboard navigation support
- **Reusability**: Extract generic UI elements into reusable components in `src/components/common/`
- **Style isolation**: Use CSS Modules, Tailwind, or the project's CSS solution consistently to prevent pollution
- **Clean code**: Remove `console.log` statements and unused imports before committing
- **Build check**: Run `npm run build` or `npm run lint` and fix TypeScript errors or ESLint warnings
- **Scaffold**: Create new files in `src/components/` or `src/pages/` following the existing directory structure
- **Integration**: Import and integrate the new component into the parent container or router configuration
- **Tech stack compliance**: Adhere strictly to the UI library and styling method defined in the project
