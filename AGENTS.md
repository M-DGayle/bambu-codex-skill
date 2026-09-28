# Repository instructions

Keep the primary checkout on `dev`, matching `origin/dev`. Implement changes in a worktree from `origin/dev`; push commits and open substantive draft pull requests into `dev`. Do not merge or promote without user authorization. Never switch, stash, reset, or commit in the primary checkout. Initial repository bootstrap is the sole exception to having an existing `origin/dev`.

Update CHANGELOG.md for source changes. Validate with `python -m unittest discover -s tests -v`, `python scripts/validate_package.py`, and `python tests/mcp_smoke.py` in the configured runtime. Do not start real prints, change temperatures, move motors, or alter printer settings during automated tests. Live read-only checks require a configured printer.

Never commit printer credentials, account tokens, local MCP launch configuration, user profiles, models, slices, logs, or machine-specific paths. Keep runtime state outside the repository. Treat raw printer commands and G-code as privileged physical operations. A broker acknowledgement does not confirm physical execution. A slicer exit code alone does not prove valid toolpaths.

This public personal repository follows the distribution model of M-DGayle/fusion-360-codex-skill. It is not a Madd-Technologies organization repository.
