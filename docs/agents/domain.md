# Domain Docs

How the engineering skills should consume this repo's domain documentation when exploring the codebase.

## Layout: single-context

This repo uses a single root-level `CONTEXT.md` + `docs/adr/`. The product-level change (US-stock investment research assistant) is concentrated in the Python core (`gpt_researcher/`, `backend/`); the Next.js frontend is not being modified, so its concepts do not need a separate context yet.

```
/
├── CONTEXT.md
├── docs/adr/
│   ├── 0001-...md
│   └── 0002-...md
└── ...
```

Neither `CONTEXT.md` nor `docs/adr/` exists yet — they are created lazily by `/grill-with-docs` as terms and decisions actually get resolved. Don't flag their absence.

## When to migrate to multi-context

**Trigger**: when you start modifying `frontend/nextjs/` (it will develop a distinct domain — UI components, state, design system — separate from the Python research pipeline).

**Migration steps** (~10 min):
1. Move `CONTEXT.md` → `gpt_researcher/CONTEXT.md`
2. Create `frontend/nextjs/CONTEXT.md`
3. Create root `CONTEXT-MAP.md` listing both contexts
4. Update this file's "Layout" section to "multi-context" and update the structure diagram

Until that trigger fires, single-context is correct.

## Before exploring, read these

- **`CONTEXT.md`** at the repo root (single-context layout)
- **`docs/adr/`** — read ADRs that touch the area you're about to work in

If any of these files don't exist, proceed silently. Don't flag their absence; don't suggest creating them upfront.

## Use the glossary's vocabulary

When your output names a domain concept (in an issue title, a refactor proposal, a hypothesis, a test name), use the term as defined in `CONTEXT.md`. Don't drift to synonyms the glossary explicitly avoids.

If the concept you need isn't in the glossary yet, that's a signal — either you're inventing language the project doesn't use (reconsider) or there's a real gap (note it for `/grill-with-docs`).

## Flag ADR conflicts

If your output contradicts an existing ADR, surface it explicitly rather than silently overriding:

> _Contradicts ADR-0007 (...) — but worth reopening because…_
