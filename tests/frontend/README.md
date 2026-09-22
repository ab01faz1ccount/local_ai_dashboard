# Frontend tests

Vitest + Testing Library behavior tests for the new Browse components.
Install (if not already present):

    npm install -D vitest jsdom @testing-library/react @testing-library/dom \
      @testing-library/user-event @testing-library/jest-dom @vitejs/plugin-react

Copy `vitest.config.ts` to your frontend root (merge with an existing one
if you have one), keep `setup.ts` and `helpers.ts` alongside the test
files, and run:

    npx vitest run

All 42 tests should pass. `BrowseIntegration.test.tsx` also renders your
existing `RuntimeCard`, `SettingsPage`, and `OnboardingWizard` components
to check the new Browse wiring end-to-end, so it needs those files (and
`format.ts` / `StatusLed.tsx`) present at the same relative paths as in
your project.
