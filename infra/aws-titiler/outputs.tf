# outputs.tf — values NATE reads after `tofu apply`, used for the CloudFront
# cutover (DEPLOY_NOTE.md) and the pre-cutover verification.

output "titiler_instance_id" {
  description = "EC2 instance id of the isolated TiTiler box."
  value       = aws_instance.titiler.id
}

output "titiler_public_ip" {
  description = "Elastic IP of the TiTiler box (stable across stop/start/replace)."
  value       = aws_eip.tiles.public_ip
}

output "titiler_public_dns" {
  description = <<-EOT
    Public DNS of the TiTiler box's EIP. THIS is what CloudFront
    origin-titiler.DomainName is repointed to in the cutover (keep HTTPPort=8080,
    http-only, OriginReadTimeout>=30). See DEPLOY_NOTE.md /
    cloudfront-tiles-origin.tf.docs.
  EOT
  value       = local.eip_public_dns
}

output "titiler_origin_url" {
  description = "Direct origin URL for the pre-cutover smoke test (curl <this>/cog/info?url=s3://...known.tif => 200)."
  value       = "http://${local.eip_public_dns}:${var.titiler_port}"
}

output "titiler_instance_role_name" {
  description = "Instance role name (AmazonS3ReadOnlyAccess + AmazonSSMManagedInstanceCore) — the read-only COG access identity."
  value       = aws_iam_role.titiler.name
}

output "titiler_security_group_id" {
  description = "Security group id (:8080 ingress from CloudFront-only when var.cloudfront_prefix_list_id is set, else var.ingress_cidr)."
  value       = aws_security_group.titiler.id
}

output "ingress_source" {
  description = "Resolved :8080 ingress source — the CloudFront managed prefix list id, or the fallback CIDR."
  value       = var.cloudfront_prefix_list_id != "" ? var.cloudfront_prefix_list_id : var.ingress_cidr
}

output "cloudfront_cutover_hint" {
  description = "One-line reminder of the CloudFront origin repoint this box enables (authored in cloudfront-tiles-origin.tf.docs / DEPLOY_NOTE.md — NOT applied here)."
  value       = "Repoint CloudFront E2L74AS56MVZ87 origin 'origin-titiler' DomainName -> ${local.eip_public_dns} (moves both /tiles* and /cog/*)."
}
