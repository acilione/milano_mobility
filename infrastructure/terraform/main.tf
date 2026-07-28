resource "aws_s3_bucket" "raw" {
  bucket_prefix = "milano-mobility-${var.environment}-raw-"
  force_destroy = false
}

resource "aws_s3_bucket_versioning" "raw" {
  bucket = aws_s3_bucket.raw.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "raw" {
  bucket = aws_s3_bucket.raw.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "raw" {
  bucket                  = aws_s3_bucket.raw.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_db_subnet_group" "warehouse" {
  name       = "milano-mobility-${var.environment}"
  subnet_ids = var.private_subnet_ids
}

resource "aws_db_instance" "warehouse" {
  identifier                 = "milano-mobility-${var.environment}"
  engine                     = "postgres"
  engine_version             = "16.6"
  instance_class             = "db.t4g.micro"
  allocated_storage          = 20
  max_allocated_storage      = 100
  storage_encrypted          = true
  db_name                    = "mobility"
  username                   = var.database_username
  password                   = var.database_password
  db_subnet_group_name       = aws_db_subnet_group.warehouse.name
  vpc_security_group_ids     = var.database_security_group_ids
  publicly_accessible        = false
  backup_retention_period    = 7
  deletion_protection        = true
  skip_final_snapshot        = false
  final_snapshot_identifier  = "milano-mobility-${var.environment}-final"
  auto_minor_version_upgrade = true
}
