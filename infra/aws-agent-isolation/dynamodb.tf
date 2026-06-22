# dynamodb.tf -- the session-route table the broker reads/writes.
#
# grace2_session_routes: maps (user_ulid, session_id) -> the Fargate task that
# session is pinned to. PK user_ulid, SK session_id (spike section 4.1) so the
# broker can ConsistentRead the exact (user, session) row, and both of a tab's
# dual sockets (same localStorage session_id) hit the SAME row -> the SAME task,
# preserving the _SESSION_WS_CONNECTIONS / SESSION_HUB / _SESSION_LIVE_TURNS
# convergence the agent depends on.
#
# Reuses the existing autostop DynamoDB conventions: PAY_PER_REQUEST (negligible
# cost at this churn), a single conditional-write pattern, TTL for self-healing.
# Decision 10: the OWNER id is the internal ULID, NOT the Cognito sub -- the
# broker resolves sub -> ULID via the users-table firebase_uid-index GSI BEFORE
# touching this table (mirrors Persistence.get_user_by_firebase_uid + the
# case_list Lambda's _resolve_internal_uid), so this table is keyed on the
# canonical ULID exactly like cases/secrets.

resource "aws_dynamodb_table" "session_routes" {
  name         = var.routes_table_name
  billing_mode = "PAY_PER_REQUEST"

  hash_key  = "user_ulid"
  range_key = "session_id"

  attribute {
    name = "user_ulid"
    type = "S"
  }
  attribute {
    name = "session_id"
    type = "S"
  }

  # TTL self-heal: the per-task idle reaper deletes a route on a clean StopTask;
  # this only garbage-collects an orphaned row (a task that vanished without a
  # clean stop) so the table never accumulates dead routes. The attribute holds
  # an epoch-seconds expiry the broker stamps on write (route_ttl_seconds out).
  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  # PITR is cheap insurance for a routing table; a corrupt route only mis-pins a
  # connect (recoverable on reconnect), so this is belt, not load-bearing.
  point_in_time_recovery {
    enabled = true
  }

  tags = {
    Name = var.routes_table_name
    role = "session-route-registry"
  }
}
