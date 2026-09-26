package main

required_tags := {"project", "environment", "owner", "cost_center", "managed_by"}

# Every taggable resource must carry the required tags (resource or default_tags).
deny contains msg if {
	some rc in managed_changes
	startswith(rc.type, "aws_")
	object.get(after(rc), "tags_all", null) != null
	tags := object.get(after(rc), "tags_all", {})
	missing := required_tags - {k | some k, v in tags; v != ""}
	count(missing) > 0
	msg := sprintf("%s: missing required tags %v", [rc.address, sort(missing)])
}

deny contains msg if {
	some rc in managed_changes
	object.get(after(rc), "tags_all", null) != null
	after(rc).tags_all.managed_by != "terraform"
	msg := sprintf("%s: managed_by tag must be 'terraform'", [rc.address])
}
