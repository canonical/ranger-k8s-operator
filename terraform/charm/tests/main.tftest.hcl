# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

# Plan runs assert on values known before apply; the revision stays unknown at plan time,
# so it is checked on the input variable.

run "setup" {
  module {
    source = "./tests/setup"
  }
}

run "default_plan" {
  command = plan

  variables {
    model_uuid = run.setup.model_uuid
  }

  assert {
    condition = alltrue([
      output.application.name == "ranger-k8s",
      output.application.units == 1,
      output.application.charm[0].channel == "latest/edge",
      var.revision == null,
      length(output.application.config) == 0,
      length(output.application.resources) == 0,
      length(output.offers) == 0,
    ])
    error_message = "default inputs did not produce the expected application"
  }

  assert {
    condition = alltrue([
      toset(keys(output.provides)) == toset(["grafana_dashboard", "metrics_endpoint", "policy"]),
      toset(keys(output.requires)) == toset(["database", "ingress", "ldap", "log_proxy", "opensearch", "trino_catalog"]),
      alltrue([for endpoint in values(output.provides) : endpoint.kind == "endpoint" && endpoint.name == "ranger-k8s"]),
      alltrue([for endpoint in values(output.requires) : endpoint.kind == "endpoint" && endpoint.name == "ranger-k8s"]),
      output.provides.policy.endpoint == "policy",
      output.requires.log_proxy.endpoint == "log-proxy",
      output.requires.trino_catalog.endpoint == "trino-catalog",
    ])
    error_message = "provides and requires outputs did not match the charm endpoints"
  }
}

run "config_passthrough_plan" {
  command = plan

  variables {
    model_uuid = run.setup.model_uuid
    config = {
      for name, option in yamldecode(file("../../charmcraft.yaml")).config.options :
      name => option.type == "boolean" ? "false" : (option.type == "int" ? "1" : "value")
    }
  }

  assert {
    condition     = length(var.config) == length(yamldecode(file("../../charmcraft.yaml")).config.options)
    error_message = "config did not cover every charm option"
  }

  assert {
    condition     = alltrue([for key, value in var.config : output.application.config[key] == value])
    error_message = "config values did not reach the application unchanged"
  }
}

run "resources_override_plan" {
  command = plan

  variables {
    model_uuid = run.setup.model_uuid
    resources  = { "ranger-image" = "docker.io/example/ranger:test" }
  }

  assert {
    condition     = output.application.resources["ranger-image"] == "docker.io/example/ranger:test"
    error_message = "ranger-image resource did not reach the application"
  }
}

run "invalid_offered_endpoint" {
  command = plan

  variables {
    model_uuid        = run.setup.model_uuid
    offered_endpoints = ["database"]
  }

  expect_failures = [var.offered_endpoints]
}

# Ranger may remain waiting for its database; only the offers are asserted.
run "offers_apply" {
  command = apply

  variables {
    model_uuid        = run.setup.model_uuid
    offered_endpoints = ["policy", "metrics-endpoint"]
  }

  assert {
    condition     = output.application.name == "ranger-k8s"
    error_message = "application was not deployed"
  }

  assert {
    condition = alltrue([
      toset(keys(output.offers)) == toset(["policy", "metrics-endpoint"]),
      output.offers.policy.url != "",
      output.offers["metrics-endpoint"].url != "",
      output.offers.policy.url != output.offers["metrics-endpoint"].url,
    ])
    error_message = "offers were missing, empty or not distinct"
  }
}
