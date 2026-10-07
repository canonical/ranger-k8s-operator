# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

output "app_name" {
  value = juju_application.k8s_postgresql.name
}

# Only addition to the upstream interface.
output "application" {
  description = "The deployed postgresql-k8s application."
  value       = juju_application.k8s_postgresql
}

output "provides" {
  value = {
    database          = "database"
    metrics_endpoint  = "metrics-endpoint"
    grafana_dashboard = "grafana-dashboard"
  }
}

output "requires" {
  value = {
    logging       = "logging"
    certificates  = "certificates"
    s3_parameters = "s3-parameters"
  }
}
