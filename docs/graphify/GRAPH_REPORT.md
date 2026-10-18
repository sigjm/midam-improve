# Graph Report - corpus  (2026-10-18)

## Corpus Check
- Corpus is ~9,016 words - fits in a single context window. You may not need a graph.

## Summary
- 163 nodes · 280 edges · 10 communities (8 shown, 2 thin omitted)
- Extraction: 91% EXTRACTED · 9% INFERRED · 0% AMBIGUOUS · INFERRED: 25 edges (avg confidence: 0.87)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- Emulator workflow runner
- Emulator test harness
- ECR publish pipelines
- CodeBuild GenAI runner
- ECR repository module
- GitHub OIDC role module
- Prod CI roles and policies
- backend CI (no AWS)
- AI image mirror script
- OIDC provider bootstrap

## God Nodes (most connected - your core abstractions)
1. `test.sh script` - 16 edges
2. `aws_codebuild_project.this` - 13 edges
3. `aws_iam_role.this` - 11 edges
4. `GenAI publish job (3-service matrix)` - 11 edges
5. `backend CD build-and-push job` - 10 edges
6. `build-deploy cd job` - 10 edges
7. `aws_ecr_repository.app` - 9 edges
8. `module.ecr` - 8 edges
9. `promote job (tag -> digest, staging match)` - 8 edges
10. `StepFailed` - 7 edges

## Surprising Connections (you probably didn't know these)
- `amazon_ecr_login()` --implements--> `aws-actions/amazon-ecr-login`  [INFERRED]
  emulator/run_workflow.py → backend/.github/workflows/cd.yml
- `backend CD build-and-push job` --references--> `module.github_oidc_backend`  [INFERRED]
  backend/.github/workflows/cd.yml → infra/terraform/environments/prod/github_oidc.tf
- `GenAI publish job (3-service matrix)` --references--> `module.github_oidc_genai`  [INFERRED]
  GenAI/.github/workflows/genai-ci.yml → infra/terraform/environments/prod/github_oidc.tf
- `configure_aws_credentials()` --implements--> `aws-actions/configure-aws-credentials`  [INFERRED]
  emulator/run_workflow.py → backend/.github/workflows/cd.yml
- `build_push()` --implements--> `docker/build-push-action`  [INFERRED]
  emulator/run_workflow.py → backend/.github/workflows/cd.yml

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **OIDC -> ECR login -> build-push flow** — backend__github_workflows_cd_configure_aws_credentials, backend__github_workflows_cd_amazon_ecr_login, backend__github_workflows_cd_build_push_action, backend__github_workflows_cd_build_and_push_job, genai__github_workflows_genai_ci_publish_job, infra_ci_cd_build_deploy_cd_job [EXTRACTED 1.00]
- **Image tag conventions (sha-<40> / <40> / <7>)** — backend__github_workflows_cd_tag_sha_40, genai__github_workflows_genai_ci_tag_sha_40, infra_ci_cd_build_deploy_tag_sha_7, infra_ci_cd_promote_promote_job [INFERRED 0.85]
- **Emulator shims for AWS actions** — emulator_run_workflow_configure_aws_credentials, emulator_run_workflow_amazon_ecr_login, emulator_run_workflow_build_push, backend__github_workflows_cd_configure_aws_credentials, backend__github_workflows_cd_amazon_ecr_login, backend__github_workflows_cd_build_push_action [INFERRED 0.95]

## Communities (10 total, 2 thin omitted)

### Community 0 - "Emulator workflow runner"
Cohesion: 0.09
Nodes (11): aws-actions/configure-aws-credentials, amazon_ecr_login(), aws(), configure_aws_credentials(), evaluate(), interp(), sub(), main() (+3 more)

### Community 1 - "Emulator test harness"
Cohesion: 0.10
Nodes (27): AWS_ACCESS_KEY_ID, AWS_CONFIG_FILE, AWS_DEFAULT_REGION, AWS_ENDPOINT_URL, AWS_PAGER, AWS_REGION, AWS_SECRET_ACCESS_KEY, AWS_SHARED_CREDENTIALS_FILE (+19 more)

### Community 2 - "ECR publish pipelines"
Cohesion: 0.12
Nodes (22): aws-actions/amazon-ecr-login, backend CD build-and-push job, docker/build-push-action, backend CD workflow (develop push), ECR existence check (describe-images), image tag sha-<40>, build_push(), GenAI docker-validate job (+14 more)

### Community 3 - "CodeBuild GenAI runner"
Cohesion: 0.16
Nodes (21): aws_cloudwatch_log_group.this, aws_codebuild_project.this, aws_codebuild_webhook.this, aws_iam_role_policy_attachment.extra, aws_iam_role_policy.this, aws_iam_role.this, data.aws_caller_identity.current, data.aws_region.current (+13 more)

### Community 4 - "ECR repository module"
Cohesion: 0.19
Nodes (14): aws_ecr_lifecycle_policy.app, aws_ecr_repository.app, Terraform module: infra/terraform/modules/ecr, local.ecr_common_tags, output.registry_id, output.repository_arns, output.repository_urls, var.enabled (+6 more)

### Community 5 - "GitHub OIDC role module"
Cohesion: 0.20
Nodes (13): aws_iam_role_policy_attachment.this, aws_iam_role.this, data.aws_iam_openid_connect_provider.github, Terraform module: infra/terraform/modules/github_oidc, output.role_arn, output.role_name, var.github_org, var.github_org_id (+5 more)

### Community 6 - "Prod CI roles and policies"
Cohesion: 0.43
Nodes (7): aws_iam_policy.gha_backend_ecr, aws_iam_policy.gha_genai_ecr, aws_iam_policy.gha_genai_models_s3, module.github_oidc_backend, module.github_oidc_genai, output.github_actions_role_arn_backend, output.github_actions_role_arn_genai

### Community 7 - "backend CI (no AWS)"
Cohesion: 1.00
Nodes (4): backend CI workflow, CI docker-build-verify (no push), CI e2e-test job, CI test job (gradlew test)

## Knowledge Gaps
- **17 isolated node(s):** `AWS_ENDPOINT_URL`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`, `AWS_DEFAULT_REGION` (+12 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 42 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **2 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `module.ecr` connect `ECR publish pipelines` to `ECR repository module`?**
  _High betweenness centrality (0.014) - this node is a cross-community bridge._
- **Are the 3 inferred relationships involving `GenAI publish job (3-service matrix)` (e.g. with `module.codebuild_genai_runner` and `module.ecr`) actually correct?**
  _`GenAI publish job (3-service matrix)` has 3 INFERRED edges - model-reasoned connections that need verification._
- **What connects `AWS_ENDPOINT_URL`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` to the rest of the system?**
  _17 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Emulator workflow runner` be split into smaller, more focused modules?**
  _Cohesion score 0.09032258064516129 - nodes in this community are weakly interconnected._
- **Are the 2 inferred relationships involving `backend CD build-and-push job` (e.g. with `module.ecr` and `module.github_oidc_backend`) actually correct?**
  _`backend CD build-and-push job` has 2 INFERRED edges - model-reasoned connections that need verification._
- **Should `Emulator test harness` be split into smaller, more focused modules?**
  _Cohesion score 0.10344827586206896 - nodes in this community are weakly interconnected._
- **Should `ECR publish pipelines` be split into smaller, more focused modules?**
  _Cohesion score 0.12 - nodes in this community are weakly interconnected._