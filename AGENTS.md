# New Crown development rules

- Read README.md and docs/IMPLEMENTATION_STATUS.md before changing the project. Do not infer deployment or acceptance from file presence.
- Preserve the existing UI, business contracts, permission checks, audit history and caller-owned modifications.
- Keep business SQL under apps/crm/db/migrations as the single authoritative chain; do not rewrite applied SQL or silently replay an incomplete legacy ledger.
- Default development/restoration environments to silent external integrations. Test both browser measurement and server-side dispatch guards. No real customer submissions, messages, advertising changes or WhatsApp activation without explicit scope authorization.
- Never commit secrets, real customer data, private backups, production configuration or runtime exports. Publicly accessible media is not automatically licensed for redistribution.
- Use isolated databases and persistent volumes. Do not reset shared schemas or delete volumes as a troubleshooting shortcut.
- Production deployment, private-data restoration and public release require confirmed targets and explicit authority. Never touch unrelated services on a shared server.
- Verify changed behavior with tests and actual runtime checks. Update implementation status with observed versions, evidence and unresolved limitations.
