variable "api_cors" {
  description = "Browser CORS policy for the TAMS API. Empty origins disables CORS."
  type = object({
    origins = optional(list(string), [])
    methods = optional(list(string), ["GET", "HEAD", "POST", "PUT", "DELETE", "OPTIONS"])
    headers = optional(list(string), ["Authorization", "Content-Type"])
    expose_headers = optional(list(string), [
      "Location", "Link", "WWW-Authenticate", "X-Paging-Limit", "X-Paging-Count",
      "X-Paging-Reverse-Order", "X-Paging-NextKey", "X-Paging-Timerange"
    ])
    max_age_seconds   = optional(number, 3600)
    allow_credentials = optional(bool, false)
  })
  default  = {}
  nullable = false

  validation {
    condition = alltrue([for origin in var.api_cors.origins : origin == "*" || (
      can(regex("^https?://([A-Za-z0-9.-]+|\\[[0-9A-Fa-f:]+\\])(:[0-9]{1,5})?$", origin)) &&
      try(tonumber(regex(":([0-9]+)$", origin)[0]) > 0 && tonumber(regex(":([0-9]+)$", origin)[0]) <= 65535, true)
    )])
    error_message = "API origins must be * or HTTP(S) origins without paths, credentials, queries or fragments."
  }
  validation {
    condition     = !var.api_cors.allow_credentials || !contains(var.api_cors.origins, "*")
    error_message = "Credentialed API CORS requires explicit origins."
  }
  validation {
    condition     = alltrue([for method in var.api_cors.methods : contains(["GET", "HEAD", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"], method)])
    error_message = "API CORS methods must be uppercase HTTP method names."
  }
  validation {
    condition     = alltrue([for header in concat(var.api_cors.headers, var.api_cors.expose_headers) : can(regex("^[!#$%&'*+.^_`|~0-9A-Za-z-]+$", header))])
    error_message = "API CORS headers must be HTTP header names or *."
  }
  validation {
    condition     = var.api_cors.max_age_seconds >= 0 && floor(var.api_cors.max_age_seconds) == var.api_cors.max_age_seconds
    error_message = "API CORS max_age_seconds must be a non-negative integer."
  }
}

variable "media_cors" {
  description = "R2 CORS settings applied to media_origins."
  type = object({
    methods         = optional(list(string), ["GET", "HEAD", "PUT"])
    headers         = optional(list(string), ["Content-Type", "If-None-Match", "Range"])
    expose_headers  = optional(list(string), ["ETag", "Content-Length", "Content-Range", "Accept-Ranges"])
    max_age_seconds = optional(number, 3600)
  })
  default  = {}
  nullable = false

  validation {
    condition     = length(var.media_cors.methods) > 0 && alltrue([for method in var.media_cors.methods : contains(["GET", "HEAD", "PUT", "POST", "DELETE"], method)])
    error_message = "R2 CORS requires at least one of GET, HEAD, PUT, POST or DELETE."
  }
  validation {
    condition     = alltrue([for header in concat(var.media_cors.headers, var.media_cors.expose_headers) : can(regex("^[!#$%&'*+.^_`|~0-9A-Za-z-]+$", header))])
    error_message = "R2 CORS headers must be HTTP header names or *."
  }
  validation {
    condition     = var.media_cors.max_age_seconds >= 0 && var.media_cors.max_age_seconds <= 86400 && floor(var.media_cors.max_age_seconds) == var.media_cors.max_age_seconds
    error_message = "R2 CORS max_age_seconds must be an integer between 0 and 86400."
  }
}
