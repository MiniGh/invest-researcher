# Issue tracker: GitHub

Issues and PRDs for this repo live as GitHub issues at **`MiniGh/invest-researcher`** (the user's fork of `assafelovic/gpt-researcher`). Use the `gh` CLI for all operations.

`origin` is the fork (`MiniGh/invest-researcher`); `upstream` is the source repo (`assafelovic/gpt-researcher`). `gh` infers the target repo from `origin` automatically when run inside this clone, so by default issues land in the right place. **Do not** add `--repo` flags that route to upstream.

## Conventions

- **Create an issue**: `gh issue create --title "..." --body "..."`. Use a heredoc for multi-line bodies.
- **Read an issue**: `gh issue view <number> --comments`, filtering comments by `jq` and also fetching labels.
- **List issues**: `gh issue list --state open --json number,title,body,labels,comments --jq '[.[] | {number, title, body, labels: [.labels[].name], comments: [.comments[].body]}]'` with appropriate `--label` and `--state` filters.
- **Comment on an issue**: `gh issue comment <number> --body "..."`
- **Apply / remove labels**: `gh issue edit <number> --add-label "..."` / `--remove-label "..."`
- **Close**: `gh issue close <number> --comment "..."`

## When a skill says "publish to the issue tracker"

Create a GitHub issue.

## When a skill says "fetch the relevant ticket"

Run `gh issue view <number> --comments`.
