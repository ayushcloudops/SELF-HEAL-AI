region = "us-east-1"
environment = "prod"

# Provide either `zone_id` (preferred) or `zone_name` (lookup by name).
# Replace the placeholder below with your real hosted zone id.
zone_id = "Z0033339NIQVBAWUWYZH"
#zone_name = "example.com"

private_zone = false

records = {
  "@"  = { type = "A", ttl = 300, values = ["1.2.3.4"] }
  "www" = { type = "A", ttl = 300, values = ["1.2.3.4"] }
}
