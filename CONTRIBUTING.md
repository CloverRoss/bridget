# Contributing to bridget

## Tracking work

bridget uses [mg](https://github.com/drellem2/macguffin) as the maintainer's
local work tracker — bugs, ideas, designs, and tasks all live there. Because
external contributors don't have access to that local state, bug and roadmap
state is mirrored into in-repo markdown so the README is self-contained:
[ROADMAP.md](ROADMAP.md) tracks v2 priorities and
[KNOWN_BUGS.md](KNOWN_BUGS.md) lists currently-broken behaviors.

## PRs that change roadmap or known-bug state

If your PR adds, changes, dispatches, or closes a bug or roadmap item, update
`KNOWN_BUGS.md` and/or `ROADMAP.md` in the same PR — don't leave the in-repo
copies out of sync with the change. The PR template's checkbox is the
enforcement surface; please tick it (or note that the PR doesn't touch
roadmap/bug state) before requesting review.

## Style and testing

bridget is a single-file Python script targeting Python 3.9+. Match the
existing style in `bridget` — there's no separate formatter or linter
configured.

Before opening a PR, run both gates from the repo root:

- `./build.sh` — compiles `bridget` and `tests/`, catching import-time syntax
  errors.
- `./test.sh` — runs the pytest suite in `tests/` (600+ tests).

Both run under the venv `install.sh` creates (`~/.pogo/venv-bridget`), which is
the same interpreter the service runs in production — not whatever `python3`
happens to be first on your `PATH`. Run `./install.sh` first if you haven't;
the gates fail loudly rather than falling back to a different Python, because a
gate that quietly runs under the wrong interpreter is worse than no gate.
Override with `BRIDGET_GATE_PYTHON` if your install is non-standard.

These same two commands are the merge gate — the refinery runs them on every
merge request (see `.pogo/refinery.toml`), so a change that breaks any test
cannot land. A green suite is not a substitute for manual verification against
a live Discord bot on behavior changes, but it is now a hard requirement.

After `./test.sh` passes, you can also run
`./tests/smoke-fresh-install.sh` to exercise every Discord command
path against a fresh-install env fixture. It boots bridget with only
the three required Discord vars set, calls `handle_command` for
each command, and asserts that no result matches the deleted
"is unavailable: set ..." config-error pattern. Recommended
before merging anything that touches `handle_command` or
`load_config`.
