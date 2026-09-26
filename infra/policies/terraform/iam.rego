package main

# Workloads use IRSA and CI uses OIDC: no long-lived IAM users or keys.
deny contains msg if {
	some rc in managed_changes
	rc.type in {"aws_iam_user", "aws_iam_access_key", "aws_iam_user_policy", "aws_iam_user_policy_attachment"}
	msg := sprintf("%s: IAM users/access keys are forbidden (use IRSA/OIDC)", [rc.address])
}

deny contains msg if {
	some rc in managed_changes
	rc.type == "aws_iam_role_policy_attachment"
	endswith(after(rc).policy_arn, "/AdministratorAccess")
	msg := sprintf("%s: AdministratorAccess must not be attached", [rc.address])
}
