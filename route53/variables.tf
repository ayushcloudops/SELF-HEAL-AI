variable "region" {
  description = "AWS region for provider"
  type        = string
  default     = "us-east-1"
}


variable "zone_name" {
  description = "(Optional) Domain name for the hosted zone (e.g. example.com). Provide this when you want the module to look up the hosted zone by name."
  type        = string
  default     = ""
}

variable "zone_id" {
  description = "(Optional) Route53 hosted zone ID. If provided this will be used directly and `zone_name` lookup will be skipped."
  type        = string
  default     = ""
}

variable "private_zone" {
  description = "Whether the hosted zone is private (used when looking up by name)."
  type        = bool
  default     = false
}

variable "environment" {
  description = "Environment tag applied to resources"
  type        = string
  default     = "dev"
}

variable "records" {
  description = "Map of DNS records to create. Key = record name (use '@' for root). Value is object with type, ttl, values list."
  type = map(object({
    type   = string
    ttl    = number
    values = list(string)
  }))
  default = {}
}
