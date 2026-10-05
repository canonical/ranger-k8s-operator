# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

output "metadata" {
  description = "Module version and deployment timestamps."
  value = {
    version     = local.module_version
    deployed_at = time_static.deployed_at.rfc3339
    updated_at  = time_static.updated_at.rfc3339
  }
}

output "models" {
  description = "The created model UUID and the application object of every deployed component."
  value = {
    ranger = {
      model_uuid = juju_model.this.uuid
      components = local.components
    }
  }
}

output "offers" {
  description = "Offer URLs exported by the product."
  value = {
    policy = module.ranger.offers["policy"].url
  }
}
