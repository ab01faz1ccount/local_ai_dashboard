# Frontend tests

Vitest + Testing Library behavior tests for the Browse components and the
new Logs page.

## Where these go — and a fix

These files, plus `vitest.config.ts`, belong **inside `frontend/`**
(`frontend/tests/`, `frontend/vitest.config.ts`) — not at a repo-root
`tests/frontend/`. Node/Vite resolve `node_modules` (and everything a
test file imports — React, vitest, testing-library, your own `../src/*`
modules) by walking **up** from wherever the file sits; `node_modules`
only exists under `frontend/`, so anything placed at a sibling
`tests/frontend/` can never find it, no matter how `vitest.config.ts` is
configured. If your repo currently has a `tests/frontend/` folder at the
root, **delete it** — this delivery replaces it with the working
`frontend/tests/` version. `tests/backend/` is unaffected; Python's
import story is different and that one already works from the repo root.

## Running them

Already installed as devDependencies in this delivery's `package.json`
(`vitest`, `jsdom`, `@testing-library/react`, `/dom`, `/user-event`,
`/jest-dom`, `@vitejs/plugin-react`) — if you're merging by hand instead
of taking the whole `package.json`, add those plus `vitest.config.ts`,
then:

    cd frontend
    npm install
    npx vitest run

All 70 tests should pass. `BrowseIntegration.test.tsx` also renders your
actual `RuntimeCard`, `SettingsPage`, and `OnboardingWizard` components,
so it needs those (and `format.ts` / `StatusLed.tsx`) present at their
normal paths under `frontend/src/`.

## What's new here

- `EventBadge.test.tsx` — the tone (ok/muted/bad/pending) `EventBadge`
  picks for every suffix it knows about, and that an unrecognized suffix
  falls back to "muted" instead of throwing.
- `LogsPage.test.tsx` — initial load, the empty state, a readable error
  on failure, type/runtime filters (and that they actually reload with
  the right query params), pagination ("Load older" with `before_id`),
  the live socket (new matching events prepend, non-matching ones don't,
  Pause really pauses, Resume catches back up, the socket disconnects on
  unmount), and a regression test for a real bug this suite caught
  during development — a deleted agent's `metadata.name` fallback was
  leaking into the **Runtime** column for `agent.*` events that never
  had a runtime to begin with.

`package.json`/`package-lock.json` also gained `@xterm/xterm` and
`@xterm/addon-fit` as real dependencies — `AgentTerminal.tsx` already
imported them, but they were never actually listed, so a clean
`npm install` elsewhere would have failed on that file. Unrelated to
Logs; found it while getting the typecheck clean.
