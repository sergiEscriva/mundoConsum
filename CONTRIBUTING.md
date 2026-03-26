# Contributing

Thank you for your interest in contributing to Consum Tracker!

This is a personal project, but contributions, bug reports, and suggestions are
welcome. Please follow these guidelines to keep the process smooth.

---

## Reporting bugs

Use the **Bug report** issue template. Include:

- A clear description of the problem.
- Steps to reproduce it.
- Expected vs. actual behaviour.
- Relevant log output (avoid pasting your cookies or personal data).

## Suggesting features

Use the **Feature request** issue template and describe the problem your idea
solves before proposing a solution.

## Submitting a pull request

1. Fork the repository and create a branch from `main`.
2. Keep changes focused — one fix or feature per PR.
3. Make sure `scripts/scraper.py` and `scripts/build_data.py` still run without
   errors after your change.
4. Do **not** commit `cookies.json`, personal ticket data, or any credentials.
5. Update `CHANGELOG.md` under an `[Unreleased]` section.
6. Open the PR with a clear title and description using the provided template.

## Code style

- Python: follow [PEP 8](https://peps.python.org/pep-0008/). Keep functions small
  and add a docstring to any new public function.
- JSON files: write with `ensure_ascii=False` (preserves Spanish characters).
- Comments: write in the same language as the surrounding code (Spanish for this
  codebase).

## Security

If you find a security issue, **do not open a public issue**. See
[SECURITY.md](SECURITY.md) for the responsible-disclosure process.

---

## License

By contributing you agree that your contributions will be licensed under the
[MIT License](LICENSE).
