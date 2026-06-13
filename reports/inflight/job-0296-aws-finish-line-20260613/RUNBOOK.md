# job-0296 — AWS finish-line CUTOVER RUNBOOK

All code is **landed, verified, and deployed dormant** (commit `7a5df52`). The live
demo is byte-identical until the env flips in Part D. The commands below are
**permission-gated** — run them yourself (paste after `! ` in the session, or in a
shell). Read-only `describe`/`get` steps are safe; mutating steps are marked ⚠️.

Account `226996537797`, region `us-west-2`. Run **Part B (CloudFront) before Part C
(Cognito)** — Cognito callback URLs must be HTTPS, which only the CloudFront domain
provides. Part A (DynamoDB) is independent. Tell me the captured IDs and I do Part D.

---

## Part A — DynamoDB (7 tables + IAM policy)

Index names are **load-bearing** (hard-coded in `dynamo_backend._TABLE_GSIS`).

```bash
R=us-west-2
# ⚠️ A.1 cases
aws dynamodb create-table --region $R --table-name grace2_cases \
  --attribute-definitions AttributeName=_id,AttributeType=S AttributeName=user_id,AttributeType=S AttributeName=owner_user_id,AttributeType=S \
  --key-schema AttributeName=_id,KeyType=HASH \
  --global-secondary-indexes 'IndexName=user_id-index,KeySchema=[{AttributeName=user_id,KeyType=HASH}],Projection={ProjectionType=ALL}' 'IndexName=owner_user_id-index,KeySchema=[{AttributeName=owner_user_id,KeyType=HASH}],Projection={ProjectionType=ALL}' \
  --billing-mode PAY_PER_REQUEST
# ⚠️ A.2 chat (composite key)
aws dynamodb create-table --region $R --table-name grace2_chat \
  --attribute-definitions AttributeName=case_id,AttributeType=S AttributeName=message_id,AttributeType=S \
  --key-schema AttributeName=case_id,KeyType=HASH AttributeName=message_id,KeyType=RANGE --billing-mode PAY_PER_REQUEST
# ⚠️ A.3 sessions (holds charts/project_ids lists)
aws dynamodb create-table --region $R --table-name grace2_sessions \
  --attribute-definitions AttributeName=_id,AttributeType=S --key-schema AttributeName=_id,KeyType=HASH --billing-mode PAY_PER_REQUEST
# ⚠️ A.4 users (+firebase_uid GSI — stores the Cognito sub)
aws dynamodb create-table --region $R --table-name grace2_users \
  --attribute-definitions AttributeName=_id,AttributeType=S AttributeName=firebase_uid,AttributeType=S \
  --key-schema AttributeName=_id,KeyType=HASH \
  --global-secondary-indexes 'IndexName=firebase_uid-index,KeySchema=[{AttributeName=firebase_uid,KeyType=HASH}],Projection={ProjectionType=ALL}' --billing-mode PAY_PER_REQUEST
# ⚠️ A.5 secrets (+user_id GSI)
aws dynamodb create-table --region $R --table-name grace2_secrets \
  --attribute-definitions AttributeName=_id,AttributeType=S AttributeName=user_id,AttributeType=S \
  --key-schema AttributeName=_id,KeyType=HASH \
  --global-secondary-indexes 'IndexName=user_id-index,KeySchema=[{AttributeName=user_id,KeyType=HASH}],Projection={ProjectionType=ALL}' --billing-mode PAY_PER_REQUEST
# ⚠️ A.6 audit  +  ⚠️ A.7 telemetry
aws dynamodb create-table --region $R --table-name grace2_audit --attribute-definitions AttributeName=_id,AttributeType=S --key-schema AttributeName=_id,KeyType=HASH --billing-mode PAY_PER_REQUEST
aws dynamodb create-table --region $R --table-name grace2_telemetry --attribute-definitions AttributeName=_id,AttributeType=S --key-schema AttributeName=_id,KeyType=HASH --billing-mode PAY_PER_REQUEST

# ⚠️ A.8 IAM policy for the EC2 role (grace2-agent-ec2). Write the JSON then attach:
cat > /tmp/grace2-dynamo-policy.json <<'JSON'
{"Version":"2012-10-17","Statement":[{"Sid":"Grace2DynamoCRUD","Effect":"Allow","Action":["dynamodb:GetItem","dynamodb:PutItem","dynamodb:UpdateItem","dynamodb:Query","dynamodb:Scan","dynamodb:BatchGetItem","dynamodb:BatchWriteItem","dynamodb:DescribeTable"],"Resource":["arn:aws:dynamodb:us-west-2:226996537797:table/grace2_*","arn:aws:dynamodb:us-west-2:226996537797:table/grace2_*/index/*"]}]}
JSON
aws iam put-role-policy --role-name grace2-agent-ec2 --policy-name grace2-dynamodb-persistence --policy-document file:///tmp/grace2-dynamo-policy.json

# A.9 VERIFY (read-only): expect ACTIVE x7
for t in grace2_cases grace2_chat grace2_sessions grace2_users grace2_secrets grace2_audit grace2_telemetry; do aws dynamodb describe-table --region $R --table-name $t --query 'Table.TableStatus' --output text; done
```

## Part B — CloudFront (HTTPS/WSS edge) — DO THIS BEFORE COGNITO

```bash
R=us-west-2
# B.1 (read-only) confirm EC2 public DNS (changes on stop/start; sub into B.3 if different)
aws ec2 describe-instances --region $R --instance-ids i-0251879a278df797f --query 'Reservations[].Instances[].PublicDnsName' --output text
# ⚠️ B.2 tile cache policy (forward ALL query strings) -> capture CachePolicy.Id as <TILES_CP>
aws cloudfront create-cache-policy --cache-policy-config '{"Name":"grace2-tiles-qs","Comment":"TiTiler tiles keyed on all query strings","DefaultTTL":86400,"MaxTTL":31536000,"MinTTL":0,"ParametersInCacheKeyAndForwardedToOrigin":{"EnableAcceptEncodingGzip":true,"EnableAcceptEncodingBrotli":true,"HeadersConfig":{"HeaderBehavior":"none"},"CookiesConfig":{"CookieBehavior":"none"},"QueryStringsConfig":{"QueryStringBehavior":"all"}}}'
# ⚠️ B.3 distribution — see the FULL --distribution-config JSON in
#   reports/inflight/job-0296-aws-finish-line-20260613/cloudfront-distribution-config.json
#   (origins: S3 web default / EC2 8765 /ws* WSS / EC2 8080 /cog/* /tiles* / EC2 8766 /api/*).
#   Substitute <TILES_CP> for the /cog/* + /tiles* CachePolicyId first.
aws cloudfront create-distribution --distribution-config file:///home/nate/Documents/GRACE-2/reports/inflight/job-0296-aws-finish-line-20260613/cloudfront-distribution-config.json
#   -> capture Distribution.Id (<DIST_ID>) and Distribution.DomainName (<CF_DOMAIN> e.g. dxxxx.cloudfront.net)
# B.4 (read-only) poll until Deployed (5-15 min)
aws cloudfront get-distribution --id <DIST_ID> --query 'Distribution.Status' --output text
# B.5 (read-only) edge smoke once Deployed
CF=<CF_DOMAIN>; curl -sI https://$CF/ | head -3; curl -s -o /dev/null -w 'tile %{http_code}\n' "https://$CF/cog/tiles/WebMercatorQuad/8/40/98.png?url=s3%3A%2F%2Fdummy"; curl -s -o /dev/null -w 'ws %{http_code}\n' -H 'Connection: Upgrade' -H 'Upgrade: websocket' -H 'Sec-WebSocket-Version: 13' -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' "https://$CF/ws"
```

## Part C — Cognito (email/password) — AFTER CloudFront (needs HTTPS callback)

`<SITE_URL>` = `https://<CF_DOMAIN>` (the CloudFront domain from B.3).

```bash
R=us-west-2
# ⚠️ C.1 user pool -> capture UserPool.Id <POOL_ID>
aws cognito-idp create-user-pool --region $R --pool-name grace2-users --username-attributes email --auto-verified-attributes email \
  --policies '{"PasswordPolicy":{"MinimumLength":8,"RequireUppercase":true,"RequireLowercase":true,"RequireNumbers":true,"RequireSymbols":false}}' \
  --account-recovery-setting '{"RecoveryMechanisms":[{"Priority":1,"Name":"verified_email"}]}' \
  --schema '[{"Name":"email","AttributeDataType":"String","Required":true,"Mutable":true}]' --mfa-configuration OFF
# ⚠️ C.2 app client (public, no secret) -> capture UserPoolClient.ClientId <CLIENT_ID>. Replace <POOL_ID>/<SITE_URL>.
aws cognito-idp create-user-pool-client --region $R --user-pool-id <POOL_ID> --client-name grace2-web --no-generate-secret \
  --supported-identity-providers COGNITO --allowed-o-auth-flows code --allowed-o-auth-scopes openid email profile \
  --allowed-o-auth-flows-user-pool-client --callback-urls '<SITE_URL>/' 'http://localhost:5173/' --logout-urls '<SITE_URL>/' 'http://localhost:5173/' \
  --explicit-auth-flows ALLOW_REFRESH_TOKEN_AUTH ALLOW_USER_SRP_AUTH --prevent-user-existence-errors ENABLED
# ⚠️ C.3 hosted-UI domain -> VITE_COGNITO_DOMAIN = grace2-auth.auth.us-west-2.amazoncognito.com
aws cognito-idp create-user-pool-domain --region $R --domain grace2-auth --user-pool-id <POOL_ID>
```

## Part D — cutover env-flips (I do these after you give me the IDs)

I apply via SSM (agent) + rebuild/sync (web). Order chosen so each is independently reversible.
1. **DynamoDB:** set agent `GRACE2_PERSISTENCE_BACKEND=dynamodb` (+ migrate the existing file cases into the tables), restart. *(Verify cases still rehydrate; revert env to roll back.)*
2. **CloudFront:** rebuild web with `VITE_GRACE2_PUBLIC_BASE=<CF_DOMAIN>`, set agent `GRACE2_TILE_SERVER_BASE=https://<CF_DOMAIN>` so new layers emit HTTPS tiles; site now fully HTTPS.
3. **Cognito:** rebuild web with `VITE_COGNITO_USER_POOL_ID/CLIENT_ID/DOMAIN/REDIRECT_URI=https://<CF_DOMAIN>/`, set agent `GRACE2_COGNITO_USER_POOL_ID/CLIENT_ID`. Then **flip `AUTH_REQUIRED=true`** last, after a manual sign-up→sign-in smoke. *(This is the only step that requires real accounts.)*

Cross-track prerequisite to confirm at D.1: `main._maybe_bind_dev_persistence` / `server.init_persistence_from_env` must call `persistence.make_persistence_for_backend()` (not hardcode `make_file_persistence`) or the DynamoDB env is a no-op. (I'll verify/patch before flipping.)

## Part E — GCP teardown (independent, anytime)
Console <https://console.cloud.google.com/iam-admin/settings> → select `grace-2-hazard-prod` → SHUT DOWN. Or Cloud Shell: `gcloud projects delete grace-2-hazard-prod`. (gcloud not installed locally.)
