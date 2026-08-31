# Pinned, because an Auth0 tenant is not a place to discover that a provider changed
# its defaults. Upgrades happen deliberately, in their own commit, with a plan reviewed.
terraform {
  required_version = ">= 1.9.0, < 2.0.0"

  required_providers {
    auth0 = {
      source  = "auth0/auth0"
      version = "~> 1.11"
    }
  }

  # State holds client identifiers and the shape of the tenant. Keep it remote,
  # encrypted and locked; a local state file on one laptop is how two people apply
  # conflicting changes to production, and how a tenant becomes unreproducible when
  # that laptop dies.
  #
  # Azure Blob Storage encrypts at rest by default and takes a blob lease for the
  # duration of an apply, which is the locking Terraform needs - so unlike S3 there is
  # no second resource to provision for it.
  #
  # Filled per environment by `terraform init -backend-config=envs/<env>.backend`.
  backend "azurerm" {}
}

provider "auth0" {
  domain        = var.auth0_domain
  client_id     = var.auth0_management_client_id
  client_secret = var.auth0_management_client_secret
  debug         = false
}
