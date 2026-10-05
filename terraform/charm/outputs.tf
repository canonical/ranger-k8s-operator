# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

output "application" {
  description = "The deployed ranger-k8s application."
  value       = juju_application.this
}

output "offers" {
  description = "Map of the created offers, keyed by the offered endpoint name."
  value = {
    for endpoint, offer in juju_offer.this : endpoint => {
      kind = "offer"
      url  = offer.url
    }
  }
}

output "provides" {
  description = "Map of the provides endpoints of the charm."
  value = {
    for key, endpoint in local.provides_endpoints : key => {
      kind     = "endpoint"
      name     = juju_application.this.name
      endpoint = endpoint
    }
  }
}

output "requires" {
  description = "Map of the requires endpoints of the charm."
  value = {
    for key, endpoint in local.requires_endpoints : key => {
      kind     = "endpoint"
      name     = juju_application.this.name
      endpoint = endpoint
    }
  }
}
