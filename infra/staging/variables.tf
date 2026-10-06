variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "name" {
  type    = string
  default = "grounded-ops-pilot"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,23}$", var.name))
    error_message = "name must be a short lowercase AWS resource prefix"
  }
}

variable "certificate_arn" {
  type        = string
  description = "ACM certificate ARN for the public HTTPS listener, in aws_region."

  validation {
    condition     = can(regex("^arn:aws:acm:[a-z0-9-]+:[0-9]{12}:certificate/", var.certificate_arn))
    error_message = "certificate_arn must be an ACM certificate ARN"
  }
}

variable "jwt_issuer" {
  type        = string
  description = "Trusted JWT issuer URL; no signing or verification key is stored in Terraform."

  validation {
    condition     = startswith(var.jwt_issuer, "https://")
    error_message = "jwt_issuer must use HTTPS"
  }
}

variable "jwt_audience" {
  type = string

  validation {
    condition     = length(trimspace(var.jwt_audience)) > 0
    error_message = "jwt_audience must not be empty"
  }
}

variable "image_digest" {
  type        = string
  default     = ""
  description = "Published ECR image digest, supplied only when enable_services is true."

  validation {
    condition     = var.image_digest == "" || can(regex("^sha256:[a-f0-9]{64}$", var.image_digest))
    error_message = "image_digest must be an immutable sha256 digest"
  }
}

variable "enable_services" {
  type        = bool
  default     = false
  description = "Enable only after publishing an image and populating the three application secrets."
}
