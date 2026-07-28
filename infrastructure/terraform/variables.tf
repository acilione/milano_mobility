variable "aws_region" {
  description = "AWS region for the reference deployment."
  type        = string
  default     = "eu-south-1"
}

variable "environment" {
  description = "Deployment environment name."
  type        = string
  default     = "demo"
}

variable "vpc_id" {
  description = "Existing VPC in which to create the database."
  type        = string
}

variable "private_subnet_ids" {
  description = "Private subnet IDs used by the RDS subnet group."
  type        = list(string)
}

variable "database_security_group_ids" {
  description = "Security groups that permit PostgreSQL only from platform compute."
  type        = list(string)
}

variable "database_username" {
  description = "RDS administrator username; store the password outside Terraform input files."
  type        = string
  default     = "mobility_admin"
}

variable "database_password" {
  description = "RDS administrator password supplied by CI or a secret manager."
  type        = string
  sensitive   = true
}
