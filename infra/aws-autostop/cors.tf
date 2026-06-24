# Runs-bucket CORS for the cold-view path (the box-off "no layers" root cause).
#
# WHY THIS EXISTS (load-bearing, learned the hard way 2026-06-22): the web app
# opens a Case with the agent box ASLEEP by fetching the persisted Case-view
# snapshot directly from S3 via a pre-signed URL (web/src/lib/case_view.ts,
# fetchCaseView Hop 2 -> a cross-origin browser GET of
# https://<runs_bucket>.s3.<region>.amazonaws.com/case-views/{case_id}.json).
# A browser cross-origin fetch only exposes the response to JS when the bucket
# returns an Access-Control-Allow-Origin header that matches the app origin. The
# bucket had NO CORS configuration, so EVERY cold open silently failed: S3
# returned 200 + the bytes, the browser blocked them, fetchCaseView hit its
# catch and returned null, and the map showed "No layers loaded yet" for every
# Case while the box was asleep. Unit/vitest coverage could not catch it (the
# test fetch is a fake that bypasses CORS), which is why it survived multiple
# "fixes". Do NOT remove this without a replacement (e.g. serving the snapshot
# through the same CloudFront origin instead of S3-direct).
#
# SCOPE: GET/HEAD only, restricted to the app origin(s). The pre-signed URL's
# SigV4 signature is the real access control (time-limited; minted only by the
# view_sign Lambda) -- CORS only decides which web origin's JS may READ the
# response, so this is least-privilege, not the security boundary. Add a new
# origin here whenever a new web host is introduced (custom domain, staging
# CloudFront, preview deploy) or cold-view breaks there.

variable "web_cors_origins" {
  type = list(string)
  description = "Web origins allowed to read runs-bucket objects (Case-view snapshots, export zips) from a browser cross-origin fetch."
  default = [
    "https://grace-2.vercel.app",      # LIVE frontend (Vercel) since 2026-06-23
    "https://*.vercel.app",            # Vercel preview deploys (one wildcard, S3-supported)
    "https://d125yfbyjrpbre.cloudfront.net", # legacy S3+CloudFront SPA (retained, not live)
    "http://localhost:5173",
    "http://localhost:4173",
  ]
}

resource "aws_s3_bucket_cors_configuration" "runs" {
  bucket = var.runs_bucket

  cors_rule {
    allowed_methods = ["GET", "HEAD"]
    allowed_origins = var.web_cors_origins
    allowed_headers = ["*"]
    expose_headers  = ["Content-Length", "Content-Type", "ETag"]
    max_age_seconds = 3000
  }
}
