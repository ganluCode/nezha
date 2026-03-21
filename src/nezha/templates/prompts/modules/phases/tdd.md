### TDD FRAMEWORK (If `is_rework` is FALSE — new feature)

1. **Read** existing code to understand conventions (naming, error handling, code style)
2. **Write tests FIRST (RED)**:
   Tests must be derived from the feature's **acceptance criteria**, not from code structure.

   **MUST test** (business value):
   - Business rules and domain logic (calculations, state transitions, validations with business meaning)
   - Edge cases and error scenarios (invalid input, boundary conditions, concurrent access)
   - API contracts (request/response format, status codes, error responses)
   - Data transformations that contain business logic

   **MUST NOT test** (zero value):
   - Constructors, getters, setters, simple accessors
   - Simple CRUD with no business logic (just delegates to data layer)
   - Framework behavior (e.g. does dependency injection work, does ORM commit)
   - Configuration classes that only declare beans/settings

   **Judgment rule**: "If this test fails, what business problem does it indicate?" — if no clear answer, skip it.

3. **Run tests — confirm FAIL**: `{{test_command}}` — tests should fail (no implementation yet)
4. **Implement (GREEN)** following best practices for the stack
5. **Run tests — confirm PASS**: `{{test_command}}` — all tests should pass now
6. **Update** task_list.json: set `passes: true`
7. **Commit**: `git add -A && git commit -m "<feature-id>: <brief description>"`
