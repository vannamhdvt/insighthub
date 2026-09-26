package main

data_ports := {5432, 6379}

open_cidrs := {"0.0.0.0/0", "::/0"}

# Data stores are never reachable from the internet.
deny contains msg if {
	some rc in managed_changes
	rc.type == "aws_vpc_security_group_ingress_rule"
	some port in data_ports
	after(rc).from_port <= port
	port <= after(rc).to_port
	object.get(after(rc), "cidr_ipv4", "") in open_cidrs
	msg := sprintf("%s: port %d open to the internet", [rc.address, port])
}

deny contains msg if {
	some rc in managed_changes
	rc.type == "aws_security_group"
	some rule in object.get(after(rc), "ingress", [])
	some port in data_ports
	rule.from_port <= port
	port <= rule.to_port
	some cidr in rule.cidr_blocks
	cidr in open_cidrs
	msg := sprintf("%s: inline ingress opens port %d to the internet", [rc.address, port])
}

# Org rule: prod EKS API must not be reachable from anywhere.
deny contains msg if {
	is_prod
	some rc in managed_changes
	rc.type == "aws_eks_cluster"
	some vpc in after(rc).vpc_config
	vpc.endpoint_public_access
	some cidr in vpc.public_access_cidrs
	cidr in open_cidrs
	msg := sprintf("%s: prod EKS public endpoint must not allow %s", [rc.address, cidr])
}

# Non-prod: allowed but visible in the policy report.
warn contains msg if {
	not is_prod
	some rc in managed_changes
	rc.type == "aws_eks_cluster"
	some vpc in after(rc).vpc_config
	some cidr in vpc.public_access_cidrs
	cidr in open_cidrs
	msg := sprintf("%s: public EKS endpoint open to %s (allowed for %s lab only)", [rc.address, cidr, environment])
}
