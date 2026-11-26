variable "auth_issuer" {
  type        = string
  default     = null
  description = "Public HTTPS origin of the OAuth Worker, without a trailing slash."
  validation {
    condition     = var.auth_issuer == null ? !local.oauth_enabled : can(regex("^https://[A-Za-z0-9.-]+$", var.auth_issuer))
    error_message = "auth_issuer must be an HTTPS origin without a path; it is required for the paid profile."
  }
}

variable "tams_audience" {
  type        = string
  default     = null
  description = "Public HTTPS origin of the TAMS Worker, without a trailing slash."
  validation {
    condition     = var.tams_audience == null ? !local.oauth_enabled : can(regex("^https://[A-Za-z0-9.-]+$", var.tams_audience))
    error_message = "tams_audience must be an HTTPS origin without a path; it is required for the paid profile."
  }
}

variable "auth_worker_name" {
  type        = string
  default     = null
  description = "Name for the OAuth Worker and its D1 database. Null uses the release defaults."
}

variable "auth_rate_limit_namespace_id" {
  type        = string
  default     = null
  description = "Use a distinct numeric rate-limit namespace for each issuer in the same Cloudflare account."
  validation {
    condition     = var.auth_rate_limit_namespace_id == null || can(regex("^[1-9][0-9]*$", var.auth_rate_limit_namespace_id))
    error_message = "The rate-limit namespace ID must be a positive integer."
  }
}

resource "cloudflare_d1_database" "auth" {
  account_id = var.account_id
  name       = coalesce(var.auth_worker_name, local.auth_worker.d1_databases[0].database_name)
  read_replication = {
    mode = "disabled"
  }
  lifecycle {
    enabled         = local.oauth_enabled
    prevent_destroy = true
  }
}

locals {
  auth_worker_name = coalesce(var.auth_worker_name, local.auth_worker.name)
  auth_worker      = jsondecode(file("${path.module}/../${local.runtime_dir}/auth/wrangler.json"))
}

resource "local_file" "auth_config" {
  lifecycle {
    enabled = local.oauth_enabled
  }
  filename        = "${path.module}/../auth/wrangler.deploy.json"
  file_permission = "0644"
  content = jsonencode(merge(local.auth_worker, {
    name       = local.auth_worker_name
    ratelimits = [for limit in local.auth_worker.ratelimits : merge(limit, { namespace_id = coalesce(var.auth_rate_limit_namespace_id, limit.namespace_id) })]
    account_id = var.account_id
    main       = "../${local.runtime_dir}/auth/${local.auth_worker.main}"
    base_dir   = "../${local.runtime_dir}/auth"
    d1_databases = [{
      binding        = "DB"
      database_name  = cloudflare_d1_database.auth.name
      database_id    = cloudflare_d1_database.auth.id
      migrations_dir = "../${local.runtime_dir}/auth/migrations"
      remote         = true
    }]
    vars = merge(local.auth_worker.vars, {
      AUTH_ISSUER   = var.auth_issuer
      TAMS_AUDIENCE = var.tams_audience
      CORS_ORIGINS  = jsonencode(var.api_cors.origins)
    })
  }))
}
