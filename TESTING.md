# 무엇으로 테스트했나

**실제 AWS 계정에는 요청을 하나도 보내지 않았습니다.** 테스트에서 "AWS" 역할은 전부 로컬 Docker 컨테이너로 띄운
**MiniStack 1.5.24**(`127.0.0.1:4566`)가 맡았습니다. AWS 가 운영하는 엔드포인트 중 접속한 곳은
T8 원본 이미지를 익명으로 읽은 `public.ecr.aws` 하나입니다 ([외부 네트워크](#외부-네트워크-접속)).

## 실행 환경

| 구분 | 도구 | 버전 |
|---|---|---|
| 호스트 | macOS (Apple Silicon) | 27.0.1, arm64 |
| 컨테이너 | Docker Desktop (buildx 포함) | 4.83.0 (client 29.6.2) |
| AWS 에뮬레이터 | `ministackorg/ministack` | 1.5.24 |
| AWS 클라이언트 | AWS CLI v2 | 2.37.11 |
| IaC | Terraform / `hashicorp/aws` / `hashicorp/tls` | 1.16.5 / 5.100.0 / 4.4.1 |
| 레지스트리 복사 | skopeo | 1.24.1 |
| 워크플로 러너 | `emulator/run_workflow.py` (자체 제작) | Python 3.14.3, PyYAML 6.0.3 |
| 판정 | bash, jq | jq 1.7.1 |
| Java | `eclipse-temurin:25-jdk-noble` 컨테이너 (프로젝트가 JDK 25 요구, 호스트는 26) | 25 |
| backend 런타임 | Spring Boot / AWS SDK for Java v2 BOM | 4.0.3 / 2.54.13 |
| 스텁 이미지 | `busybox` | 1.36 |

## MiniStack 이 대신한 AWS 서비스

| AWS 서비스 | 호출한 쪽 | 테스트 |
|---|---|---|
| ECR API (`DescribeImages`, `BatchGetImage`, `GetAuthorizationToken`, 레포 생성·정책) | Terraform, 워크플로 `run:` 스텝의 AWS CLI, 액션 shim | T0–T8 |
| ECR 레지스트리 (Docker Registry V2, 같은 포트의 `/v2/`) | `docker buildx` push, `docker push`, skopeo copy | T1–T8 |
| STS `AssumeRoleWithWebIdentity` | `configure-aws-credentials` shim (AWS CLI) | T1–T8 |
| IAM (OIDC 공급자, 역할, 정책) | `infra/scripts/bootstrap-github-oidc.sh` (AWS CLI), Terraform | T0 |
| S3 (버킷, 정책, 암호화, 객체) | Terraform, AWS SDK for Java v2 `S3Client`·`S3Presigner`, `java.net.http` PUT | T0, T9 |
| KMS | Terraform (반품 버킷 키) | T0, T9 |
| Route 53 | AWS CLI (호스팅 존 미리 생성), Terraform (ACM 검증 레코드) | T0 |
| ACM (us-east-1), CloudFront (+OAC), WAFv2 | Terraform | T0 |

## 테스트별 실행 방법

| ID | 원본 | 실행 방법 | 대체·생략한 것 |
|---|---|---|---|
| T0 | infra `terraform/environments/prod` | `emulator/.work/` 로 복사 후 override 파일(local 백엔드, 프로바이더 엔드포인트 → MiniStack)로 `terraform init/plan/apply` | `-target` 2단계, 변수는 `terraform.tfvars.example`, Route 53 존은 에뮬레이터에 생성 |
| T1·T2 | backend `cd.yml` `build-and-push` | `run_workflow.py` 가 `run:` 스텝을 원문 그대로 bash 로 실행. JAR 빌드는 temurin 25 컨테이너, 이미지는 실제 `docker buildx build --push` | `./gradlew test` 생략. 액션 3개 shim |
| T3 | ECR IMMUTABLE 설정 | busybox 이미지를 같은 태그로 `docker push` | — |
| T4·T6 | infra `ci-cd/promote.yml` (초안) | `run_workflow.py` | GitOps 체크아웃·편집 스텝은 초안 조건상 미실행 |
| T5 | infra `ci-cd/build-deploy.yml` (초안) | `run_workflow.py --stub-build` | 테스트 명령 `true`, 이미지는 busybox 스텁 |
| T7 | GenAI `genai-ci.yml` `publish` | `run_workflow.py --stub-build`, matrix `service=chatbot-api` | 다른 leg, `test`·`docker-validate` job, `Check runner storage` 생략. 이미지는 스텁 |
| T8 | infra `ai-image-mirror.yml` + `scripts/mirror-ai-image.sh` | `run_workflow.py`. 스크립트가 부르는 실제 skopeo 앞에 PATH shim 을 둬서 대상 호스트만 `localhost:4566` 으로 바꾸고 TLS 검증을 끔. 원본은 `public.ecr.aws/aws-cli/aws-cli@<digest>` | apt 로 skopeo 설치하는 스텝 생략 (brew 설치본 사용) |
| T9 | backend `ImageStorageConfig` + `S3ImageStorage` | `S3StorageCheck.java` 를 temurin 25 컨테이너에서 실행. 클래스패스는 T1 이 만든 bootJar 레이어, 네트워크는 MiniStack 컨테이너 공유, 접속은 `AWS_ENDPOINT_URL_S3` 환경변수만으로 | Spring 앱 전체(컨트롤러·DB·인증)는 띄우지 않고 S3 빈만 구성 |

## GitHub Actions 액션 처리

| 액션 | 처리 |
|---|---|
| `actions/checkout`, `setup-java`, `setup-python`, `cache`, `upload-artifact`, `docker/setup-buildx-action` | 아무것도 안 함 (로컬 클론과 호스트 도구 사용) |
| `aws-actions/configure-aws-credentials` | shim: 서명 없는 가짜 JWT 로 `aws sts assume-role-with-web-identity` → 받은 키를 이후 스텝 환경변수로 |
| `aws-actions/amazon-ecr-login` | shim: `aws ecr get-authorization-token` → `docker login localhost:4566`, 출력 `registry=localhost:4566` |
| `docker/build-push-action` | shim: `docker buildx build [--push] --metadata-file` → `digest` 출력 |

## 외부 네트워크 접속

AWS 자격증명을 실어 보낸 곳은 없습니다.

| 대상 | 용도 |
|---|---|
| github.com (`jangingmall/*`) | 레포 클론 |
| Docker Hub | MiniStack, temurin, busybox 이미지 |
| Terraform Registry | 프로바이더 다운로드 |
| Gradle 저장소 (Maven Central 등) | backend JAR 빌드 의존성 |
| Homebrew | terraform, skopeo 설치 |
| `public.ecr.aws` (AWS 운영 공개 레지스트리) | T8 원본 이미지 inspect·copy, 익명 읽기 |

실제 AWS 로 요청이 새지 않았다고 보는 근거는 다음과 같습니다.

- 모든 AWS 클라이언트의 엔드포인트를 `localhost:4566` 으로 고정했습니다.
- 사용자 프로필·자격증명 파일은 `/dev/null` 로 막았습니다.
- 자격증명은 `test`/`test` 였습니다. 실제 AWS 에 닿았다면 인증 실패로 해당 테스트가 깨졌을 것입니다.

나머지 장치는 [README 안전장치](README.md#안전장치)에 있습니다.

## 실제 AWS 에서만 확인되는 것

에뮬레이터가 강제하지 않아서, 아래 항목은 이 테스트로 보장되지 않습니다.

- OIDC 신뢰 정책의 `sub`/`repository_id` 조건 (MiniStack 은 토큰·신뢰 정책을 검증하지 않음)
- IAM 최소 권한, 버킷 정책 (`DenyInsecureTransport` 등)
- 버킷 CORS (FE 브라우저 업로드)
- CloudFront·WAF 의 실제 트래픽 처리, ACM 인증서 발급
- EKS 에서의 IRSA 자격증명, amd64 이미지 실행 (backend 이미지는 arm64 로 빌드)
