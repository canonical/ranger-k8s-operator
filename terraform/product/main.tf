# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

resource "juju_model" "this" {
  name = var.model_name

  config = merge(
    { "logging-config" = var.logging_config },
    {
      for key, value in {
        "juju-http-proxy"  = var.proxy.http
        "juju-https-proxy" = var.proxy.https
        "juju-no-proxy"    = var.proxy.no_proxy
      } : key => value if value != null
    },
  )
}

module "ranger" {
  source = "../charm"

  app_name          = "ranger-k8s"
  model_uuid        = juju_model.this.uuid
  base              = var.ranger.base
  channel           = local.ranger_channel
  config            = merge(var.ranger.config, { "charm-function" = "admin" })
  constraints       = var.ranger.constraints
  endpoint_bindings = var.ranger.endpoint_bindings
  offered_endpoints = ["policy"]
  resources         = var.ranger.resources
  revision          = var.ranger.revision
  units             = var.ranger.units
}

module "postgresql" {
  source = "./modules/postgresql"
  count  = local.deploy_postgresql ? 1 : 0

  app_name           = "postgresql-k8s"
  model_uuid         = juju_model.this.uuid
  base               = var.postgresql.base
  channel            = local.postgresql_channel
  config             = var.postgresql.config
  constraints        = var.postgresql.constraints
  resources          = var.postgresql.resources
  revision           = var.postgresql.revision
  storage_directives = var.postgresql.storage_directives
  units              = var.postgresql.units
}

module "traefik" {
  source = "git::https://github.com/canonical/traefik-k8s-operator.git//terraform?ref=abd922dd7605d7d5b8cdfd0956b1efe47e5649cc"
  count  = local.deploy_traefik ? 1 : 0

  app_name           = "traefik-k8s"
  model_uuid         = juju_model.this.uuid
  base               = var.traefik.base
  channel            = local.traefik_channel
  config             = var.traefik.config
  constraints        = var.traefik.constraints
  resources          = var.traefik.resources
  revision           = var.traefik.revision
  storage_directives = var.traefik.storage_directives
  units              = var.traefik.units
}

resource "juju_integration" "database" {
  model_uuid = juju_model.this.uuid

  application {
    name     = module.ranger.requires["database"].name
    endpoint = module.ranger.requires["database"].endpoint
  }

  application {
    name      = one(module.postgresql[*].app_name)
    endpoint  = one(module.postgresql[*].provides.database)
    offer_url = var.database_offer
  }
}

resource "juju_integration" "ingress" {
  count = local.deploy_traefik || var.ingress_offer != null ? 1 : 0

  model_uuid = juju_model.this.uuid

  application {
    name     = module.ranger.requires["ingress"].name
    endpoint = module.ranger.requires["ingress"].endpoint
  }

  application {
    name      = one(module.traefik[*].app_name)
    endpoint  = one(module.traefik[*].provides.ingress)
    offer_url = var.ingress_offer
  }
}

resource "juju_integration" "grafana_dashboard" {
  count = var.grafana_dashboard_offer == null ? 0 : 1

  model_uuid = juju_model.this.uuid

  application {
    name     = module.ranger.provides["grafana_dashboard"].name
    endpoint = module.ranger.provides["grafana_dashboard"].endpoint
  }

  application {
    offer_url = var.grafana_dashboard_offer
  }
}

resource "juju_integration" "log_proxy" {
  count = var.log_proxy_offer == null ? 0 : 1

  model_uuid = juju_model.this.uuid

  application {
    name     = module.ranger.requires["log_proxy"].name
    endpoint = module.ranger.requires["log_proxy"].endpoint
  }

  application {
    offer_url = var.log_proxy_offer
  }
}

resource "juju_integration" "metrics_endpoint" {
  count = var.metrics_endpoint_offer == null ? 0 : 1

  model_uuid = juju_model.this.uuid

  application {
    name     = module.ranger.provides["metrics_endpoint"].name
    endpoint = module.ranger.provides["metrics_endpoint"].endpoint
  }

  application {
    offer_url = var.metrics_endpoint_offer
  }
}

resource "juju_integration" "opensearch" {
  count = var.opensearch_offer == null ? 0 : 1

  model_uuid = juju_model.this.uuid

  application {
    name     = module.ranger.requires["opensearch"].name
    endpoint = module.ranger.requires["opensearch"].endpoint
  }

  application {
    offer_url = var.opensearch_offer
  }
}

resource "time_static" "deployed_at" {}

resource "time_static" "updated_at" {
  triggers = {
    database_offer          = jsonencode(var.database_offer)
    grafana_dashboard_offer = jsonencode(var.grafana_dashboard_offer)
    ingress_offer           = jsonencode(var.ingress_offer)
    log_proxy_offer         = jsonencode(var.log_proxy_offer)
    logging_config          = var.logging_config
    metrics_endpoint_offer  = jsonencode(var.metrics_endpoint_offer)
    model_name              = var.model_name
    opensearch_offer        = jsonencode(var.opensearch_offer)
    postgresql              = jsonencode(var.postgresql)
    postgresql_channel      = local.postgresql_channel
    postgresql_revision     = coalesce(var.postgresql.revision, -1)
    proxy                   = jsonencode(var.proxy)
    ranger                  = jsonencode(var.ranger)
    ranger_channel          = local.ranger_channel
    ranger_revision         = coalesce(var.ranger.revision, -1)
    risk                    = var.risk
    traefik                 = jsonencode(var.traefik)
    traefik_channel         = local.traefik_channel
    traefik_revision        = coalesce(var.traefik.revision, -1)
  }
}
