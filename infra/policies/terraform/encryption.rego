package main

deny contains msg if {
	some rc in managed_changes
	rc.type == "aws_db_instance"
	not after(rc).storage_encrypted
	msg := sprintf("%s: RDS storage must be encrypted", [rc.address])
}

deny contains msg if {
	some rc in managed_changes
	rc.type == "aws_db_instance"
	after(rc).publicly_accessible
	msg := sprintf("%s: RDS must not be publicly accessible", [rc.address])
}

deny contains msg if {
	some rc in managed_changes
	rc.type == "aws_elasticache_replication_group"
	some field in ["at_rest_encryption_enabled", "transit_encryption_enabled"]
	not after(rc)[field]
	msg := sprintf("%s: ElastiCache %s must be true", [rc.address, field])
}

deny contains msg if {
	some rc in managed_changes
	rc.type == "aws_eks_cluster"
	count(object.get(after(rc), "encryption_config", [])) == 0
	msg := sprintf("%s: EKS secrets must use envelope encryption (encryption_config)", [rc.address])
}

deny contains msg if {
	some rc in managed_changes
	rc.type == "aws_secretsmanager_secret"
	not has_value(rc, "kms_key_id")
	msg := sprintf("%s: secret must use a customer-managed KMS key", [rc.address])
}

deny contains msg if {
	some rc in managed_changes
	rc.type == "aws_launch_template"
	some bdm in object.get(after(rc), "block_device_mappings", [])
	some ebs in bdm.ebs
	ebs.encrypted != "true"
	msg := sprintf("%s: node EBS volumes must be encrypted", [rc.address])
}

deny contains msg if {
	some rc in managed_changes
	rc.type == "aws_launch_template"
	some md in object.get(after(rc), "metadata_options", [])
	md.http_tokens != "required"
	msg := sprintf("%s: IMDSv2 (http_tokens=required) is mandatory", [rc.address])
}
