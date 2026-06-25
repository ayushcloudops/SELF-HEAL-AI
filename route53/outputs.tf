output "zone_id" {
  description = "The resolved Route53 hosted zone ID used for record operations"
  value       = var.zone_id != "" ? var.zone_id : data.aws_route53_zone.by_name[0].zone_id
}
