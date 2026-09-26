package main

# Helpers over `terraform show -json tfplan` (resource_changes[].change.after).

managed_changes contains rc if {
	some rc in input.resource_changes
	rc.mode == "managed"
	not deleting(rc)
}

deleting(rc) if {
	rc.change.actions == ["delete"]
}

after(rc) := rc.change.after

# Environment comes from the provider default_tags that every resource inherits.
environment := env if {
	some rc in managed_changes
	env := after(rc).tags_all.environment
} else := "unknown"

is_prod if environment == "prod"

# A field counts as set when it is known and non-empty, or will be known after
# apply (e.g. a KMS ARN created in the same plan).
has_value(rc, field) if {
	v := object.get(after(rc), field, null)
	not v in {null, ""}
}

has_value(rc, field) if {
	object.get(object.get(rc.change, "after_unknown", {}), field, false) == true
}
