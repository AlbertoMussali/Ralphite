# Secrets Strategy (Dev/Prod)

## Development

- Use `.env` for local development only.
- Start from `.env.example` and fill in non-production values.
- Never commit `.env` or real tokens.
- Rotate any accidentally exposed development token immediately.

## Production

- Do not use `.env` files in production images or hosts.
- Inject secrets at deploy/runtime using a managed secret store or CI/CD secret manager.
- Grant least-privilege access to secrets and services.
- Rotate production secrets on a schedule and immediately after incidents.
- Keep production and development credentials fully separated.

## Repository Rules

- Keep `.env.example` committed with placeholders only.
- Keep real secret values out of git history, issues, and logs.
