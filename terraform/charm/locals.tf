# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

locals {
  expose_blocks = var.expose == null ? [] : [var.expose]

  provides_endpoints = {
    grafana_dashboard = "grafana-dashboard"
    metrics_endpoint  = "metrics-endpoint"
    policy            = "policy"
  }

  requires_endpoints = {
    database      = "database"
    ingress       = "ingress"
    ldap          = "ldap"
    log_proxy     = "log-proxy"
    opensearch    = "opensearch"
    trino_catalog = "trino-catalog"
  }
}
