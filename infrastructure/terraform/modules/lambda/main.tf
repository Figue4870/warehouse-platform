# Fase 9: Lambda que procesa los objetos nuevos del bucket (contratos y reporte diario).
# El rol es LabRole: el Learner Lab no permite crear roles de IAM.
# La notificación del bucket (ObjectCreated -> Lambda) la configura la central al arrancar, igual que el bucket.

data "archive_file" "code" {
  type        = "zip"
  source_dir  = var.source_dir
  output_path = "${path.root}/.build/${var.function_name}.zip"
  excludes    = ["Dockerfile", "local_runner.py", "storage_processor/__pycache__"]
}

resource "aws_cloudwatch_log_group" "this" {
  name              = "/aws/lambda/${var.function_name}"
  retention_in_days = 7
}

resource "aws_lambda_function" "this" {
  function_name    = var.function_name
  role             = var.role_arn
  runtime          = "python3.12"
  handler          = "storage_processor.handler.lambda_handler"
  filename         = data.archive_file.code.output_path
  source_code_hash = data.archive_file.code.output_base64sha256
  timeout          = 30
  memory_size      = 256
  depends_on       = [aws_cloudwatch_log_group.this]

  environment {
    variables = {
      CENTRAL_URL           = var.central_url
      CENTRAL_SHARED_SECRET = var.shared_secret
      CONTRACT_MAX_BYTES    = tostring(var.contract_max_bytes)
    }
  }
}

# Permite que S3 (solo este bucket y esta cuenta) invoque la función
resource "aws_lambda_permission" "s3" {
  statement_id   = "AllowS3Invoke"
  action         = "lambda:InvokeFunction"
  function_name  = aws_lambda_function.this.function_name
  principal      = "s3.amazonaws.com"
  source_arn     = "arn:aws:s3:::${var.bucket}"
  source_account = var.account_id
}

# Dos reintentos de AWS si la función falla; lo que no se pudo procesar queda registrado en CloudWatch
resource "aws_lambda_function_event_invoke_config" "this" {
  function_name                = aws_lambda_function.this.function_name
  maximum_retry_attempts       = 2
  maximum_event_age_in_seconds = 3600
}
