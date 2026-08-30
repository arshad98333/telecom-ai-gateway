/**
 * Add the claims the telecom middleware depends on: tenant, customer reference, role.
 *
 * Three rules, each of which exists because breaking it is a breach:
 *
 *  - values come from app_metadata, never user_metadata. A user can edit their own
 *    user_metadata through the Management API; app_metadata is written only by the
 *    identity lifecycle. Reading tenant from the wrong one lets a customer choose
 *    whose data they see.
 *  - a login with no tenant is denied rather than defaulted. An account half-way
 *    through provisioning must not get a working token with a guessed tenant.
 *  - a customer must carry a customer reference, or their token cannot be checked
 *    against the account they are asking about.
 */
exports.onExecutePostLogin = async (event, api) => {
  const NAMESPACE = "${claim_namespace}";

  const metadata = event.user.app_metadata || {};
  const tenantId = metadata.tenant_id;
  const role = metadata.role;
  const cxId = metadata.cx_id;

  if (!tenantId || !role) {
    api.access.deny("This account is not provisioned for the telecom API.");
    return;
  }

  if (role === "customer" && !cxId) {
    api.access.deny("This customer account has no customer reference.");
    return;
  }

  api.accessToken.setCustomClaim(NAMESPACE + "tenant_id", tenantId);
  api.accessToken.setCustomClaim(NAMESPACE + "role", role);
  if (cxId) {
    api.accessToken.setCustomClaim(NAMESPACE + "cx_id", cxId);
  }

  // The role assignment itself lives in Auth0's RBAC and arrives in the standard
  // `permissions` claim; this Action does not grant permissions, only identity context.
};
