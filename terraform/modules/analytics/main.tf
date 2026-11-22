variable "sql_dir" {
  type = string
}

data "aws_caller_identity" "current" {}

resource "aws_s3_bucket" "data" {
  bucket = "warehouse-platform-datos-${data.aws_caller_identity.current.account_id}"
}

resource "aws_s3_bucket_public_access_block" "data" {
  bucket                  = aws_s3_bucket.data.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "data" {
  bucket = aws_s3_bucket.data.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "results" {
  bucket = aws_s3_bucket.data.id

  rule {
    id     = "expire-athena-results"
    status = "Enabled"

    filter {
      prefix = "athena-results/"
    }

    expiration {
      days = 7
    }
  }
}

resource "aws_glue_catalog_database" "data" {
  name = "warehouse_platform"
}

locals {
  tables = jsondecode(file("${var.sql_dir}/../tables.json"))
}

resource "aws_glue_catalog_table" "dataset" {
  for_each      = local.tables
  name          = each.key
  database_name = aws_glue_catalog_database.data.name
  table_type    = "EXTERNAL_TABLE"
  parameters = {
    "projection.enabled"          = "true"
    "projection.dt.type"          = "date"
    "projection.dt.range"         = "2026-01-01,NOW"
    "projection.dt.format"        = "yyyy-MM-dd"
    "projection.dt.interval"      = "1"
    "projection.dt.interval.unit" = "DAYS"
    "storage.location.template"   = "s3://${aws_s3_bucket.data.bucket}/${each.key}/dt=$${dt}/"
  }

  partition_keys {
    name = "dt"
    type = "string"
  }

  storage_descriptor {
    location      = "s3://${aws_s3_bucket.data.bucket}/${each.key}/"
    input_format  = "org.apache.hadoop.mapred.TextInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat"

    ser_de_info {
      serialization_library = "org.openx.data.jsonserde.JsonSerDe"
      parameters = {
        "ignore.malformed.json" = "true"
      }
    }

    dynamic "columns" {
      for_each = each.value
      content {
        name = columns.key
        type = columns.value
      }
    }
  }
}

resource "aws_athena_workgroup" "reports" {
  name = "warehouse-platform-reportes"

  configuration {
    bytes_scanned_cutoff_per_query  = 1073741824
    enforce_workgroup_configuration = true

    result_configuration {
      output_location = "s3://${aws_s3_bucket.data.bucket}/athena-results/"

      encryption_configuration {
        encryption_option = "SSE_S3"
      }
    }
  }
}

resource "aws_athena_named_query" "report" {
  for_each  = fileset(var.sql_dir, "*.sql")
  name      = trimsuffix(each.key, ".sql")
  database  = aws_glue_catalog_database.data.name
  workgroup = aws_athena_workgroup.reports.name
  query     = templatefile("${var.sql_dir}/${each.key}", { database = aws_glue_catalog_database.data.name, days = 30 })
}

output "runtime_env" {
  value = {
    EXPORT_S3_BUCKET = aws_s3_bucket.data.bucket
    ATHENA_DATABASE  = aws_glue_catalog_database.data.name
    ATHENA_WORKGROUP = aws_athena_workgroup.reports.name
  }
}
