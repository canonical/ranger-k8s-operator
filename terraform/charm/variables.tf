# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

variable "app_name" {
  description = "Name to give the deployed application."
  type        = string
  default     = "ranger-k8s"
  nullable    = false

  validation {
    condition     = length(var.app_name) > 0
    error_message = "app_name must not be empty."
  }
}

variable "base" {
  description = "The operating system on which to deploy, e.g. ubuntu@22.04. Null lets Juju choose."
  type        = string
  default     = null
}

variable "channel" {
  description = "Channel that the charm is deployed from."
  type        = string
  default     = "latest/edge"
  nullable    = false
}

variable "config" {
  description = "Map of the charm configuration options. Values are passed unchanged; Juju converts them to the declared option types."
  type        = map(string)
  default     = {}
  nullable    = false
}

variable "constraints" {
  description = "String listing constraints for this application."
  type        = string
  default     = null
}

variable "endpoint_bindings" {
  description = "Endpoint bindings of the application. Each entry maps a Juju space, optionally for one endpoint."
  type = set(object({
    space    = string
    endpoint = optional(string)
  }))
  default  = []
  nullable = false
}

variable "expose" {
  description = "The expose block of the application. Null leaves the application unexposed; an empty object exposes all endpoints."
  type = object({
    cidrs     = optional(string)
    endpoints = optional(string)
    spaces    = optional(string)
  })
  default = null
}

variable "model_uuid" {
  description = "UUID of an existing Juju model to deploy to."
  type        = string
  nullable    = false
}

variable "offered_endpoints" {
  description = "Provides endpoints to expose as Juju offers: policy, metrics-endpoint or grafana-dashboard."
  type        = list(string)
  default     = []
  nullable    = false

  validation {
    condition = alltrue([
      for endpoint in var.offered_endpoints :
      contains(["policy", "metrics-endpoint", "grafana-dashboard"], endpoint)
    ])
    error_message = "offered_endpoints may only contain the provides endpoints: policy, metrics-endpoint, grafana-dashboard."
  }
}

variable "resources" {
  description = "Charm resources, i.e. a resource revision from Charmhub or an OCI image reference, e.g. { \"ranger-image\" = \"<revision or image>\" }."
  type        = map(string)
  default     = {}
  nullable    = false
}

variable "revision" {
  description = "Revision number of the charm. Null deploys the latest revision of the channel."
  type        = number
  default     = null
}

variable "units" {
  description = "Number of units to deploy."
  type        = number
  default     = 1
  nullable    = false

  validation {
    condition     = var.units >= 1
    error_message = "units must be at least 1."
  }
}
