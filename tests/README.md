> **LOCKED governing file.** Do not edit in place. See `GOVERNANCE.md`.

# tests/ — status: no baseline yet

**There is no test baseline in this repo, and that is correct, not an oversight.** The stack is
undecided (`design/decisions/ADR-0002-stack-selection.md`, open, `Q-0002`) — there is nothing to
write a runnable test against yet, and no toolchain to run it with.

**Do not fake a baseline.** Do not add a stub test that always passes, a test runner with nothing
to run, or a smoke test written against a stack that hasn't actually been chosen. A scaffold whose
test command fails (or trivially no-ops) on first invocation trains every future agent — human or
AI — to stop trusting or running the test command at all, which is worse than having no test
command documented.

## What must happen instead
Whoever resolves `ADR-0002` (picks the stack) must, **in the same change**:
1. Add the language-appropriate test tooling and a real, minimal smoke test that actually exercises
   something (even just "the app boots" / "the CLI prints its help").
2. Run it for real and confirm it's green before reporting the ADR resolved.
3. Update `CLAUDE.md`'s "Canonical commands" section with the real command to run it.
4. Replace this file's content with the real testing conventions for the chosen stack.

## Test fixtures and .gitignore
Whatever stack is chosen, any fixture files placed under `tests/` must stay trackable in git. The
top-level `.gitignore` already carries a `!tests/**` negation guard specifically so a future
data/build-artifact ignore pattern can never silently swallow a fixture in here — verify with
`git status --short` / `git check-ignore -v <fixture-path>` after adding one, not just by assuming
the guard works.
