# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

variable "database_offer" {
  description = "URL of a PostgreSQL offer. When set, PostgreSQL is not deployed and Ranger integrates with this offer."
  type        = string
  default     = null
}

variable "grafana_dashboard_offer" {
  description = "URL of an offer to integrate with the Ranger grafana-dashboard endpoint."
  type        = string
  default     = null
}

variable "ingress_offer" {
  description = "URL of an ingress offer. When set, Traefik is not deployed and Ranger integrates with this offer."
  type        = string
  default     = null
}

variable "log_proxy_offer" {
  description = "URL of an offer to integrate with the Ranger log-proxy endpoint."
  type        = string
  default     = null
}

variable "logging_config" {
  description = "Logging configuration of the model, e.g. <root>=WARNING."
  type        = string
  nullable    = false
}

variable "metrics_endpoint_offer" {
  description = "URL of an offer to integrate with the Ranger metrics-endpoint endpoint."
  type        = string
  default     = null
}

variable "model_name" {
  description = "Name of the Juju model to create."
  type        = string
  nullable    = false

  validation {
    condition     = length(var.model_name) > 0
    error_message = "model_name must not be empty."
  }
}

variable "opensearch_offer" {
  description = "URL of an offer to integrate with the Ranger opensearch endpoint."
  type        = string
  default     = null
}

variable "postgresql" {
  description = "Settings of the bundled PostgreSQL application. A null channel defaults to 14/<risk>."
  type = object({
    base               = optional(string, "ubuntu@22.04")
    channel            = optional(string)
    config             = optional(map(string), {})
    constraints        = optional(string, "")
    resources          = optional(map(string), {})
    revision           = optional(number)
    storage_directives = optional(map(string), { pgdata = "10G" })
    units              = optional(number, 1)
  })
  default  = {}
  nullable = false
}

variable "proxy" {
  description = "Proxy settings of the model. Null fields are left out of the model configuration."
  type = object({
    http     = optional(string)
    https    = optional(string)
    no_proxy = optional(string)
  })
  nullable = false
}

variable "ranger" {
  description = "Settings of the Ranger application. A null channel defaults to latest/<risk>. The charm-function option is managed by the module."
  type = object({
    base        = optional(string)
    channel     = optional(string)
    config      = optional(map(string), {})
    constraints = optional(string)
    endpoint_bindings = optional(set(object({
      space    = string
      endpoint = optional(string)
    })), [])
    resources = optional(map(string), {})
    revision  = optional(number)
    units     = optional(number, 1)
  })
  default  = {}
  nullable = false

  validation {
    condition     = !contains(keys(var.ranger.config), "charm-function")
    error_message = "ranger.config must not set charm-function; the module deploys Ranger with charm-function=admin."
  }
}

variable "risk" {
  description = "Risk of the default channel of every bundled charm."
  type        = string
  default     = "edge"
  nullable    = false

  validation {
    condition     = contains(["stable", "candidate", "beta", "edge"], var.risk)
    error_message = "risk must be one of stable, candidate, beta, edge."
  }
}

variable "traefik" {
  description = "Settings of the bundled Traefik application. A null channel defaults to latest/<risk>."
  type = object({
    base               = optional(string)
    channel            = optional(string)
    config             = optional(map(string), {})
    constraints        = optional(string, "arch=amd64")
    enabled            = optional(bool, true)
    resources          = optional(map(string), {})
    revision           = optional(number)
    storage_directives = optional(map(string), {})
    units              = optional(number, 1)
  })
  default  = {}
  nullable = false
}
