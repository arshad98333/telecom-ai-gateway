# The post-login Action that puts tenant, customer reference and role into the token.
#
# Claims come from app_metadata, which is written by the identity lifecycle (joiner,
# mover, leaver) and is never editable by the user. Reading them from user_metadata
# instead would let a customer set their own tenant, which is the whole ballgame.

resource "auth0_action" "add_telecom_claims" {
  name    = "Add telecom claims (${var.environment})"
  runtime = "node18"
  deploy  = true

  supported_triggers {
    id      = "post-login"
    version = "v3"
  }

  dependencies {
    name    = "lodash"
    version = "4.17.21"
  }

  code = templatefile("${path.module}/actions/add_telecom_claims.js", {
    claim_namespace = var.claim_namespace
  })
}

resource "auth0_trigger_actions" "post_login" {
  trigger = "post-login"

  actions {
    id           = auth0_action.add_telecom_claims.id
    display_name = auth0_action.add_telecom_claims.name
  }
}
