# Trino catalog reconciliation

## Purpose

The optional `trino-catalog` relation lets the Ranger charm create the Ranger
security zones, roles, and default policies needed for catalogs published by
Trino. Ranger is the relation requirer and Trino is the provider.

The relation does not create a Ranger Trino service. Register a Trino service
of type `trino` first, normally through the separate `policy` relation.

## Relation contract

The `trino-catalog` endpoint accepts one optional relation:

```yaml
trino-catalog:
  interface: trino_catalog
  optional: true
  limit: 1
```

On `relation-created`, the Ranger-side relation library publishes `app_name`
in its application databag. Trino publishes the following application
databag fields:

| Field | Value |
| --- | --- |
| `trino_url` | Trino endpoint URL |
| `trino_catalogs` | JSON-encoded list of `TrinoCatalog` objects |
| `trino_credentials_secret_id` | Juju secret identifier containing Trino credentials |

The library returns relation information only when all three provider fields
are present and `trino_catalogs` can be deserialized. The charm stores all
three values, although reconciliation uses only `trino_catalogs`.

## Create-only behavior

Reconciliation creates missing objects. It does not update or delete an
existing completed zone, role, or policy. Removing a catalog from Trino, or
removing the relation, does not remove Ranger objects. Remove zones, roles,
and policies manually when they are no longer wanted.

Ranger can create automatic policies while it creates a security zone. A zone
that still contains those automatic policies is treated as an interrupted
provisioning operation: the charm adds the missing default policies and
removes the automatic policies to complete the initial setup. A zone without
automatic policies is complete and remains untouched.

The leader runs reconciliation after complete relation data arrives and on
each later `update-status` hook. Ranger REST API failures are logged and
retried on a later hook. On `relation-broken`, the charm clears the stored
relation data but does not reconcile the removed catalogs.

## Catalog-to-zone mapping

Each catalog normally creates a zone with the same name. A catalog ending in
`_developer` is paired with its base catalog:

| Published catalogs | Security zone |
| --- | --- |
| `marketing` | `marketing` |
| `marketing_developer` | `marketing` |
| `marketing`, `marketing_developer` | `marketing` |

Each zone covers both `<name>` and `<name>_developer`, including when Trino
publishes only one of them.

## Roles and default policies

For a new zone, the charm creates these roles when they are absent:

- `<name>-viewer`
- `<name>-editor`
- `<name>-admin`
- `<name>-auditor`

The zone assigns its administration role to `<name>-admin` and its audit role
to `<name>-auditor`. It also creates the following default policies when they
are absent:

| Policy | Catalog scope | Roles or users | Accesses |
| --- | --- | --- | --- |
| `default - ro - <name>` | `<name>` | viewer, editor, admin | `select`, `show`, `use` |
| `default - rw - <name>` | `<name>_developer` | editor, admin | `select`, `show`, `use`, `insert`, `delete` |
| `default - ddl - <name>` | `<name>_developer` | admin | `alter`, `create`, `drop` |
| `default - is - <name>` | Both catalogs and `information_schema` | `{USER}` | `select`, `show`, `use` |

Membership of the first three policy role sets controls the corresponding
catalog access. The `default - is - <name>` policy is different: its
`{USER}` grant applies even when every generated role is empty.

## Configuration

Both options default to `true`:

| Option | Effect |
| --- | --- |
| `toggle-catalog-reconciliation` | Enables creation of missing zones, roles, and policies. Set to `false` to leave every catalog unchanged. |
| `enforce-strict-reconciliation` | Before creating a new zone, requires each corresponding generated role to be absent or empty. |

When `toggle-catalog-reconciliation=false`, reconciliation makes no changes
and `enforce-strict-reconciliation=false` has no effect.

With strict reconciliation enabled, a new zone is not created if any matching
role already has users, groups, or nested roles. This gate preserves
role-based non-loosening: creation cannot attach an existing populated role to
a new default policy. With strict reconciliation disabled, the operator
explicitly authorizes that loosening and the charm may create the zone even
when matching roles are populated. The resulting access is attributable to
the operator's configuration choice, not to autonomous charm behavior.

The gate does not preserve zone isolation. If an operator deletes or omits a
zone, the catalog falls back to permissive global Ranger policies.

## Operational responsibilities and residual risks

- **R3 — revoke access without deleting a managed zone.** Empty the generated
  roles or add deny policies. Deleting a zone can expose its catalog to
  permissive global policies.
- **R4 — avoid zone-name reuse.** Reusing a catalog name for an already
  existing zone is an uncatchable operator responsibility. The completed zone
  is left unchanged.
- **R5 — account for information schema access.** The
  `default - is - <name>` `{USER}` grant reaches `information_schema` even
  when the generated roles are empty. It is the one default grant not gated
  by role membership.
- **R6 — maintain naming conventions.** Identical catalog names mean the same
  catalog. Avoid roles whose names match
  `<name>-viewer`, `<name>-editor`, `<name>-admin`, or `<name>-auditor`
  unless they are intended to participate in the catalog setup.

## Relevant files

| File | Relevance |
| --- | --- |
| `charmcraft.yaml` | Declares the relation and reconciliation configuration options. |
| `src/charm.py` | Instantiates the relation library and invokes periodic reconciliation. |
| `src/relations/trino.py` | Handles relation events, manages state, and invokes reconciliation. |
| `src/reconcile.py` | Defines catalog mapping, Ranger object creation, and the strict role gate. |
| `src/literals.py` | Defines the Ranger service type and managed role and policy suffixes. |
| `lib/charms/trino_k8s/v0/trino_catalog.py` | Defines the shared relation protocol. |
| `tests/unit/test_reconcile.py` | Covers catalog mapping, policy generation, and create-only behavior. |
| `tests/unit/test_charm.py` | Covers configuration propagation and relation removal. |
