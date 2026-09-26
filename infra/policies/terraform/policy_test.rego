package main

import data.main

base := {"resource_changes": [{
	"address": "module.rds.aws_db_instance.this",
	"mode": "managed",
	"type": "aws_db_instance",
	"change": {"actions": ["create"], "after": {
		"instance_class": "db.t4g.micro",
		"storage_encrypted": true,
		"publicly_accessible": false,
		"multi_az": false,
		"deletion_protection": false,
		"backup_retention_period": 1,
		"tags_all": {"project": "insighthub", "environment": "dev", "owner": "namtv", "cost_center": "do2603", "managed_by": "terraform"},
	}},
}]}

test_valid_dev_rds_allowed if {
	count(main.deny) == 0 with input as base
}

test_public_rds_denied if {
	inp := json.patch(base, [{"op": "replace", "path": "/resource_changes/0/change/after/publicly_accessible", "value": true}])
	some msg in main.deny with input as inp
	contains(msg, "publicly accessible")
}

test_missing_tag_denied if {
	inp := json.remove(base, ["/resource_changes/0/change/after/tags_all/owner"])
	some msg in main.deny with input as inp
	contains(msg, "missing required tags")
}

test_prod_requires_multi_az if {
	inp := json.patch(base, [{"op": "replace", "path": "/resource_changes/0/change/after/tags_all/environment", "value": "prod"}])
	some msg in main.deny with input as inp
	contains(msg, "multi_az")
}

test_deleted_resources_ignored if {
	inp := json.patch(base, [
		{"op": "replace", "path": "/resource_changes/0/change/actions", "value": ["delete"]},
		{"op": "replace", "path": "/resource_changes/0/change/after/publicly_accessible", "value": true},
	])
	count(main.deny) == 0 with input as inp
}

test_unknown_kms_counts_as_set if {
	inp := {"resource_changes": [{
		"address": "s",
		"mode": "managed",
		"type": "aws_secretsmanager_secret",
		"change": {"actions": ["create"], "after": {"tags_all": base.resource_changes[0].change.after.tags_all}, "after_unknown": {"kms_key_id": true}},
	}]}
	count(main.deny) == 0 with input as inp
}
