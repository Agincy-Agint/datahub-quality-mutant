# Quality Mutant

Quality Mutant asks a question normal data-quality monitoring does not: **would the checks catch bad data if it arrived?** It reads DataHub metadata, creates six deterministic adversarial counterexamples, executes the existing checks in DuckDB, and records which mutants were killed or survived.

Built for **Build with DataHub: The Agent Hackathon**, category **Open / Wildcard**.

The category choice is deliberate. Quality Mutant executes a real adversarial test campaign,
but this release prepares DataHub-native evidence rather than writing it to a live graph. The
Wildcard track is therefore the accurate fit; the project does not claim the durable writeback
expected by the “Agents That Do Real Work” challenge description.

Judge demo: https://quality-mutant-9eca6471.pages.dev

## The three-minute demo

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
./scripts/demo.sh
```

The committed ecommerce fixture starts green: all six declared assertions pass. Quality Mutant then injects NULL, duplicate-key, orphan-foreign-key, invalid-enum, numeric-extreme, and stale-timestamp records. Five are caught. The orphan survives because `customer_id IS NOT NULL` looks green even when the customer does not exist. The agent returns the minimal missing referential-integrity SQL.

Generated evidence lands in `sample-output/`:

- `mutation-report.json`: baseline proof, deterministic hashes, scores, survivors, suggestions, and the per-operator kill matrix.
- `mutation-report.md`: linked coverage document content.
- `datahub-custom-assertion-result.prepared.json`: GraphQL operations for a DataHub CUSTOM assertion and its result.
- `datahub-coverage-document.prepared.json`: exact `save_document` MCP arguments.

Both writeback artifacts are stamped **`PREPARED_NOT_SENT`**. This project has no send/apply command.

## How DataHub is used

Fixture mode models the same metadata shapes deterministically: dataset and field tags, glossary terms, schemas, primary/foreign keys, assertions, and a Data Contract assertion bundle.

Live metadata mode starts the [official `mcp-server-datahub`](https://github.com/acryldata/mcp-server-datahub) over stdio with mutation tools disabled and data-quality reads enabled. It calls only these published read tools:

- `get_entities` for dataset metadata plus `schemaMetadata.primaryKeys` and `foreignKeys`;
- `list_schema_fields` for fields, types, nullability, tags, and glossary terms;
- `get_dataset_assertions` for definitions and recent result metadata.

The official MCP server does **not** return table rows. Live mode therefore requires a separate, explicit local rows file:

```bash
DATAHUB_GMS_URL=http://localhost:8080 \
quality-mutant run-mcp \
  --urn 'urn:li:dataset:(urn:li:dataPlatform:duckdb,showcase.orders,PROD)' \
  --rows fixtures/ecommerce.json \
  --output out/live
```

`get_dataset_assertions` is registered by the official server only when `DATA_QUALITY_TOOLS_ENABLED=true`; the adapter sets that flag and validates the tool list. Current MCP does not expose exact first-class Data Contract membership, so live mode labels the complete asset assertion set as an inferred bundle. It never pretends that inference is an exact contract read.

## Safety and correctness rules

- Baseline executable checks must pass before mutation begins.
- Unsupported assertion semantics are `NOT_EXECUTED`, never PASS.
- Unknown data types and malformed assertions fail closed.
- Equivalent mutants are excluded using the actual row multiset, independent of row order.
- Unavailable mutation operators are recorded instead of crashing the campaign.
- Every fixture, mutant, and report has a deterministic SHA-256 hash.
- The custom assertion reports SUCCESS only if the declared score threshold is met.
- There is no release gate, approval flow, external publication, or DataHub mutation path.

## CLI

```bash
quality-mutant run-fixture \
  --fixture fixtures/ecommerce.json \
  --output sample-output \
  --threshold 1.0

quality-mutant run-mcp \
  --urn '<dataset urn>' \
  --rows '<explicit local rows json>' \
  --output out/live \
  --mcp-command 'uvx mcp-server-datahub@latest'
```

## Tests

```bash
.venv/bin/pytest
.venv/bin/ruff check .
```

Tests cover baseline-first behavior, the visibly green-but-incomplete FK case, deterministic hashes, threshold-correct payload status, unsupported-assertion handling, fail-closed type parsing, input validation, exact MCP tool names, and both structured/text MCP response shapes.

## Honest limitations

- DuckDB execution currently supports six assertion families: not-null, uniqueness, accepted values, range, freshness, and foreign key. Other live definitions remain visible as `NOT_EXECUTED`.
- The real MCP adapter has been contract-tested against the official repository’s current tool names and response shape, but this repository does not include credentials or claim a successful connection to a live tenant.
- Live Data Contract membership is not available through the current MCP tool surface; the adapter records an inferred asset-level assertion bundle.
- Live assertion retrieval is currently bounded to the first 20 results per dataset; pagination is not implemented in this MVP.
- The MVP mutates one selected dataset and its first usable field/relationship per operator. It is not exhaustive combinatorial fuzzing.
- Prepared writeback JSON is inert and may still require version-specific adjustment before an authorized operator sends it.

## License

Apache License 2.0. See [LICENSE](LICENSE).
