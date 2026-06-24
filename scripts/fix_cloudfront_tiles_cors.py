#!/usr/bin/env python3
"""Attach a CORS response-headers-policy (ACAO:*) to the CloudFront tile/catalog
behaviors so the cross-origin Vercel SPA (grace-2.vercel.app + preview deploys)
can READ raster tiles + the catalog.

WHY: the frontend moved from S3+CloudFront (same-origin with the tiles) to Vercel
(cross-origin). The /cog/*, /tiles*, /api/* CloudFront behaviors have NO CORS
response header and the tile cache policy does NOT forward the Origin header, so
the browser blocks every cross-origin tile fetch -> rasters never paint (the
layer LIST shows, the map stays blank). A CloudFront Response-Headers-Policy
stamps Access-Control-Allow-Origin at the edge on every response, regardless of
caching or Origin-forwarding -- the robust, cache-safe fix. ACAO:* is correct for
a PUBLIC read-only tile/catalog CDN (public hazard COGs, no auth/cookies).

This is idempotent (create-or-reuse the policy; re-attach is a no-op). It mutates
the LIVE production CloudFront distribution E2L74AS56MVZ87 -- run it knowingly.

Run:  services/agent/.venv/bin/python scripts/fix_cloudfront_tiles_cors.py
Verify after ~3-6 min propagation:
  curl -s -I -H 'Origin: https://grace-2.vercel.app' \
    'https://d125yfbyjrpbre.cloudfront.net/cog/info?url=<an s3 COG>' | grep -i access-control-allow-origin
"""
import boto3

DIST = "E2L74AS56MVZ87"
POLICY_NAME = "grace2-tiles-cors"
TARGET_PATHS = {"/cog/*", "/tiles*", "/tiles/*", "/api/*", "/api*", "/cog*"}


def main() -> int:
    cf = boto3.client("cloudfront", region_name="us-east-1")  # CloudFront is global
    # 1) create-or-reuse the CORS response-headers-policy
    try:
        rhp = cf.create_response_headers_policy(
            ResponseHeadersPolicyConfig={
                "Name": POLICY_NAME,
                "Comment": "ACAO:* for /cog /tiles /api (Vercel SPA cross-origin)",
                "CorsConfig": {
                    "AccessControlAllowOrigins": {"Quantity": 1, "Items": ["*"]},
                    "AccessControlAllowHeaders": {"Quantity": 1, "Items": ["*"]},
                    "AccessControlAllowMethods": {
                        "Quantity": 4,
                        "Items": ["GET", "HEAD", "OPTIONS", "POST"],
                    },
                    "AccessControlAllowCredentials": False,
                    "OriginOverride": True,
                },
            }
        )
        pid = rhp["ResponseHeadersPolicy"]["Id"]
        print(f"created response-headers-policy {POLICY_NAME}: {pid}")
    except cf.exceptions.ResponseHeadersPolicyAlreadyExists:
        items = cf.list_response_headers_policies(Type="custom")[
            "ResponseHeadersPolicyList"
        ]["Items"]
        pid = next(
            p["ResponseHeadersPolicy"]["Id"]
            for p in items
            if p["ResponseHeadersPolicy"]["ResponseHeadersPolicyConfig"]["Name"]
            == POLICY_NAME
        )
        print(f"reusing existing {POLICY_NAME}: {pid}")
    # 2) attach to the cross-origin backend behaviors
    cfg = cf.get_distribution_config(Id=DIST)
    etag = cfg["ETag"]
    dc = cfg["DistributionConfig"]
    changed = []
    for b in dc.get("CacheBehaviors", {}).get("Items", []):
        if b["PathPattern"] in TARGET_PATHS:
            if b.get("ResponseHeadersPolicyId") != pid:
                b["ResponseHeadersPolicyId"] = pid
                changed.append(b["PathPattern"])
    if not changed:
        print("all target behaviors already carry the policy; nothing to do")
        return 0
    cf.update_distribution(Id=DIST, IfMatch=etag, DistributionConfig=dc)
    print(f"attached {pid} to: {changed}")
    print("CloudFront propagating (~3-6 min); then curl-verify the ACAO header")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
