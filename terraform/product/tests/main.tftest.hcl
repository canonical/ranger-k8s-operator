# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

# Every run uses its own model name because the product creates its model. Plan runs assert on
# values known before apply; revisions stay unknown at plan time, so they are checked on the
# input variables.

run "default_plan" {
  command = plan

  variables {
    model_name     = "ranger-tf-test-default"
    logging_config = "<root>=WARNING"
    proxy = {
      http     = "http://proxy.example:3128"
      https    = "https://proxy.example:3128"
      no_proxy = "localhost,127.0.0.1"
    }
  }

  assert {
    condition = alltrue([
      juju_model.this.config["juju-http-proxy"] == "http://proxy.example:3128",
      juju_model.this.config["juju-https-proxy"] == "https://proxy.example:3128",
      juju_model.this.config["juju-no-proxy"] == "localhost,127.0.0.1",
      juju_model.this.config["logging-config"] == "<root>=WARNING",
    ])
    error_message = "model config did not include the proxy fields and logging-config"
  }

  assert {
    condition     = length(module.postgresql) == 1 && length(module.traefik) == 1
    error_message = "PostgreSQL and Traefik must be deployed by default"
  }

  assert {
    condition = alltrue([
      module.ranger.application.config["charm-function"] == "admin",
      module.ranger.application.charm[0].channel == "latest/edge",
      module.postgresql[0].application.charm[0].channel == "14/edge",
      module.traefik[0].application.charm[0].channel == "latest/edge",
    ])
    error_message = "default channels or the Ranger charm-function were not as expected"
  }

  assert {
    condition = alltrue([
      var.ranger.revision == null,
      var.postgresql.revision == null,
      var.traefik.revision == null,
    ])
    error_message = "revisions must default to null"
  }

  assert {
    condition = alltrue([
      length(juju_integration.ingress) == 1,
      length(juju_integration.grafana_dashboard) == 0,
      length(juju_integration.log_proxy) == 0,
      length(juju_integration.metrics_endpoint) == 0,
      length(juju_integration.opensearch) == 0,
    ])
    error_message = "only the database and ingress integrations must exist by default"
  }
}

run "external_offers_plan" {
  command = plan

  variables {
    model_name              = "ranger-tf-test-offers"
    logging_config          = "<root>=WARNING"
    proxy                   = {}
    database_offer          = "admin/db.postgresql"
    ingress_offer           = "admin/ingress.traefik"
    grafana_dashboard_offer = "admin/cos.grafana-dashboards"
    log_proxy_offer         = "admin/cos.loki-logging"
    metrics_endpoint_offer  = "admin/cos.prometheus-receive-remote-write"
    opensearch_offer        = "admin/search.opensearch"
  }

  assert {
    condition     = length(module.postgresql) == 0 && length(module.traefik) == 0
    error_message = "supplied offers must replace the bundled PostgreSQL and Traefik"
  }

  assert {
    condition = alltrue([
      length(juju_integration.ingress) == 1,
      length(juju_integration.grafana_dashboard) == 1,
      length(juju_integration.log_proxy) == 1,
      length(juju_integration.metrics_endpoint) == 1,
      length(juju_integration.opensearch) == 1,
    ])
    error_message = "every supplied offer must produce an integration"
  }

  assert {
    condition = alltrue([
      one([for a in juju_integration.database.application : a.offer_url if a.offer_url != null]) == var.database_offer,
      one([for a in juju_integration.ingress[0].application : a.offer_url if a.offer_url != null]) == var.ingress_offer,
      one([for a in juju_integration.grafana_dashboard[0].application : a.offer_url if a.offer_url != null]) == var.grafana_dashboard_offer,
      one([for a in juju_integration.log_proxy[0].application : a.offer_url if a.offer_url != null]) == var.log_proxy_offer,
      one([for a in juju_integration.metrics_endpoint[0].application : a.offer_url if a.offer_url != null]) == var.metrics_endpoint_offer,
      one([for a in juju_integration.opensearch[0].application : a.offer_url if a.offer_url != null]) == var.opensearch_offer,
    ])
    error_message = "integrations did not use the supplied offer URLs"
  }
}

run "traefik_disabled_plan" {
  command = plan

  variables {
    model_name     = "ranger-tf-test-no-traefik"
    logging_config = "<root>=WARNING"
    proxy          = {}
    traefik        = { enabled = false }
  }

  assert {
    condition     = length(module.traefik) == 0 && length(juju_integration.ingress) == 0
    error_message = "disabling Traefik must remove Traefik and the ingress integration"
  }
}

run "invalid_risk" {
  command = plan

  variables {
    model_name     = "ranger-tf-test-invalid-risk"
    logging_config = "<root>=WARNING"
    proxy          = {}
    risk           = "invalid"
  }

  expect_failures = [var.risk]
}

run "default_apply" {
  command = apply

  variables {
    model_name     = "ranger-tf-test-apply"
    logging_config = "<root>=WARNING"
    proxy          = {}
    traefik = {
      config = { external_hostname = "ranger.test" }
    }
  }

  assert {
    condition     = toset(keys(output.models.ranger.components)) == toset(["ranger", "postgresql", "traefik"])
    error_message = "models.ranger.components did not contain exactly the default applications"
  }

  assert {
    condition     = output.offers.policy != ""
    error_message = "policy offer URL was empty after apply"
  }
}

run "wait_for_ranger_active" {
  module {
    source = "./tests/wait_for_active"
  }

  variables {
    model_uuid = run.default_apply.models.ranger.model_uuid
    app_name   = "ranger-k8s"
    timeout    = 1800
  }

  assert {
    condition     = data.external.app_status.result.status == "active"
    error_message = "ranger-k8s did not reach active status"
  }
}

run "wait_for_postgresql_active" {
  module {
    source = "./tests/wait_for_active"
  }

  variables {
    model_uuid = run.default_apply.models.ranger.model_uuid
    app_name   = "postgresql-k8s"
    timeout    = 1800
  }

  assert {
    condition     = data.external.app_status.result.status == "active"
    error_message = "postgresql-k8s did not reach active status"
  }
}

run "wait_for_traefik_active" {
  module {
    source = "./tests/wait_for_active"
  }

  variables {
    model_uuid = run.default_apply.models.ranger.model_uuid
    app_name   = "traefik-k8s"
    timeout    = 1800
  }

  assert {
    condition     = data.external.app_status.result.status == "active"
    error_message = "traefik-k8s did not reach active status"
  }
}
