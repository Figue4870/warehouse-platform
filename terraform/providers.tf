# Credenciales: las del AWS Academy Learner Lab ("AWS Details" -> "AWS CLI"),
provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project   = var.project_name
      ManagedBy = "terraform"
    }
  }
}
