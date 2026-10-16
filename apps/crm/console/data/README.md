# Fictional customer fixture

`customer_examples.json` supplies the optional legacy customer-only fixture.
Its context labels are illustrative data, not product categories or required
form choices. It contains no credentials, live contact routes or platform IDs.

`seed_vorntek_demo` remains plan-only without explicit synthetic-target flags;
its apply path still refuses a nonempty customer database. It is not the complete
cross-module example bundle, an idempotent updater, or a reset command. Those
are separate integration acceptance requirements, not proved by this fixture.
