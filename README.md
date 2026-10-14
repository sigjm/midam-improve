# midam-improve

[jangingmall](https://github.com/jangingmall) 조직 레포의 **AWS 접점(CI/CD, Terraform, 런타임 S3)** 을
[MiniStack](https://github.com/ministackorg/ministack)(로컬 AWS 에뮬레이터) 위에서 실제로 돌려 본 기록과, 그 과정에서 찾은 개선점입니다.

- 실제 AWS 계정·state 버킷에는 한 번도 접속하지 않았습니다 ([안전장치](#안전장치)).
- 조직 레포 4개는 수정하지 않았습니다. 이 레포는 하네스와 기록만 담습니다.
- 마지막 전체 실행: **52 passed / 0 failed** (2026-10-10)

## 대상

| 레포 | 브랜치 | 커밋 | 다룬 범위 |
|---|---|---|---|
| backend | develop | `b54bf64` | `.github/workflows/cd.yml` (ECR push), 런타임 S3 (`S3ImageStorage`) |
| GenAI | main | `1581553` | `.github/workflows/genai-ci.yml` `publish` job (chatbot-api leg) |
| infra | main | `777a8e4` | `terraform/environments/prod` 일부, `ai-image-mirror.yml`, `ci-cd/` 초안 2개, OIDC 부트스트랩 스크립트 |
| frontend | dev | `85a74c1` | 없음 (AWS 접점이 백엔드가 서명한 presigned URL 뿐이고, 에뮬레이터가 CORS 를 평가하지 않음) |

## 결과

| ID | 시나리오 | 검증 | 결과 |
|---|---|---|---|
| T0 | infra Terraform → MiniStack apply (리소스 3 + 44개), 재plan 변경 없음, 생성 설정 확인 | 9 | ✅ |
| T1 | backend CD, 새 SHA: 실제 JAR 빌드 → 실제 이미지 push, 워크플로 digest == ECR digest | 3 | ✅ |
| T2 | backend CD 재실행: 기존 태그 감지 → 빌드 건너뜀, 같은 digest | 3 | ✅ |
| T3 | IMMUTABLE: 같은 태그에 다른 이미지 push 거부, digest 불변 | 2 | ✅ |
| T4 | infra promote 초안 × backend CD 이미지: 태그 규칙 불일치로 실패 (재현) | 1 | ✅ |
| T5 | infra build-deploy 초안: `batch-get-image` 로 신규 판정 → push | 3 | ✅ |
| T6 | infra promote 초안: staging 검증 digest 일치 시 승격 후보 생성 / 불일치 시 실패 | 3 | ✅ |
| T7 | GenAI publish: 역할 변수 없으면 AWS 인증 전 실패 / 신규 push / 재실행 시 재사용 | 7 | ✅ |
| T8 | AI 이미지 미러: 태그 입력 거부 / 멀티아키 digest 보존 복사 / 재실행 멱등 | 5 | ✅ |
| T9 | backend 런타임 S3: 실제 `ImageStorageConfig` + `S3ImageStorage` × Terraform 이 만든 버킷 | 16 | ✅ |

<details>
<summary>T0 Terraform 에서 만든 것</summary>

`module.ecr`(ECR 5개), `module.github_oidc*`(OIDC 역할 3개 + IAM 정책 3개), `module.s3_returns`(+ `module.kms_app`),
`module.s3_images`(+ `module.s3_access`, 버킷 정책이 참조하는 CloudFront·ACM(us-east-1)·WAF·Route53 검증 레코드).
변수는 `terraform.tfvars.example`, Route53 호스팅 존은 에뮬레이터에 미리 만들어 ID 를 넘겼습니다.
</details>

<details>
<summary>T9 S3 검증 15개</summary>

presigned URL 이 에뮬레이터 공개 버킷을 가리킴 · presigned PUT 200 · `isValid` 통과 / 크기 초과 false / Content-Type 불일치 false / 없는 객체 false ·
`exists` true/false · 서버 측 `put` · `delete` · RETURN 은 반품 버킷으로 서명 · 반품 버킷 PUT 200 · 반품 객체 `aws:kms` 암호화 ·
반품 객체가 공개 버킷에 없음 · 서명과 다른 Content-Type PUT 403 · 서명 변조 URL PUT 403
</details>

## 발견한 문제와 개선 제안

조직 레포에는 아직 반영하지 않았습니다. 제안 코드는 에뮬레이터에서 다시 돌려 보지 않았습니다.

| # | 위치 | 문제 | 영향 | 제안 |
|---|---|---|---|---|
| 1 | infra `ci-cd/promote.yml`, `build-deploy.yml` ↔ backend `cd.yml` | 태그 규칙 불일치. backend CD 는 `sha-<40자리>`, infra 초안은 `<7자리>`, GenAI 는 `<40자리>` | 초안을 그대로 붙이면 backend 이미지 승격이 `ImageNotFoundException` 으로 실패 (T4 재현) | 조직 공통 규칙 하나로 통일 (예: backend CD 의 `sha-<40>`) 후 infra 초안 수정 |
| 2 | infra `terraform/modules/github_oidc/main.tf:55` | `for_each = toset(var.policy_arns)` 가 아직 생성 전인 정책 ARN 에 의존 | 빈 state 에서 prod 가 한 번에 plan 안 됨 (`Invalid for_each argument`). **새 정책 + 새 역할을 한 PR 로 추가할 때도 동일** | 아래 [제안 2](#제안-2-github_oidc-for_each) |
| 3 | backend `cd.yml` `ecr-check` 스텝 else 분기 | `exit "${STATUS}"` — CLI 가 0 으로 끝났는데 digest 가 비면 `exit 0` | 스텝 성공 + `exists` 출력 없음 → push 스텝이 skip → **이미지 없이 job 초록** (fail-open). 실제 ECR 에서는 이 경로에 안 들어가 잠재 결함. 에뮬레이터 패치 전 실제로 재현됨 | `exit 1` |
| 4 | backend `cd.yml` 스텝 순서 | 테스트·JAR 빌드가 ECR 존재 확인보다 먼저 | 이미 올라간 SHA 재실행에도 테스트·빌드 비용 전부 지불 | 인증·존재 확인을 앞으로, 테스트·빌드에 `if: steps.ecr-check.outputs.exists == 'false'` (infra `build-deploy.yml` 초안이 이미 이 순서) |
| 5 | backend `S3ImageStorageTest` | `S3Client`/`S3Presigner` 를 mock 으로만 검증 | presigned URL 서명, HeadObject 메타데이터, 404 처리가 테스트에서 실행되지 않음 | 코드 수정 없이 `AWS_ENDPOINT_URL_S3` 만으로 에뮬레이터에 붙는 것을 확인함 (T9). 필요하면 통합 테스트로 이식 |

### 제안 2 (github_oidc for_each)

키를 plan 시점에 정해지는 정적 문자열로 바꿉니다.

```hcl
# modules/github_oidc/variables.tf
variable "policy_arns" {
  description = "이 Role에 붙일 정책 ARN (키는 정적 이름)"
  type        = map(string)
}

# modules/github_oidc/main.tf
resource "aws_iam_role_policy_attachment" "this" {
  for_each   = var.policy_arns
  role       = aws_iam_role.this.name
  policy_arn = each.value
}

# environments/prod/github_oidc.tf
policy_arns = { ecr = aws_iam_policy.gha_backend_ecr.arn }                                         # backend
policy_arns = { ecr = aws_iam_policy.gha_genai_ecr.arn, models_s3 = aws_iam_policy.gha_genai_models_s3.arn } # genai
```

주의: 기존 state 의 attachment 주소가 `["<ARN>"]` → `["ecr"]` 로 바뀌어 plan 에 교체가 나옵니다.
`moved` 블록은 ARN(계정 ID 포함)을 코드에 적어야 하므로 infra 규칙 2번과 충돌합니다. 적용 방식은 infra 담당이 정해야 합니다.

### 제안 3 (`cd.yml`)

```diff
           else
             echo "${RESULT}" >&2
-            exit "${STATUS}"
+            exit 1
           fi
```

## 에뮬레이터(MiniStack 1.5.24)의 한계

| 항목 | 실제 AWS | MiniStack | 이 하네스의 처리 |
|---|---|---|---|
| ECR `DescribeImages` 없는 태그 | `ImageNotFoundException` | 빈 목록 + exit 0 | 컨테이너 안 `ecr.py` 를 패치 (`assert` 로 업스트림 변경 감지). 업스트림 이슈 후보 |
| STS `AssumeRoleWithWebIdentity` | 토큰·역할·신뢰 정책(`sub`) 검증 | 검증 안 함 | 신뢰 정책은 문서 내용만 확인 (T0) |
| IAM 권한 / 버킷 정책 | 강제 | 저장만 함 (`AUTH=true` 미시도) | 최소 권한, TLS 강제는 미검증 |
| 버킷 CORS | Origin 평가 | 모든 Origin 허용 | FE 브라우저 업로드 CORS 미검증 |
| presigned URL 서명 | 검증 | **검증함** (403) | T9 에서 확인 |
| ECR IMMUTABLE | 강제 | **강제함** | T3 에서 확인 |

## 검증하지 못한 것

- **Terraform 은 일부만**: VPC, EKS, 노드그룹, 나머지 버킷, 계정 보안 등은 apply 하지 않았습니다.
- **GitHub Actions 액션 3개는 shim**: `configure-aws-credentials`, `amazon-ecr-login`, `build-push-action` 의 실제 코드는 돌지 않았습니다. `run:` 스텝은 원문 그대로 실행했습니다.
- **건너뛴 스텝**: backend `./gradlew test`, GenAI `test`·`docker-validate` job 과 `Check runner storage`(GNU `df`), 미러의 apt skopeo 설치.
- **스텁 이미지**: GenAI 와 infra build-deploy 는 busybox 기반 스텁 이미지. backend 이미지만 실제 JAR 로 빌드했고 arm64 입니다.
- **GenAI 는 chatbot-api leg 만**, backend 는 S3 경계만 (컨트롤러·DB·인증 흐름, IRSA 미검증).
- frontend, `k8s-validate.yml`, CloudFront·WAF 의 실제 트래픽 동작.

## 실행

필요: docker, AWS CLI v2, terraform (1.16.5 로 확인), skopeo, jq, rsync, python3 + PyYAML. 네트워크(Gradle 의존성, `public.ecr.aws` 원본 이미지) 필요.

```bash
git clone https://github.com/sigjm/midam-improve.git && cd midam-improve
git clone -b develop https://github.com/jangingmall/backend.git
git clone https://github.com/jangingmall/GenAI.git
git clone https://github.com/jangingmall/infra.git

./emulator/test.sh        # 에뮬레이터 새로 기동 → 전체 실행 (약 5분). 로그: emulator/out/
./emulator/test.sh down   # 에뮬레이터 제거
```

| 파일 | 역할 |
|---|---|
| `emulator/test.sh` | MiniStack 기동·패치, T0–T9 실행과 판정 |
| `emulator/run_workflow.py` | 워크플로 YAML 의 job 하나를 로컬에서 실행하는 최소 러너 (`${{ }}` 평가, `if:`, 스텝 출력, matrix). AWS 액션 3개는 에뮬레이터용 shim |
| `emulator/S3StorageCheck.java` | backend 의 실제 S3 코드를 Spring 으로 엮어 에뮬레이터에 붙이는 검증. bootJar 레이어를 클래스패스로 사용, backend 레포에 파일 추가 없음 |

[nektos/act](https://github.com/nektos/act) 대신 자체 러너를 쓴 이유: GitHub OIDC 토큰이 없고, `amazon-ecr-login` 이 `*.amazonaws.com` 레지스트리로 고정되며, 무거운 비-AWS 스텝을 명시적으로 건너뛸 방법이 필요했습니다.

## 안전장치

- 가짜 자격증명(`test`/`test`), `AWS_ENDPOINT_URL=http://localhost:4566`, `AWS_CONFIG_FILE`/`AWS_SHARED_CREDENTIALS_FILE=/dev/null`, `AWS_PROFILE` 해제.
- `run_workflow.py` 는 엔드포인트가 localhost 가 아니면 실행을 거부합니다.
- 레지스트리 호스트(`<계정>.dkr.ecr.<리전>.amazonaws.com`)는 `localhost:4566` 으로 바꿔 접속합니다 (액션 shim, skopeo PATH shim).
- Terraform 은 `infra/terraform` 을 `emulator/.work/` 로 복사하고 override 파일로 **local 백엔드** + MiniStack 프로바이더를 씁니다. 백엔드가 local 이 아니면 plan 전에 중단합니다. 실제 state 버킷은 접근하지 않습니다.
- `S3StorageCheck` 는 에뮬레이터가 아닌 URL 로 PUT 하지 않고, HTTPS 는 죽은 프록시로 보냅니다.
- 계정 ID 는 코드·문서에 적지 않고 backend `cd.yml` 의 역할 ARN 에서 실행 시 읽습니다. 실행 로그(`emulator/out/`)와 state(`emulator/.work/`)에는 찍히므로 커밋하지 않습니다.
