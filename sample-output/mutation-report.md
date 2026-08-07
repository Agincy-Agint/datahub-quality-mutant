# Quality mutation coverage: orders

**Result:** 5/6 non-equivalent mutants killed (83.3%); threshold 100.0% not met.

The baseline passed before mutation. Unsupported live assertion semantics are shown as `NOT_EXECUTED`; they are never counted as passing.

## Operator kill matrix

| Operator | Result | Killed by | Mutant hash |
|---|---|---|---|
| `null` | KILLED | orders.order_id.not_null, orders.order_id.unique | `155ffab86d872972` |
| `duplicate` | KILLED | orders.order_id.unique | `fa14006ee21277e0` |
| `orphan_fk` | SURVIVED | — | `e2b63ed3b44ca8e9` |
| `enum` | KILLED | orders.status.accepted | `12121f4d48555be5` |
| `boundary_extreme` | KILLED | orders.amount.range | `d22a0c763f60acf6` |
| `stale_timestamp` | KILLED | orders.updated_at.fresh | `b65cd40f48556cb8` |

## Surviving counterexamples

- `orphan_fk` survived. Suggested `foreign_key` check: The field remained non-null but pointed to no parent row.

```sql
SELECT count(*) FROM orders s LEFT JOIN customers t ON s.customer_id=t.customer_id WHERE s.customer_id IS NOT NULL AND t.customer_id IS NULL
```

## Evidence

- Fixture/catalog hash: `c5a40f0ddf90304a641147c50af36a4d331decc1d4e491a8e0db41c1984e33da`
- Report hash: `a3d530565b70dade5e48eca398ed9c58e369bc31e231367fccfc7b2256d3dbf5`
- Source: `fixture:ecommerce.json`
- Evaluated at fixed timestamp: `2026-08-07T12:00:00+00:00`
- Writeback state: **prepared, not sent**
