# The applications that obtain tokens.

# The MCP tool server. Machine-to-machine, and deliberately powerless on its own: it is
# granted no customer-data scope, because it must present the customer's token for
# anything touching customer data. A compromised service credential reads nothing.
resource "auth0_client" "mcp_tools" {
  name        = "telecom-mcp-tools (${var.environment})"
  description = "MCP tool server. Authenticates itself; carries the customer's token for data."
  app_type    = "non_interactive"

  grant_types = ["client_credentials"]

  jwt_configuration {
    alg                 = "RS256"
    lifetime_in_seconds = var.token_lifetime_seconds
  }
}

resource "auth0_client_grant" "mcp_tools" {
  client_id = auth0_client.mcp_tools.id
  audience  = auth0_resource_server.telecom_api.identifier

  # Empty on purpose. See the comment above; this is not an oversight, and a future
  # change that adds a scope here should have to explain itself in review.
  scopes = []

  depends_on = [auth0_resource_server_scopes.telecom_api]
}

# The agent and supervisor console. A public single-page application: no secret can be
# kept in a browser, so it uses authorization code with PKCE and refresh rotation.
resource "auth0_client" "console" {
  name        = "Telecom support console (${var.environment})"
  description = "Where agents and supervisors work"
  app_type    = "spa"

  grant_types = ["authorization_code", "refresh_token"]

  callbacks           = var.console_callback_urls
  allowed_logout_urls = var.console_callback_urls
  web_origins         = var.console_callback_urls

  oidc_conformant = true

  jwt_configuration {
    alg                 = "RS256"
    lifetime_in_seconds = var.token_lifetime_seconds
  }

  refresh_token {
    rotation_type   = "rotating"
    expiration_type = "expiring"
    token_lifetime  = 86400
    idle_token_lifetime = 3600
    leeway          = 30
  }
}
