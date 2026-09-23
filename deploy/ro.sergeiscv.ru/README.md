# ro.sergei-scv.ru deployment state

This directory tracks the non-secret live Hermes state that should survive
cleanups of the `ro.sergei-scv.ru` installation.

The directory name is retained as a stable on-disk deployment path; the live DNS name is `ro.sergei-scv.ru`.

- `hermes/profiles/letovo/cron/jobs.json` mirrors
  `/home/yara/.hermes/profiles/letovo/cron/jobs.json`.

Webhook scripts and subscription files are intentionally kept on the host.
Secrets, `.env` files, logs, state databases, and caches are not tracked here.
