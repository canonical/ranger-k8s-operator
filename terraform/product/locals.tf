# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

locals {
  components = merge(
    { ranger = module.ranger.application },
    { for application in module.postgresql[*].application : "postgresql" => application },
    { for application in module.traefik[*].application : "traefik" => application },
  )

  deploy_postgresql = var.database_offer == null
  deploy_traefik    = var.traefik.enabled && var.ingress_offer == null

  module_version = "1.0.0"

  postgresql_channel = coalesce(var.postgresql.channel, "14/${var.risk}")
  ranger_channel     = coalesce(var.ranger.channel, "latest/${var.risk}")
  traefik_channel    = coalesce(var.traefik.channel, "latest/${var.risk}")
}
