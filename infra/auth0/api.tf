# The API and its permissions. This resource is the source of truth for what scopes
# exist in the tenant; the same list exists in the service's Scope enum, and a test
# fails if the two drift apart.

locals {
  scopes = {
    "account:read"      = "Read a customer's account record"
    "service:read"      = "Read a customer's active services"
    "order:read"        = "Read a customer's orders"
    "billing:read"      = "Read a customer's invoices"
    "network:read"      = "Read network status for a customer's area"
    "ticket:read"       = "Read support tickets"
    "ticket:write"      = "Raise a support ticket"
    "callback:write"    = "Schedule a callback"
    "refund:request"    = "Ask for a refund to be approved. Moves no money."
    "refund:approve"    = "Decide a pending approval request"
    "case:read"         = "Read voice case state"
    "case:write"        = "Record voice case state"
    "audit:read"        = "Read the audit trail"
    "config:read"       = "Read security configuration"
    "config:write"      = "Change security configuration"
    "assignment:read"   = "Read which accounts an agent may act on"
    "assignment:write"  = "Assign and revoke account access"
  }
}

resource "auth0_resource_server" "telecom_api" {
  name       = "Telecom middleware API (${var.environment})"
  identifier = var.api_identifier

  signing_alg = "RS256"

  # Short-lived tokens. The middleware refuses anything longer than an hour regardless,
  # so a mismatch here fails closed rather than widening the window.
  token_lifetime         = var.token_lifetime_seconds
  token_lifetime_for_web = var.token_lifetime_seconds
  skip_consent_for_verifiable_first_party_clients = true
  allow_offline_access                            = false

  # RBAC on, and permissions carried in the access token. Without the second setting the
  # API would have to call the Management API on every request to discover what the
  # caller may do, which is a network hop and an outage waiting to happen on the hot path.
  enforce_policies = true
  token_dialect    = "access_token_authz"
}

resource "auth0_resource_server_scopes" "telecom_api" {
  resource_server_identifier = auth0_resource_server.telecom_api.identifier

  dynamic "scopes" {
    for_each = local.scopes
    content {
      name        = scopes.key
      description = scopes.value
    }
  }
}
