# v2 prod 전환 방식 조사

> 질문: dev 인프라와 prod 인프라를 나눠 운영하다가 v2 prod로 어떻게 옮기나
> 조사일: 2026-10-08 · 담당: pappus.yoon(윤중선) · 관련: [v2 구현 기록.md](v2%20구현%20기록.md) I4

## 결론

AWS가 권하는 방식은 **"같은 템플릿으로 환경을 하나 더 만들고, DNS로 트래픽을 옮기는 것"**입니다. 이미 있는 v2 dev를 prod로 바꾸거나 옮기는 게 아닙니다.

```
1. 템플릿 검증      dev 파라미터로 배포 → dev에서 테스트 (지금 단계)
2. v2 prod 생성     같은 템플릿 + prod 파라미터로 prod VPC에 새 스택 배포 (Green)
                    v1 prod(Blue)는 그대로 서비스
3. 트래픽 전환      Route 53 가중치로 v1 prod → v2 prod 조금씩 이동
4. 롤백 대기        문제 있으면 가중치를 v1으로 되돌림
5. 정리             안정되면 v1 prod 제거
```

## 1. 환경마다 같은 템플릿, 다른 파라미터

CloudFormation 모범 사례는 템플릿을 재사용해 dev·test·prod를 만들고, 차이는 파라미터로 두라고 합니다.

> "you can reuse your templates to replicate your infrastructure in multiple environments ... so that you can test changes before implementing them into production."
> "for your development environments, you can specify a lower-cost instance type compared to your production environment, but all other configurations and settings remain the same."
> — [CloudFormation best practices](https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/best-practices.html)

우리 경우:

| | v2 dev | v2 prod |
| --- | --- | --- |
| 템플릿 | `infra/v2/*.yaml` | 같은 파일 |
| 파라미터 | `params/*.dev.json` | `params/*.prod.json` (prod VPC ID, 10.0 대역 CIDR, m 시리즈) |
| 스택 이름 | `dameokja-dev-*` | `dameokja-prod-*` |

그래서 "dev를 prod로 옮긴다"가 아니라 "prod 파라미터 파일을 추가하고 한 번 더 배포한다"가 됩니다. 서브넷·ALB·ECS 인스턴스는 만들어진 VPC에 묶여 있어 다른 VPC로 옮길 수 없기 때문에도 이 방식이 맞습니다.

## 2. 환경 단위 Blue/Green + Route 53

AWS Blue/Green 백서의 "DNS 라우팅 갱신" 방식이 우리 I4와 같습니다.

> "By updating the alias record, you can route traffic from the blue environment to the green environment."
> "you can define a percentage of traffic to go to the green environment and gradually update the weights until the green environment carries the full production traffic."
> "If issues arise during the deployment, you can roll back by updating the DNS record to shift traffic back to the blue environment."
> — [Update DNS Routing with Amazon Route 53](https://docs.aws.amazon.com/whitepapers/latest/blue-green-deployments/update-dns-routing-with-amazon-route-53.html)

- Blue = v1 prod (EC2 + Docker Compose), Green = v2 prod (ECS + ALB)
- prod 도메인 레코드를 가중치 레코드 두 개(v1 EC2 / v2 ALB)로 바꾸고 0 → 10 → 50 → 100%로 옮김
- **주의**: DNS TTL 때문에 일부 클라이언트는 한동안 이전 환경에 붙어 있습니다("certain sessions may still be tied to the previous environment"). 전환 하루 전쯤 TTL을 60초 정도로 낮춰 둡니다.
- 새 ALB는 트래픽이 갑자기 몰리면 확장에 시간이 걸리므로, 가중치를 단계적으로 올리는 편이 안전합니다.

## 3. DB는 두 환경이 공유 (가장 중요)

백서는 Blue와 Green이 **같은 데이터 저장소를 공유**하는 것을 기본으로 봅니다. Green은 새 운영 환경이 되니 최신 데이터가 필요하고, Blue는 롤백 때 최신 데이터가 필요하기 때문입니다.

> "Broadly, you accomplish this by having both the green and blue environments share the same data stores."
> "A general recommendation is to decouple schema changes from the code changes. This way, the relational database is outside of the environment boundary defined for the blue/green deployment and shared between the blue and green environments."
> — [Blue/Green Deployments on AWS (PDF)](https://docs.aws.amazon.com/pdfs/whitepapers/latest/blue-green-deployments/blue-green-deployments.pdf) (2021년 개정, AWS가 "참고용"으로 표시)

우리에게 뜻하는 것:
- **v2 prod는 prod VPC(10.0) 안에 만들어야 합니다.** v1 prod가 쓰는 prod MySQL에 v2 prod도 붙어야 하기 때문입니다.
- v2의 DB 작업(기존 Primary에 Standby·Replica 추가)은 v1이 계속 쓰는 DB를 **제자리에서 확장**하는 것이라 Blue/Green 경계 밖입니다. 전환 전에 먼저 끝내 두고, v1·v2 모두 같은 Primary에 씁니다.
- 스키마를 바꿔야 하면 v1 코드가 깨지지 않게 **추가만** 먼저 하고(컬럼·테이블 추가), 삭제는 v1을 내린 뒤에 합니다.
- S3는 공유가 쉬운 저장소로 분류됩니다. Redis(Refresh Token)는 v2에만 있으면, v1으로 롤백했을 때 v2에서 로그인한 사용자가 다시 로그인해야 할 수 있습니다(BE 구현 후 확인).

## 4. dev는 어떻게 되나

dev도 같은 순서를 한 번 먼저 거칩니다(I4: `v2.dameokja.com`에서 검증 후 `dev.dameokja.com`을 v1 dev → v2 dev로 전환). dev 전환이 prod 전환의 리허설이 됩니다.

## 우리 일정에 넣으면

| 시점 | 할 일 |
| --- | --- |
| ~10/16 | v2 dev 구축·검증 (현재 계획) |
| M20 뒤 | prod 전환 여부 결정 |
| 전환 준비 | prod DB에 Standby·Replica 추가, `params/*.prod.json` 작성, prod VPC 대역 확인(10.0.0.0/24가 VPC 전체라면 IP가 부족할 수 있어 보조 CIDR 검토) |
| 전환 | prod 스택 배포 → prod 서브도메인으로 검증 → Route 53 가중치 전환 |
| 정리 | 안정 확인 후 v1 prod EC2 제거 |

## 참고 자료

- [CloudFormation best practices](https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/best-practices.html): 템플릿 재사용, 파라미터, 수명 주기별 스택 분리
- [Blue/Green Deployments on AWS: Update DNS Routing with Amazon Route 53](https://docs.aws.amazon.com/whitepapers/latest/blue-green-deployments/update-dns-routing-with-amazon-route-53.html)
- [Blue/Green Deployments on AWS (전체 PDF)](https://docs.aws.amazon.com/pdfs/whitepapers/latest/blue-green-deployments/blue-green-deployments.pdf): 데이터 계층, 환경 경계
