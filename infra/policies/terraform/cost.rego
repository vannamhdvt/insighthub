package main

# Cost guardrails for lab environments (dev/staging).
allowed_db_classes := {"db.t4g.micro", "db.t4g.small", "db.t4g.medium"}

allowed_cache_types := {"cache.t4g.micro", "cache.t4g.small"}

allowed_node_types := {"t3.medium", "t3a.medium", "t3.large", "t3a.large"}

max_nodes := 4

deny contains msg if {
	not is_prod
	some rc in managed_changes
	rc.type == "aws_db_instance"
	not after(rc).instance_class in allowed_db_classes
	msg := sprintf("%s: %s not allowed in %s (allowed %v)", [rc.address, after(rc).instance_class, environment, sort(allowed_db_classes)])
}

deny contains msg if {
	not is_prod
	some rc in managed_changes
	rc.type == "aws_elasticache_replication_group"
	not after(rc).node_type in allowed_cache_types
	msg := sprintf("%s: %s not allowed in %s", [rc.address, after(rc).node_type, environment])
}

deny contains msg if {
	not is_prod
	some rc in managed_changes
	rc.type == "aws_eks_node_group"
	some t in after(rc).instance_types
	not t in allowed_node_types
	msg := sprintf("%s: instance type %s not allowed in %s", [rc.address, t, environment])
}

deny contains msg if {
	not is_prod
	some rc in managed_changes
	rc.type == "aws_eks_node_group"
	some sc in after(rc).scaling_config
	sc.max_size > max_nodes
	msg := sprintf("%s: max_size %d exceeds lab cap %d", [rc.address, sc.max_size, max_nodes])
}

# Prod durability requirements.
deny contains msg if {
	is_prod
	some rc in managed_changes
	rc.type == "aws_db_instance"
	some field in ["multi_az", "deletion_protection"]
	not after(rc)[field]
	msg := sprintf("%s: prod RDS requires %s", [rc.address, field])
}

deny contains msg if {
	is_prod
	some rc in managed_changes
	rc.type == "aws_db_instance"
	after(rc).backup_retention_period < 7
	msg := sprintf("%s: prod RDS backup retention must be >= 7 days", [rc.address])
}
