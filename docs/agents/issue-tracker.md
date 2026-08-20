# Issue tracker: GitHub

Issues and product requirement documents for this repository use GitHub Issues. Use the `gh` command for all operations.

## Repository

Use `aayushman-singh/hydraclaim`. The `gh` command detects this repository from the Git remote.

## Operations

- Create an issue: `gh issue create --title "..." --body "..."`.
- Read an issue: `gh issue view <number> --comments`.
- List issues: `gh issue list --state open`.
- Add a comment: `gh issue comment <number> --body "..."`.
- Add or remove labels: use `gh issue edit` with `--add-label` or `--remove-label`.
- Close an issue: `gh issue close <number> --comment "..."`.

When a skill says to publish an issue, create a GitHub issue. When a skill says to get a ticket, use `gh issue view <number> --comments`.
