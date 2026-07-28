output "raw_bucket_name" {
  description = "Immutable raw GTFS bucket."
  value       = aws_s3_bucket.raw.id
}

output "warehouse_endpoint" {
  description = "Private PostgreSQL endpoint."
  value       = aws_db_instance.warehouse.address
}
