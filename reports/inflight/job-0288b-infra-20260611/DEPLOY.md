# AWS deploy facts (sprint-14-aws job-0288b)

- **Frontend (S3 static site):** http://grace2-hazard-web-226996537797.s3-website-us-west-2.amazonaws.com
- **Agent (EC2):** i-0251879a278df797f @ ec2-35-93-91-8.us-west-2.compute.amazonaws.com:8765 (WS) / :8766 (catalog)
- **Model:** Bedrock us.anthropic.claude-sonnet-4-6
- **IAM role:** grace2-agent-ec2 (bedrock:InvokeModel + SSM + S3 read)
- **SG:** sg-0d15f32310c874a6e (8765-8766 → 0.0.0.0/0)
- **Code bundle:** s3://grace2-agent-bundle-226996537797/grace2-agent-bundle.tgz
- **Service:** systemd grace2-agent (MODEL_PROVIDER=bedrock, file persistence, 0.0.0.0 bind)

## Works
chat, Claude multi-step reasoning, geocode, real data fetch (TIGER/Line verified), map snap, file persistence — all on AWS, no GCP.

## Not yet (needs job-0289 S3 storage swap + job-0290 QGIS-on-ECS)
layer OVERLAY rendering (raster WMS via QGIS Server is still GCP; vector inline conversion reads gs://).

## Teardown (when done)
aws ec2 terminate-instances --region us-west-2 --instance-ids i-0251879a278df797f
