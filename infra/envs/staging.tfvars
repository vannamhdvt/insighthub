aws_region  = "ap-southeast-1"
environment = "staging"
owner       = "namtv"
cost_center = "do2603"
name_prefix = "do2603-namtv"
lab_id      = "do2603-namtv-day3-staging"
expires_at  = "2026-09-28T17:00:00Z"

eks_public_access_cidrs = ["0.0.0.0/0"]

node_instance_types = ["t3.medium"]
node_capacity_type  = "ON_DEMAND"
node_desired_size   = 2

db_instance_class        = "db.t4g.small"
db_multi_az              = false
db_backup_retention_days = 7
db_deletion_protection   = false
redis_num_cache_clusters = 2
