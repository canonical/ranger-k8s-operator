# Ranger K8s charm module

Terraform module that deploys one [`ranger-k8s`](https://charmhub.io/ranger-k8s) application and,
optionally, one Juju offer per requested `provides` endpoint. It does not create models,
integrations, secrets or dependencies; the caller owns those.

The module has no `machines` input (Kubernetes charm), no `storage_directives` input (the charm
declares no storage) and does not set `trust` (the charm does not use the Kubernetes API).

## Usage

The caller configures the `juju` provider.

```hcl
module "ranger" {
  source = "git::https://github.com/canonical/ranger-k8s-operator.git//terraform/charm?ref=<commit>"

  model_uuid        = juju_model.this.uuid
  channel           = "latest/edge"
  config            = { "charm-function" = "admin" }
  offered_endpoints = ["policy"]
}
```

## Requirements

| Name | Version |
| --- | --- |
| terraform | `>= 1.14` |
| juju | `~> 2.0` |

## Inputs

| Name | Type | Default | Description |
| --- | --- | --- | --- |
| `app_name` | `string` | `"ranger-k8s"` | Name to give the deployed application. Must not be empty. |
| `base` | `string` | `null` | Operating system on which to deploy, e.g. `ubuntu@22.04`. Null lets Juju choose. |
| `channel` | `string` | `"latest/edge"` | Channel that the charm is deployed from. |
| `config` | `map(string)` | `{}` | Charm configuration options, passed unchanged. Juju converts values to the declared option types. |
| `constraints` | `string` | `null` | Constraints for the application. |
| `endpoint_bindings` | `set(object({ space = string, endpoint = optional(string) }))` | `[]` | Endpoint bindings of the application. |
| `expose` | `object({ cidrs = optional(string), endpoints = optional(string), spaces = optional(string) })` | `null` | The `expose` block. Null leaves the application unexposed; `{}` exposes everything. |
| `model_uuid` | `string` | n/a | UUID of an existing Juju model. |
| `offered_endpoints` | `list(string)` | `[]` | Provides endpoints to offer: `policy`, `metrics-endpoint` or `grafana-dashboard`. |
| `resources` | `map(string)` | `{}` | Charm resources, e.g. `{ "ranger-image" = "<revision or OCI image>" }`. |
| `revision` | `number` | `null` | Charm revision. Null deploys the latest revision of the channel. |
| `units` | `number` | `1` | Number of units. Must be at least 1. |

## Outputs

| Name | Description |
| --- | --- |
| `application` | The deployed `juju_application` object. |
| `offers` | Map from offered endpoint name to `{ kind = "offer", url }`. |
| `provides` | Map of `grafana_dashboard`, `metrics_endpoint` and `policy`, each `{ kind = "endpoint", name, endpoint }`. |
| `requires` | Map of `database`, `ingress`, `ldap`, `log_proxy`, `opensearch` and `trino_catalog`, each `{ kind = "endpoint", name, endpoint }`. |

Offers are named `<app_name>-<endpoint>`.

## Tests

From this directory, with an authenticated Juju controller that has a Kubernetes cloud and
credential named `tfk8s`:

```shell
terraform init
terraform test
```

The suite creates a temporary model and deploys Ranger alone. Ranger can stay in `waiting`
status because the module does not integrate it with a database.
