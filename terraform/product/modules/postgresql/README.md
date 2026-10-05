# PostgreSQL K8s wrapper module

Temporary local stand-in for the upstream
[`postgresql-k8s-operator` track 14 Terraform module](https://github.com/canonical/postgresql-k8s-operator/tree/main/terraform).
It deploys one `postgresql-k8s` application with `trust = true`.

The upstream module still requires the Juju provider `~> 1.0`, which cannot be combined with the
`~> 2.0` provider used by the Ranger modules. The inputs and the `app_name`, `provides` and
`requires` outputs match the upstream module at commit `2d15b8e5d44afcf7d26883c3be17e9165e802324`,
so the product module can later switch to upstream by changing its `source`. The
`application` output is the only addition.

The upstream fix is on the branch
[`feature/fix-juju-terraform-provider-version-constraint-14`](https://github.com/canonical/postgresql-k8s-operator/compare/main...feature/fix-juju-terraform-provider-version-constraint-14).
Replace this wrapper once it is merged into the track 14 `main` branch.

## Inputs

| Name | Type | Default | Description |
| --- | --- | --- | --- |
| `app_name` | `string` | `"postgresql-k8s"` | Name of the application in the Juju model. |
| `base` | `string` | `"ubuntu@22.04"` | Application base. |
| `channel` | `string` | `"14/stable"` | Charm channel to use when deploying. |
| `config` | `map(string)` | `{}` | Application configuration. |
| `constraints` | `string` | `""` | Juju constraints for the application. |
| `model_uuid` | `string` | n/a | Juju model UUID. |
| `resources` | `map(string)` | `{}` | Resources to use with the application. |
| `revision` | `number` | `null` | Revision of the charm. |
| `storage_directives` | `map(string)` | `{ pgdata = "10G" }` | Storage directives. |
| `units` | `number` | `1` | Number of units to deploy. |

## Outputs

| Name | Description |
| --- | --- |
| `app_name` | Name of the deployed application. |
| `application` | The whole `juju_application` object. |
| `provides` | Map of the provides endpoint names. |
| `requires` | Map of the requires endpoint names. |
