resource "aws_instance" "this" {
  ami                    = var.ami_id
  instance_type          = var.instance_type
  subnet_id              = var.subnet_id
  vpc_security_group_ids = var.security_group_ids
  key_name               = var.key_name
  iam_instance_profile   = var.instance_profile_name

  user_data = templatefile("${path.module}/../../templates/warehouse_user_data.sh.tftpl", {
    wh_id       = var.wh_id
    wh_dir      = var.wh_dir
    repo_url    = var.repo_url
    repo_branch = var.repo_branch
    runtime_env = var.runtime_env
  })
  user_data_replace_on_change = true

  root_block_device {
    volume_size = var.root_volume_size_gb
    volume_type = "gp3"
  }

  metadata_options {
    http_tokens = "required" # IMDSv2 (más seguro)
  }

  tags = {
    Name      = var.wh_id
    Warehouse = var.wh_id
  }
}
