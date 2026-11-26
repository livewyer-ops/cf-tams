terraform {
  required_version = "~> 1.12.0"
  required_providers {
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "5.25.0"
    }
    local = {
      source  = "hashicorp/local"
      version = "2.6.2"
    }
  }
}

provider "cloudflare" {}

variable "deployment_profile" {
  type        = string
  default     = "paid"
  nullable    = false
  description = "free uses static HTTP Basic authentication; paid uses the OAuth issuer. Does not change the Cloudflare subscription."
  validation {
    condition     = contains(["free", "paid"], var.deployment_profile)
    error_message = "deployment_profile must be free or paid."
  }
}

variable "account_id" {
  type        = string
  description = "Cloudflare account containing the Worker and R2 bucket."
  validation {
    condition     = can(regex("^[0-9a-f]{32}$", var.account_id))
    error_message = "account_id must be a 32-character hexadecimal Cloudflare account ID."
  }
}

variable "webhook_allowed_hosts" {
  type        = list(string)
  default     = []
  nullable    = false
  description = "Webhook receiver hostnames or *.example.com subdomain patterns. Empty rejects all webhook registration."
  validation {
    condition     = alltrue([for host in var.webhook_allowed_hosts : try(trimspace(host) != "", false)])
    error_message = "Webhook allowlist entries must not be null, empty or whitespace-only."
  }
}

variable "storage_backends" {
  description = "Private R2 backends keyed by Worker binding; identities must remain stable."
  nullable    = false
  type = map(object({
    bucket_name     = string
    storage_id      = string
    label           = string
    tags            = optional(map(list(string)), {})
    default_storage = optional(bool, false)
  }))
  default = {
    MEDIA = {
      bucket_name     = "cf-tams-media"
      storage_id      = "7c21bd04-5a9a-4be0-9a31-3a95cd60c493"
      label           = "cloudflare:r2"
      default_storage = true
    }
  }
  validation {
    condition = (
      length([for backend in var.storage_backends : backend if backend.default_storage]) == 1 &&
      length(distinct([for backend in var.storage_backends : lower(backend.storage_id)])) == length(var.storage_backends) &&
      length(distinct([for backend in var.storage_backends : backend.bucket_name])) == length(var.storage_backends) &&
      length(distinct([for backend in var.storage_backends : backend.label])) == length(var.storage_backends) &&
      alltrue([for binding, backend in var.storage_backends :
        can(regex("^[A-Z][A-Z0-9_]*$", binding)) &&
        !contains(["AUTH", "AUTH_MODE", "BASIC_AUTH_USERNAME", "BASIC_AUTH_PASSWORD", "STORE", "API_TOKEN", "STORE_ID", "STORAGE_BACKENDS", "API_CORS", "R2_ENDPOINT", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "WEBHOOK_ALLOWED_HOSTS"], binding) &&
        can(regex("^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$", backend.bucket_name)) &&
        can(regex("(?i)^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", backend.storage_id)) &&
        trimspace(backend.label) != ""
      ])
    )
    error_message = "Backends need valid bindings, bucket names and UUIDs, unique IDs, buckets and labels, and exactly one default."
  }
}

variable "media_origins" {
  type        = list(string)
  default     = []
  description = "Browser origins allowed to use R2 signed URLs. Empty disables browser cross-origin access."
  nullable    = false
  validation {
    condition = alltrue([for origin in var.media_origins : origin == "*" || (
      can(regex("^https?://([A-Za-z0-9.-]+|\\[[0-9A-Fa-f:]+\\])(:[0-9]{1,5})?$", origin)) &&
      try(tonumber(regex(":([0-9]+)$", origin)[0]) > 0 && tonumber(regex(":([0-9]+)$", origin)[0]) <= 65535, true)
    )])
    error_message = "Media origins must be * or HTTP(S) origins without paths, credentials, queries or fragments."
  }
}

resource "cloudflare_r2_bucket" "media" {
  for_each      = var.storage_backends
  account_id    = var.account_id
  name          = each.value.bucket_name
  storage_class = "Standard"

  lifecycle {
    prevent_destroy = true
  }
}

resource "cloudflare_r2_managed_domain" "media" {
  for_each    = var.storage_backends
  account_id  = var.account_id
  bucket_name = cloudflare_r2_bucket.media[each.key].name
  enabled     = false
}

resource "cloudflare_r2_bucket_cors" "media" {
  for_each    = { for binding, backend in var.storage_backends : binding => backend if length(var.media_origins) > 0 }
  account_id  = var.account_id
  bucket_name = cloudflare_r2_bucket.media[each.key].name
  rules = [{
    id = "media-clients"
    allowed = {
      origins = var.media_origins
      methods = var.media_cors.methods
      headers = var.media_cors.headers
    }
    expose_headers  = var.media_cors.expose_headers
    max_age_seconds = var.media_cors.max_age_seconds
  }]
}

locals {
  runtime_lock  = jsondecode(file("${path.module}/../runtime.lock.json"))
  runtime_dir   = ".runtime/${local.runtime_lock.sha256}"
  worker        = jsondecode(file("${path.module}/../${local.runtime_dir}/api/wrangler.json"))
  oauth_enabled = var.deployment_profile == "paid"
}

resource "local_file" "worker_config" {
  filename        = "${path.module}/../wrangler.deploy.json"
  file_permission = "0644"
  content = jsonencode(merge(local.worker, {
    account_id = var.account_id
    name       = coalesce(var.wasm_worker_name, local.worker.name)
    main       = "${local.runtime_dir}/api/${local.worker.main}"
    base_dir   = "${local.runtime_dir}/api"
    routes     = [for domain in var.wasm_custom_domains : { pattern = domain, custom_domain = true }]
    services   = local.oauth_enabled ? [{ binding = "AUTH", service = local.auth_worker_name, entrypoint = "TokenVerifier" }] : []
    secrets = {
      required = concat(["R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY"], local.oauth_enabled ? [] : ["BASIC_AUTH_USERNAME", "BASIC_AUTH_PASSWORD"])
    }
    r2_buckets = [for binding, bucket in cloudflare_r2_bucket.media : { binding = binding, bucket_name = bucket.name }]
    vars = merge(local.worker.vars, {
      AUTH_MODE             = local.oauth_enabled ? "oauth2" : "basic"
      STORE_ID              = coalesce(var.wasm_store_id, local.worker.vars.STORE_ID)
      ANONYMOUS_READ        = tostring(var.wasm_anonymous_read)
      API_CORS              = jsonencode(var.api_cors)
      STORAGE_BACKENDS      = jsonencode(var.storage_backends)
      R2_ENDPOINT           = "https://${var.account_id}.r2.cloudflarestorage.com"
      WEBHOOK_ALLOWED_HOSTS = jsonencode(var.webhook_allowed_hosts)
    })
  }))
}

moved {
  from = cloudflare_r2_bucket.media
  to   = cloudflare_r2_bucket.media["MEDIA"]
}

moved {
  from = cloudflare_r2_managed_domain.media
  to   = cloudflare_r2_managed_domain.media["MEDIA"]
}

moved {
  from = cloudflare_r2_bucket_cors.media[0]
  to   = cloudflare_r2_bucket_cors.media["MEDIA"]
}
