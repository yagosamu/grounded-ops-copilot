variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "name" {
  type    = string
  default = "grounded-ops-prod"

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
  description = "Trusted HTTPS JWT issuer; signing keys are never Terraform inputs."

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

variable "alarm_topic_arn" {
  type        = string
  description = "Existing SNS topic with a verified on-call subscription; not created here."

  validation {
    condition     = can(regex("^arn:aws:sns:[a-z0-9-]+:[0-9]{12}:[A-Za-z0-9_-]+$", var.alarm_topic_arn))
    error_message = "alarm_topic_arn must reference an SNS topic"
  }
}

variable "image_digest" {
  type        = string
  default     = ""
  description = "Published ECR image digest; services remain off until supplied."

  validation {
    condition     = var.image_digest == "" || can(regex("^sha256:[a-f0-9]{64}$", var.image_digest))
    error_message = "image_digest must be an immutable sha256 digest"
  }
}

variable "enable_services" {
  type        = bool
  default     = false
  description = "Enable only after image, secrets, migrations and recovery prerequisites are verified."
}
