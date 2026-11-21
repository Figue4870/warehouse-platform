
data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
  filter {
    name   = "availability-zone"
    values = [var.availability_zone]
  }
  filter {
    name   = "default-for-az"
    values = ["true"]
  }
}

# Ubuntu 24.04 LTS oficial (Canonical)
data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720108477"]

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"]
  }
  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

module "security" {
  source       = "./modules/security"
  project_name = var.project_name
  vpc_id       = data.aws_vpc.default.id
  allowed_cidr = var.allowed_cidr
  admin_cidr   = var.admin_cidr
  warehouses   = var.warehouses
}

module "warehouse" {
  source   = "./modules/warehouse_node"
  for_each = var.warehouses

  name                  = "${var.project_name}-${each.value.wh_id}"
  wh_id                 = each.value.wh_id
  wh_dir                = each.key
  ami_id                = data.aws_ami.ubuntu.id
  instance_type         = coalesce(each.value.instance_type, var.instance_type)
  subnet_id             = data.aws_subnets.default.ids[0]
  security_group_ids    = [module.security.warehouse_sg_id]
  key_name              = var.key_name
  instance_profile_name = var.instance_profile_name
  root_volume_size_gb   = var.root_volume_size_gb
  repo_url              = var.repo_url
  repo_branch           = var.repo_branch
}
