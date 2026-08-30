output "api_identifier" {
  description = "Set this as TELECOM_MW_JWT_AUDIENCE and the MCP audience"
  value       = auth0_resource_server.telecom_api.identifier
}

output "jwks_url" {
  description = "Set this as TELECOM_MW_JWKS_URL"
  value       = "https://${var.auth0_domain}/.well-known/jwks.json"
}

output "issuer" {
  description = "Set this as TELECOM_MW_JWT_ISSUER"
  value       = "https://${var.auth0_domain}/"
}

output "claim_namespace" {
  description = "Set this as TELECOM_MW_CLAIM_NAMESPACE"
  value       = var.claim_namespace
}

output "mcp_client_id" {
  description = "Client id for the MCP tool server"
  value       = auth0_client.mcp_tools.client_id
}

output "console_client_id" {
  description = "Client id for the agent and supervisor console"
  value       = auth0_client.console.client_id
}

output "role_ids" {
  description = "Role ids, for assigning users during provisioning"
  value       = { for name, role in auth0_role.roles : name => role.id }
}
