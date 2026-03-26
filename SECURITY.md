# Security Policy

## Supported versions

| Version | Supported |
|---------|-----------|
| 1.x     | Yes       |

## Reporting a vulnerability

If you discover a security vulnerability in this project, please **do not open a
public GitHub issue**.

Instead, report it privately:

1. Go to the repository's **Security** tab on GitHub.
2. Click **Report a vulnerability** (GitHub's private advisory feature).
3. Describe the issue, steps to reproduce it, and its potential impact.

You can expect an acknowledgement within **48 hours** and a resolution or
status update within **7 days**.

## Sensitive data

- **Never commit `cookies.json`** or any session token to the repository.
  The `.gitignore` already excludes it, but double-check before every push.
- The `CONSUM_COOKIES` GitHub Secret must only be accessible to repository
  admins and the Actions runner — never print it in logs.
- Ticket JSON files in `data/tickets/` may contain personal purchase history.
  Keep the repository private if you prefer not to share that data publicly.
