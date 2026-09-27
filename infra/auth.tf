variable "auth_issuer" {
  type        = string
  description = "Public HTTPS origin of the OAuth Worker, without a trailing slash."
  validation {
    condition     = can(regex("^https://[A-Za-z0-9.-]+$", var.auth_issuer))
    error_message = "auth_issuer must be an HTTPS origin without a path."
  }
}

variable "tams_audience" {
  type        = string
  description = "Public HTTPS origin of the TAMS Worker, without a trailing slash."
  validation {
    condition     = can(regex("^https://[A-Za-z0-9.-]+$", var.tams_audience))
    error_message = "tams_audience must be an HTTPS origin without a path."
  }
}

resource "cloudflare_d1_database" "auth" {
  account_id = var.account_id
  name       = "cf-tams-auth"
  read_replication = {
    mode = "disabled"
  }
  lifecycle {
    prevent_destroy = true
  }
}

locals {
  auth_worker = jsondecode(file("${path.module}/../auth/wrangler.jsonc"))
}

resource "local_file" "auth_config" {
  filename        = "${path.module}/../auth/wrangler.deploy.json"
  file_permission = "0644"
  content = jsonencode(merge(local.auth_worker, {
    account_id = var.account_id
    d1_databases = [{
      binding        = "DB"
      database_name  = cloudflare_d1_database.auth.name
      database_id    = cloudflare_d1_database.auth.id
      migrations_dir = "migrations"
      remote         = true
    }]
    vars = merge(local.auth_worker.vars, {
      AUTH_ISSUER   = var.auth_issuer
      TAMS_AUDIENCE = var.tams_audience
      CORS_ORIGINS  = jsonencode(var.api_cors.origins)
    })
  }))
}
