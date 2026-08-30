# Roles bundle scopes. The service caps each role's permissions again in code, so a
# mistake here cannot widen access beyond what the role is allowed to hold - but this
# is still the place a person looks to answer "what can a supervisor do".

locals {
  customer_reads = [
    "account:read", "service:read", "order:read", "billing:read", "network:read", "ticket:read",
  ]

  role_scopes = {
    customer = concat(local.customer_reads, [
      "ticket:write", "callback:write", "refund:request", "case:read",
    ])

    support_agent = concat(local.customer_reads, [
      "ticket:write", "callback:write", "refund:request", "case:read", "case:write",
    ])

    supervisor_approver = concat(local.customer_reads, [
      "ticket:write", "callback:write", "refund:request", "refund:approve",
      "case:read", "case:write", "assignment:read", "assignment:write",
    ])

    # No customer-data scopes at all. Administering security is not reading bills.
    admin_security = ["audit:read", "config:read", "config:write", "assignment:read"]
  }

  role_descriptions = {
    customer            = "A telecom customer acting on their own account"
    support_agent       = "A support agent acting on accounts assigned to them"
    supervisor_approver = "A supervisor who decides restricted actions"
    admin_security      = "Security administration: audit and configuration, no customer data"
  }
}

resource "auth0_role" "roles" {
  for_each = local.role_scopes

  name        = "${each.key}-${var.environment}"
  description = local.role_descriptions[each.key]
}

resource "auth0_role_permissions" "roles" {
  for_each = local.role_scopes

  role_id = auth0_role.roles[each.key].id

  dynamic "permissions" {
    for_each = each.value
    content {
      resource_server_identifier = auth0_resource_server.telecom_api.identifier
      name                       = permissions.value
    }
  }

  depends_on = [auth0_resource_server_scopes.telecom_api]
}
