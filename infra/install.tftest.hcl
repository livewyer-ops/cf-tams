mock_provider "cloudflare" {}
mock_provider "local" {}

variables {
  auth_issuer   = "https://auth.example.com"
  tams_audience = "https://tams.example.com"
}

run "deployment_configuration" {
  command = plan

  variables {
    account_id            = "11111111111111111111111111111111"
    api_cors              = {}
    media_origins         = []
    webhook_allowed_hosts = null
  }

  assert {
    condition = (
      length(jsondecode(jsondecode(local_file.worker_config.content).vars.API_CORS).origins) == 0 &&
      length(cloudflare_r2_bucket_cors.media) == 0 &&
      contains(var.media_cors.headers, "If-None-Match") &&
      contains(var.media_cors.methods, "PUT")
    )
    error_message = "CORS must default to disabled, with media defaults compatible with signed uploads."
  }

  assert {
    condition     = jsondecode(local_file.worker_config.content).vars.WEBHOOK_ALLOWED_HOSTS == "[]"
    error_message = "The default webhook allowlist must reject all registrations."
  }

  assert {
    condition = (
      jsondecode(local_file.worker_config.content).account_id == var.account_id &&
      jsondecode(local_file.worker_config.content).vars.R2_ENDPOINT == "https://${var.account_id}.r2.cloudflarestorage.com" &&
      jsondecode(jsondecode(local_file.worker_config.content).vars.STORAGE_BACKENDS).MEDIA.bucket_name == var.storage_backends.MEDIA.bucket_name &&
      jsondecode(local_file.worker_config.content).r2_buckets[0].bucket_name == var.storage_backends.MEDIA.bucket_name
    )
    error_message = "Wrangler must use the same account and bucket as OpenTofu."
  }

  assert {
    condition = (
      jsondecode(local_file.auth_config.content).vars.AUTH_ISSUER == var.auth_issuer &&
      jsondecode(local_file.auth_config.content).vars.TAMS_AUDIENCE == var.tams_audience &&
      jsondecode(local_file.auth_config.content).vars.CORS_ORIGINS == jsonencode(var.api_cors.origins) &&
      jsondecode(local_file.worker_config.content).services[0].entrypoint == "TokenVerifier" &&
      jsondecode(local_file.worker_config.content).vars.AUTH_MODE == "oauth2" &&
      !contains(jsondecode(local_file.worker_config.content).secrets.required, "BASIC_AUTH_PASSWORD") &&
      jsondecode(local_file.worker_config.content).durable_objects == local.worker.durable_objects &&
      jsondecode(local_file.worker_config.content).migrations == local.worker.migrations &&
      jsondecode(local_file.worker_config.content).vars.STORE_ID == local.worker.vars.STORE_ID &&
      jsondecode(jsondecode(local_file.worker_config.content).vars.STORAGE_BACKENDS).MEDIA.storage_id == var.storage_backends["MEDIA"].storage_id
    )
    error_message = "Generating deployment configuration must preserve storage identities and migrations."
  }
}

run "isolated_paid_issuer" {
  command = plan
  variables {
    account_id                   = "11111111111111111111111111111111"
    auth_worker_name             = "isolated-issuer"
    auth_rate_limit_namespace_id = "7214"
  }
  assert {
    condition = (
      cloudflare_d1_database.auth.name == "isolated-issuer" &&
      jsondecode(local_file.auth_config.content).name == "isolated-issuer" &&
      jsondecode(local_file.auth_config.content).d1_databases[0].database_name == "isolated-issuer" &&
      jsondecode(local_file.auth_config.content).ratelimits[0].namespace_id == "7214" &&
      jsondecode(local_file.worker_config.content).services[0].service == "isolated-issuer"
    )
    error_message = "The Paid API must use its own issuer, database and rate-limit namespace."
  }
}

run "free_profile" {
  command = plan
  variables {
    account_id         = "11111111111111111111111111111111"
    deployment_profile = "free"
    auth_issuer        = null
    tams_audience      = null
  }
  assert {
    condition = (
      jsondecode(local_file.worker_config.content).name == local.worker.name &&
      jsondecode(local_file.worker_config.content).durable_objects == local.worker.durable_objects &&
      jsondecode(local_file.worker_config.content).migrations == local.worker.migrations &&
      jsondecode(local_file.worker_config.content).vars.STORE_ID == local.worker.vars.STORE_ID &&
      jsondecode(local_file.worker_config.content).vars.AUTH_MODE == "basic" &&
      jsondecode(local_file.worker_config.content).vars.ANONYMOUS_READ == "false" &&
      endswith(jsondecode(local_file.worker_config.content).main, "/api/worker.mjs") &&
      jsondecode(local_file.worker_config.content).no_bundle &&
      length(jsondecode(local_file.worker_config.content).services) == 0
    )
    error_message = "The Free API Worker must deploy the released module with the expected storage identity."
  }
  assert {
    condition = (
      cloudflare_d1_database.auth == null &&
      local_file.auth_config == null &&
      length(jsondecode(local_file.worker_config.content).services) == 0 &&
      jsondecode(local_file.worker_config.content).vars.AUTH_MODE == "basic" &&
      toset(jsondecode(local_file.worker_config.content).secrets.required) == toset(["R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "BASIC_AUTH_USERNAME", "BASIC_AUTH_PASSWORD"])
    )
    error_message = "Free must require Basic credentials and R2 keys, without an OAuth Worker binding or D1 database."
  }
  assert {
    condition = (
      jsondecode(local_file.worker_config.content).durable_objects == local.worker.durable_objects &&
      jsondecode(local_file.worker_config.content).migrations == local.worker.migrations &&
      jsondecode(local_file.worker_config.content).vars.STORE_ID == local.worker.vars.STORE_ID &&
      jsondecode(local_file.worker_config.content).vars.STORAGE_BACKENDS == jsonencode(var.storage_backends)
    )
    error_message = "Changing authentication must preserve metadata and media identities."
  }
}

run "separate_public_wasm_installation" {
  command = plan
  variables {
    account_id          = "11111111111111111111111111111111"
    deployment_profile  = "free"
    wasm_worker_name    = "public-demo"
    wasm_store_id       = "public-demo"
    wasm_custom_domains = ["api.example.com"]
    wasm_anonymous_read = true
  }
  assert {
    condition = (
      jsondecode(local_file.worker_config.content).name == "public-demo" &&
      jsondecode(local_file.worker_config.content).vars.STORE_ID == "public-demo" &&
      jsondecode(local_file.worker_config.content).vars.ANONYMOUS_READ == "true" &&
      jsondecode(local_file.worker_config.content).routes[0].pattern == "api.example.com" &&
      jsondecode(local_file.worker_config.content).routes[0].custom_domain == true &&
      jsondecode(local_file.worker_config.content).migrations == local.worker.migrations &&
      cloudflare_d1_database.auth == null
    )
    error_message = "A separate public installation must retain migrations and its selected store identity."
  }
}

run "reject_invalid_profile" {
  command = plan
  variables {
    account_id         = "11111111111111111111111111111111"
    deployment_profile = "freee"
  }
  expect_failures = [var.deployment_profile]
}

run "paid_requires_oauth_urls" {
  command = plan
  variables {
    account_id    = "11111111111111111111111111111111"
    auth_issuer   = null
    tams_audience = null
  }
  expect_failures = [var.auth_issuer, var.tams_audience]
}

run "custom_webhook_hosts" {
  command = plan
  variables {
    account_id            = "11111111111111111111111111111111"
    webhook_allowed_hosts = ["hooks.example.com", "*.example.com"]
  }
  assert {
    condition     = jsondecode(local_file.worker_config.content).vars.WEBHOOK_ALLOWED_HOSTS == jsonencode(var.webhook_allowed_hosts)
    error_message = "Exact and wildcard webhook hosts must reach the Worker unchanged."
  }
}

run "reject_null_webhook_host" {
  command = plan
  variables {
    account_id            = "11111111111111111111111111111111"
    webhook_allowed_hosts = [null]
  }
  expect_failures = [var.webhook_allowed_hosts]
}

run "reject_empty_webhook_host" {
  command = plan
  variables {
    account_id            = "11111111111111111111111111111111"
    webhook_allowed_hosts = [""]
  }
  expect_failures = [var.webhook_allowed_hosts]
}

run "reject_blank_webhook_host" {
  command = plan
  variables {
    account_id            = "11111111111111111111111111111111"
    webhook_allowed_hosts = [" \t\n"]
  }
  expect_failures = [var.webhook_allowed_hosts]
}

run "custom_cors" {
  command = plan
  variables {
    account_id    = "11111111111111111111111111111111"
    media_origins = ["https://media.example.com"]
    api_cors = {
      origins           = ["https://editor.example.com"]
      methods           = ["GET", "HEAD"]
      headers           = ["Authorization", "X-Client"]
      expose_headers    = ["Link", "X-Paging-NextKey"]
      max_age_seconds   = 120
      allow_credentials = true
    }
    media_cors = {
      methods         = ["GET", "HEAD"]
      headers         = ["Range"]
      expose_headers  = ["ETag"]
      max_age_seconds = 60
    }
  }
  assert {
    condition     = jsondecode(local_file.worker_config.content).vars.API_CORS == jsonencode(var.api_cors)
    error_message = "The complete API CORS policy must reach the Worker unchanged."
  }
  assert {
    condition = (
      tolist(cloudflare_r2_bucket_cors.media["MEDIA"].rules)[0].allowed.origins == tolist(var.media_origins) &&
      tolist(cloudflare_r2_bucket_cors.media["MEDIA"].rules)[0].allowed.methods == tolist(var.media_cors.methods) &&
      tolist(cloudflare_r2_bucket_cors.media["MEDIA"].rules)[0].allowed.headers == tolist(var.media_cors.headers) &&
      tolist(cloudflare_r2_bucket_cors.media["MEDIA"].rules)[0].expose_headers == tolist(var.media_cors.expose_headers) &&
      tolist(cloudflare_r2_bucket_cors.media["MEDIA"].rules)[0].max_age_seconds == var.media_cors.max_age_seconds
    )
    error_message = "The R2 CORS rule must use the configured media policy."
  }
}

run "reject_credentialed_wildcard" {
  command = plan
  variables {
    account_id = "11111111111111111111111111111111"
    api_cors   = { origins = ["*"], allow_credentials = true }
  }
  expect_failures = [var.api_cors]
}

run "reject_invalid_origins" {
  command = plan
  variables {
    account_id    = "11111111111111111111111111111111"
    api_cors      = { origins = ["https://example.com/path"] }
    media_origins = ["https://user:password@example.com"]
  }
  expect_failures = [var.api_cors, var.media_origins]
}

run "reject_invalid_methods" {
  command = plan
  variables {
    account_id = "11111111111111111111111111111111"
    api_cors   = { methods = ["INVALID"] }
    media_cors = { methods = ["OPTIONS"] }
  }
  expect_failures = [var.api_cors, var.media_cors]
}

run "reject_invalid_cache_duration" {
  command = plan
  variables {
    account_id = "11111111111111111111111111111111"
    api_cors   = { max_age_seconds = -1 }
    media_cors = { max_age_seconds = 0.5 }
  }
  expect_failures = [var.api_cors, var.media_cors]
}

run "multiple_storage_backends" {
  command = plan
  variables {
    account_id    = "11111111111111111111111111111111"
    media_origins = ["https://editor.example.com"]
    storage_backends = {
      MEDIA = {
        storage_id      = "7c21bd04-5a9a-4be0-9a31-3a95cd60c493"
        bucket_name     = "cf-tams-media"
        label           = "cloudflare:r2"
        default_storage = true
      }
      MEDIA_SECONDARY = {
        storage_id  = "22222222-2222-4222-8222-222222222222"
        bucket_name = "cf-tams-media-secondary"
        label       = "cloudflare:r2-secondary"
        tags        = { tier = ["secondary"] }
      }
    }
  }
  assert {
    condition = (
      length(cloudflare_r2_bucket.media) == 2 &&
      length(cloudflare_r2_bucket_cors.media) == 2 &&
      length(jsondecode(local_file.worker_config.content).r2_buckets) == 2 &&
      jsondecode(local_file.worker_config.content).vars.STORAGE_BACKENDS == jsonencode(var.storage_backends) &&
      alltrue([for domain in cloudflare_r2_managed_domain.media : domain.enabled == false])
    )
    error_message = "Every backend must have a private bucket, binding, shared CORS policy and registry entry."
  }
}

run "reject_duplicate_storage_identity" {
  command = plan
  variables {
    account_id = "11111111111111111111111111111111"
    storage_backends = {
      MEDIA = {
        storage_id      = "7c21bd04-5a9a-4be0-9a31-3a95cd60c493"
        bucket_name     = "cf-tams-media"
        label           = "cloudflare:r2"
        default_storage = true
      }
      MEDIA_SECONDARY = {
        storage_id  = "7c21bd04-5a9a-4be0-9a31-3a95cd60c493"
        bucket_name = "cf-tams-media-secondary"
        label       = "cloudflare:r2-secondary"
      }
    }
  }
  expect_failures = [var.storage_backends]
}

run "reject_missing_default_and_invalid_binding" {
  command = plan
  variables {
    account_id = "11111111111111111111111111111111"
    storage_backends = {
      AUTH = {
        storage_id  = "7c21bd04-5a9a-4be0-9a31-3a95cd60c493"
        bucket_name = "cf-tams-media"
        label       = "cloudflare:r2"
      }
    }
  }
  expect_failures = [var.storage_backends]
}
