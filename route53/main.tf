provider "aws" {
  region = var.region
}

# If caller supplied `zone_id` we use it; otherwise look up the hosted zone by name.
data "aws_route53_zone" "by_name" {
  count        = var.zone_id == "" && var.zone_name != "" ? 1 : 0
  name         = var.zone_name
  private_zone = var.private_zone
}

locals {
  resolved_zone_id = var.zone_id != "" ? var.zone_id : data.aws_route53_zone.by_name[0].zone_id
  resolved_zone_name = var.zone_name != "" ? var.zone_name : ""
}

resource "aws_route53_record" "records" {
  for_each = var.records

  zone_id = local.resolved_zone_id
  name    = each.key == "@" ? local.resolved_zone_name : "${each.key}.${local.resolved_zone_name}"
  type    = each.value.type
  ttl     = each.value.ttl
  records = each.value.values
}
