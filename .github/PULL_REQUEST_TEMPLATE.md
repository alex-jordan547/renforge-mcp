<!--
Thank you for contributing to RenForge!

Before submitting:
- Keep the pull request focused on one coherent change.
- Link the relevant issue when one exists.
- Add or update tests for behavior changes.
- AI-assisted contributions are welcome, but you remain responsible for
  understanding, validating, and explaining every submitted change.
- Delete sections that are genuinely not applicable, but keep the validation
  results explicit.
-->

## Summary

<!-- What does this PR change? Keep this understandable without reading the diff. -->

## Motivation and related issue

<!-- Why is this change needed? Use "Fixes #123" when the PR should close an issue. -->

Fixes #

## Changes

-

## Validation

<!--
Report the commands actually executed and their results. Write "Not applicable"
only when a command is unrelated to the change.
-->

- `python -m compileall src tests`:
- `python -m pytest -q`:
- `npm --prefix ui run build`:
- Manual verification:

## User-facing changes

<!--
Describe visible behavior changes. For dashboard changes, include before/after
screenshots or a short video. Write "None" if there is no user-facing change.
-->

## Internationalization

<!--
Complete this section when UI copy or dashboard errors change.

- Canonical English copy belongs in ui/src/i18n/locales/en.json.
- Do not add placeholder English text to zh-CN.json.
- Missing zh-CN translations intentionally display their raw i18n keys.
- The i18n scanner is enforced by the frontend build.
-->

- [ ] New user-visible copy uses i18n keys, or this PR adds no user-visible copy.
- [ ] `npm --prefix ui run i18n:check` passes, or i18n is not applicable.

## Breaking changes

<!-- Explain migration steps and affected users. Write "None" when applicable. -->

None

## Documentation

<!-- List updated documentation, or explain why no documentation is required. -->

## Reviewer notes

<!-- Highlight risks, architectural decisions, limitations, or sensitive areas. -->

## Final checklist

- [ ] The PR is focused on one coherent change.
- [ ] Tests were added or updated for behavior changes.
- [ ] Generated `src/renforge/ui/static/` assets remain untracked; CI and release builds produce them.
- [ ] No credentials, tokens, personal paths, or other secrets are included.
- [ ] I understand the submitted code and can explain how it works.
