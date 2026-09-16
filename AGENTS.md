# Agent Constitution

## Read Order
Before meaningful work, read: AGENTS.md → ARCHITECTURE.md → PROTOCOL.md → ROADMAP.md → relevant implementation → its tests. Inspect git state first.

## Source-of-Truth Hierarchy
1. CODE (implementation and tests)
2. PROTOCOL.md (frozen wire format)
3. ARCHITECTURE.md (system boundaries and ownership)
4. ROADMAP.md (milestone status)
5. README.md (human entry point and PS constraints)
6. DECISIONS.md (frozen/proposed/rejected decisions)
7. AGENTS.md (this document - procedures only)

## Conflict Rules
If documentation conflicts with verified code/tests:
1. Report contradiction before changing anything
2. Code/tests win over documentation
3. Do not modify frozen protocol (PROTOCOL.md)
4. Stop and report genuine protocol contradictions

## Contradiction/Stop-and-Report Procedure
Upon discovering a genuine contradiction in frozen protocol or canonical constraints:
1. STOP all implementation work
2. Document the contradiction clearly
3. Report evidence and impact
4. Do not proceed until resolved

## Scope Control
- Do not implement future milestones while active milestone is unproven
- Prefer standard library and small explicit modules
- Do not add frameworks, services, databases, security layers, or dependencies without concrete need
- Never delete or weaken tests to make CI pass
- Do not fabricate measurements or commit credentials/recordings/models

## Dependency/Architecture-Change Gate
Architectural changes require:
1. Real implementation failure documented
2. Affected interface identified
3. Evidence, alternatives, impact assessed
4. Tests demonstrating the failure
5. No silent alteration of frozen protocol layout, control lifecycle, timings, codecs, or semantics

## Test Workflow
1. Create focused changes
2. Add/adjust automated tests with every behavior change
3. Run `pytest`
4. Inspect `git diff` and `git status` before committing
5. Preserve working behavior of implemented milestones

## Commit Workflow
1. Make smallest coherent change
2. Run focused tests plus practical full suite
3. Update docs for changed behavior
4. Report uncertainty or failures honestly
5. Use conventional scoped messages: `feat(m1): add websocket receiver`
6. End commit messages with: Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>

## Definition of Done
- Code implements the minimal coherent change
- Tests pass (unit + integration)
- Documentation updated for changed behavior
- No duplicated facts across documents
- Commit message follows convention
- No unnecessary dependencies added