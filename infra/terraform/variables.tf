variable "environment" {
  type = string
  validation {
    condition     = contains(["staging", "production"], var.environment)
    error_message = "environment must be staging or production"
  }
}

variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "vpc_id" {
  type = string
}

variable "private_subnet_ids" {
  type = list(string)
}

variable "public_subnet_ids" {
  type = list(string)
}

variable "api_image" {
  type        = string
  description = "Immutable API image reference including sha256 digest."
  validation {
    condition     = strcontains(var.api_image, "@sha256:")
    error_message = "api_image must use an immutable digest"
  }
}

variable "worker_image" {
  type        = string
  description = "Immutable worker image reference including sha256 digest."
  validation {
    condition     = strcontains(var.worker_image, "@sha256:")
    error_message = "worker_image must use an immutable digest"
  }
}

variable "frontend_image" {
  type        = string
  description = "Immutable frontend image reference including sha256 digest."
  validation {
    condition     = strcontains(var.frontend_image, "@sha256:")
    error_message = "frontend_image must use an immutable digest"
  }
}

variable "backend_internal_url" {
  type        = string
  description = "Private API origin used by the frontend server-side proxy."
  default     = "http://rank-rent-api:8000"
}

variable "oidc_issuer" {
  type = string
}

variable "oidc_audience" {
  type = string
}

variable "oidc_jwks_host" {
  type = string
}

variable "cors_origin" {
  type = string
}

variable "release_git_sha" {
  type = string
}

variable "max_scan_cost_usd" {
  type    = number
  default = 10
}
