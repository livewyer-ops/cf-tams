variable "wasm_worker_name" {
  type        = string
  default     = null
  description = "TASM API Worker name. Null preserves the base configuration."
}

variable "wasm_store_id" {
  type        = string
  default     = null
  description = "Stable Wasm store identity. Null preserves the base configuration."
}

variable "wasm_custom_domains" {
  type        = set(string)
  default     = []
  nullable    = false
  description = "Wasm Worker custom hostnames in active Cloudflare zones."
}

variable "wasm_anonymous_read" {
  type        = bool
  default     = false
  nullable    = false
  description = "Allow unauthenticated GET and HEAD requests with read permission on the Wasm API."
}
