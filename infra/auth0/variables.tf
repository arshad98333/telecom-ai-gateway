variable "auth0_domain" {
  description = "Auth0 tenant domain, e.g. acme-dev.eu.auth0.com"
  type        = string
}

variable "auth0_management_client_id" {
  description = "Client id of the Management API application Terraform uses"
  type        = string
}

variable "auth0_management_client_secret" {
  description = "Client secret for that application"
  type        = string
  sensitive   = true
}

variable "environment" {
  description = "dev, staging or production. Used in names so a tenant is never ambiguous."
  type        = string

  validation {
    condition     = contains(["dev", "staging", "production"], var.environment)
    error_message = "environment must be dev, staging or production."
  }
}

variable "api_identifier" {
  description = "The API audience. Tokens are minted for this value and rejected elsewhere."
  type        = string
  default     = "https://api.telecom.example/v1"
}

variable "claim_namespace" {
  description = "Namespace for custom claims. Must match TELECOM_MW_CLAIM_NAMESPACE."
  type        = string
  default     = "https://telecom.example/"

  validation {
    condition     = endswith(var.claim_namespace, "/")
    error_message = "claim_namespace must end with a slash, or every claim key silently changes."
  }
}

variable "console_callback_urls" {
  description = "Where the agent and supervisor console may receive a login callback"
  type        = list(string)
  default     = []
}

variable "token_lifetime_seconds" {
  description = "Access token lifetime. Short, because a leaked token is only useful while valid."
  type        = number
  default     = 900

  validation {
    condition     = var.token_lifetime_seconds > 0 && var.token_lifetime_seconds <= 3600
    error_message = "the API refuses tokens living longer than an hour, so this must not exceed 3600."
  }
}
