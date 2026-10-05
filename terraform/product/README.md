# Ranger K8s product module

Terraform module that creates a Juju model and deploys Apache Ranger (`ranger-k8s` with
`charm-function=admin`) with the dependencies it needs to become active. The caller configures
the `juju` provider.

## Default deployment

- A Juju model named `model_name`, configured with `logging_config` and the `proxy` settings.
- `ranger-k8s` from the [charm module](../charm), integrated over `database` and `ingress`.
- `postgresql-k8s` (track `14`), integrated with Ranger's `database` endpoint.
- `traefik-k8s` (track `latest`), integrated with Ranger's `ingress` endpoint.
- An offer of Ranger's `policy` endpoint.

Ranger redirects to HTTPS. Without a TLS integration on Traefik it serves Traefik's default
certificate, so use `curl -k` to reach the UI. Setting `traefik.config.external_hostname` is
needed for Traefik to reach active status without a LoadBalancer address.

Not deployed: `ranger-k8s` usersync, LDAP, TLS, COS and OpenSearch.

## Replacing or removing dependencies

- `database_offer` replaces the bundled PostgreSQL with an existing offer.
- `ingress_offer` replaces the bundled Traefik with an existing offer.
- `traefik.enabled = false` deploys neither Traefik nor the ingress integration. A supplied
  `ingress_offer` takes precedence.
- `grafana_dashboard_offer`, `log_proxy_offer`, `metrics_endpoint_offer` and `opensearch_offer`
  integrate Ranger with optional external applications. None is integrated by default.

## Usage

```hcl
module "ranger" {
  source = "git::https://github.com/canonical/ranger-k8s-operator.git//terraform/product?ref=<commit>"

  model_name     = "ranger"
  logging_config = "<root>=WARNING"
  proxy          = {}
  risk           = "edge"

  traefik = {
    config = { external_hostname = "ranger.example.com" }
  }
}
```

## Requirements

| Name | Version |
| --- | --- |
| terraform | `>= 1.14` |
| juju | `~> 2.0` |
| time | `~> 0.12` |

## Inputs

Fields of `ranger`, `postgresql` and `traefik` that are not listed here are not exposed.

| Name | Type | Default | Description |
| --- | --- | --- | --- |
| `database_offer` | `string` | `null` | PostgreSQL offer URL. When set, PostgreSQL is not deployed. |
| `grafana_dashboard_offer` | `string` | `null` | Offer URL integrated with Ranger's `grafana-dashboard` endpoint. |
| `ingress_offer` | `string` | `null` | Ingress offer URL. When set, Traefik is not deployed. |
| `log_proxy_offer` | `string` | `null` | Offer URL integrated with Ranger's `log-proxy` endpoint. |
| `logging_config` | `string` | n/a | Model `logging-config`. |
| `metrics_endpoint_offer` | `string` | `null` | Offer URL integrated with Ranger's `metrics-endpoint` endpoint. |
| `model_name` | `string` | n/a | Name of the model to create. Must not be empty. |
| `opensearch_offer` | `string` | `null` | Offer URL integrated with Ranger's `opensearch` endpoint. |
| `postgresql` | `object` | `{}` | Bundled PostgreSQL: `base` (`ubuntu@22.04`), `channel` (`14/<risk>`), `config`, `constraints` (`""`), `resources`, `revision`, `storage_directives` (`{ pgdata = "10G" }`), `units` (`1`). |
| `proxy` | `object({ http, https, no_proxy })` | n/a | Model proxy settings, all optional strings. Null fields are left out of the model configuration. |
| `ranger` | `object` | `{}` | Ranger: `base`, `channel` (`latest/<risk>`), `config`, `constraints`, `endpoint_bindings`, `resources`, `revision`, `units` (`1`). `config` must not set `charm-function`. |
| `risk` | `string` | `"edge"` | Risk of the default channel of every bundled charm: `stable`, `candidate`, `beta` or `edge`. A `channel` in `ranger`, `postgresql` or `traefik` overrides it. |
| `traefik` | `object` | `{}` | Bundled Traefik: `base`, `channel` (`latest/<risk>`), `config`, `constraints` (`arch=amd64`), `enabled` (`true`), `resources`, `revision`, `storage_directives`, `units` (`1`). |

Revisions default to `null`, which deploys the latest revision of the channel. Set `revision` and
`resources` on each object to pin charms and OCI images, for example for air-gapped deployments.

## Outputs

| Name | Description |
| --- | --- |
| `metadata` | `{ version, deployed_at, updated_at }`. `updated_at` changes whenever an input changes. |
| `models` | `{ ranger = { model_uuid, components } }`. `components` holds the `juju_application` object of `ranger` and, when deployed, `postgresql` and `traefik`. |
| `offers` | `{ policy = <offer URL> }`. |

## Bundled dependencies

Traefik comes from the upstream `traefik-k8s-operator` Terraform module, pinned to a commit.

PostgreSQL comes from the local wrapper in [modules/postgresql](modules/postgresql) because the
upstream `postgresql-k8s-operator` track 14 module still requires the Juju provider `~> 1.0`.
Revisit the wrapper once the provider constraint fix (branch
`feature/fix-juju-terraform-provider-version-constraint-14`, already done for `16/edge` in
upstream PR #1657) is merged into the track 14 `main` branch. To migrate, swap the module
`source` to a pinned upstream commit and change `models.components.postgresql`, because the
upstream module has no `application` output.

## Security

Terraform state holds the model configuration and charm configuration, including proxy URLs. Do
not embed credentials in proxy URLs, and use an encrypted remote backend. The module creates no
secrets; configuration such as `ldap-credentials` holds Juju secret IDs, not secret values.

## Cleanup

Destroying the model removes PostgreSQL storage and can be slow. To clean up manually, run
`juju destroy-model --force <model_name>`.

## Tests

From this directory, with an authenticated Juju controller that has a Kubernetes cloud:

```shell
terraform init
terraform test
```

The suite runs plan checks and deploys the default stack, then waits for Ranger, PostgreSQL and
Traefik to become active. The wait helper needs `juju` and `jq` on the path.
