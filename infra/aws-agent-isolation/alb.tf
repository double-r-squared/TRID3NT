# alb.tf -- DESTROYED 2026-07-06 (scale-to-zero Phase 2).
#
# The internet-facing ALB (grace2-agent-broker) fronted the Fargate broker at
# ~$20/mo idle. The broker now runs as a docker/systemd unit on the always-on
# TiTiler box (:8081, see broker_on_box.tf) and CloudFront /ws* points at the
# box origin directly (origin-broker-box), so the ALB + target group + listeners
# + ALB SG are gone. The original ALB-not-API-Gateway rationale (no WS lifetime
# cap vs API GW's 2h/10min-idle) still applies to the box path: CloudFront has
# no WS lifetime cap and the 12s server DATA heartbeat keeps connections
# never-idle. Rollback = git revert this file + broker.tf + apply, then flip
# the CloudFront /ws* TargetOriginId back to origin-broker-ws.
