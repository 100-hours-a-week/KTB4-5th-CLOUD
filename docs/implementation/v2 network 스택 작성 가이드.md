# v2 network 스택 작성 가이드 (M14)

> **2026-10-08 변경 (구현 기록 I6)**: v2 dev는 ECS 인스턴스 1대 구조로 바뀌어 이 문서의 서브넷·NAT·ALB 구성은 **v2 prod용**으로 남깁니다. dev는 [v2 구현 기록.md](v2%20구현%20기록.md#dev-구축-순서-ecs-인스턴스-1대)를 따릅니다.

> `v2 네트워크 진입점 todo.md`(저장소에 올리지 않은 작업 목록)를 CloudFormation 템플릿으로 실제로 쓰는 순서입니다.
> 템플릿 파일 하나(`infra/v2/network.yaml`)를 **단계마다 조금씩 늘리고, 단계마다 PR 하나로 올려** [v2 구현 기록.md 0단계](v2%20구현%20기록.md#0단계-배포-파이프라인-pr--미리보기-main-머지--적용)이 적용하게 합니다. 한 번에 다 쓰고 배포하면 어디서 틀렸는지 찾기 어렵기 때문입니다.
> 아래 "배포 명령"은 파이프라인이 생기기 전이나 문제를 손으로 확인할 때만 씁니다. 평소에는 PR → 머지로 적용합니다.
> 아래 YAML 조각을 모두 합친 템플릿은 `cfn-lint`를 통과했습니다(2026-10-07).

## 전체 흐름

1. 준비 (CLI, cfn-lint, 파라미터 값)
2. 뼈대 + 서브넷 → 배포 (스택 생성)
3. 라우팅 + NAT + S3 엔드포인트 → 배포 (스택 업데이트)
4. 보안그룹 → 배포
5. ACM 인증서 → 배포
6. ALB + 타겟 그룹 + 리스너 → 배포
7. S3 + Redis → 배포
8. Outputs → 배포, 완료 확인

2단계(첫 PR 머지)에서 스택이 생기고, 이후 PR은 모두 **같은 스택을 업데이트**합니다. 이미 만든 리소스는 그대로 두고 새로 추가한 것만 생깁니다.

---

## 1. 준비

- [ ] AWS CLI 동작 확인: `aws sts get-caller-identity`
- [ ] cfn-lint 설치 (템플릿 문법·속성 오류를 배포 전에 잡아 줌): `pip install cfn-lint`
- [ ] VS Code를 쓰면 확장 "CloudFormation Linter" 설치 (자동완성·빨간 줄 표시)
- [ ] todo 0단계에서 모은 값 준비: VPC ID, IGW ID, AZ 2개, 빈 CIDR 4개, Route 53 호스티드 존 ID, 도메인 이름
  - 도메인은 아직 미결정(`dev.dameokja.com` 전환 vs `v2.dameokja.com` 분기)이라 **파라미터로 받게** 만들어서 어느 쪽이든 값만 바꾸면 됩니다

### 배포 명령 (모든 단계 공통)

PowerShell 기준입니다. 줄바꿈은 `` ` ``(백틱)입니다.

```powershell
cfn-lint infra/v2/network.yaml

aws cloudformation deploy `
  --region ap-northeast-2 `
  --stack-name dameokja-dev-network `
  --template-file infra/v2/network.yaml `
  --parameter-overrides Env=dev VpcId=vpc-xxxx InternetGatewayId=igw-xxxx `
      AzA=ap-northeast-2a AzB=ap-northeast-2b `
      HostedZoneId=Zxxxx DomainName=v2.dameokja.com `
  --no-execute-changeset
```

- `--no-execute-changeset`: 바로 만들지 않고 change set(무엇이 생기고 바뀌는지)만 만듭니다. CloudFormation 콘솔 → 스택 → 변경 세트에서 내용을 보고 **실행** 버튼을 누르면 적용됩니다. 익숙해지면 이 옵션을 빼고 바로 적용해도 됩니다.
- 콘솔로만 하고 싶으면: CloudFormation → 스택 생성 → "템플릿 파일 업로드" (2단계), 이후는 스택 선택 → 업데이트 → "기존 템플릿 대체"로 같은 파일을 다시 올리면 됩니다.

### 실패했을 때

- 콘솔 → 스택 → **이벤트** 탭에서 가장 먼저 나온 `CREATE_FAILED`/`UPDATE_FAILED`의 상태 사유를 봅니다. 그 뒤 실패들은 대부분 연쇄입니다.
- **첫 생성(2단계)이 실패하면** 스택이 `ROLLBACK_COMPLETE` 상태가 되어 업데이트가 안 됩니다. 스택을 삭제하고 다시 만드세요.
- 업데이트가 실패하면 직전 상태로 자동 롤백되므로, 템플릿을 고쳐서 다시 배포하면 됩니다.

---

## 2. 뼈대 + 서브넷

`infra/v2/network.yaml`을 새로 만들고 아래를 씁니다.

> 실제 첫 PR 파일은 저장소의 `infra/v2/network.yaml`(현재 저장소에는 없음)이고, 아래 예시에서 서브넷이 쓰지 않는 파라미터 `InternetGatewayId`(3단계), `HostedZoneId`·`DomainName`(5단계)을 뺀 것입니다. 템플릿에 없는 파라미터를 파라미터 파일로 넘기면 배포가 실패하므로, 그 단계에서 템플릿과 `params/network.dev.json`에 함께 추가합니다.

```yaml
AWSTemplateFormatVersion: "2010-09-09"
Description: dameokja v2 network entry point (subnets, NAT, SG, ALB, ACM, S3, Redis)

Parameters:
  Env:
    Type: String
    AllowedValues: [dev, prod]
  VpcId:
    Type: AWS::EC2::VPC::Id
  InternetGatewayId:
    Type: String
  AzA:
    Type: AWS::EC2::AvailabilityZone::Name
  AzB:
    Type: AWS::EC2::AvailabilityZone::Name
  PublicSubnetACidr:
    Type: String
    Default: 172.31.64.0/20
  PublicSubnetBCidr:
    Type: String
    Default: 172.31.80.0/20
  PrivateSubnetACidr:
    Type: String
    Default: 172.31.96.0/20
  PrivateSubnetBCidr:
    Type: String
    Default: 172.31.112.0/20
  HostedZoneId:
    Type: AWS::Route53::HostedZone::Id
  DomainName:
    Type: String

Resources:
  PublicSubnetA:
    Type: AWS::EC2::Subnet
    Properties:
      VpcId: !Ref VpcId
      AvailabilityZone: !Ref AzA
      CidrBlock: !Ref PublicSubnetACidr
      MapPublicIpOnLaunch: true
      Tags:
        - { Key: Name, Value: !Sub "dameokja-${Env}-public-a" }

  PublicSubnetB:
    Type: AWS::EC2::Subnet
    Properties:
      VpcId: !Ref VpcId
      AvailabilityZone: !Ref AzB
      CidrBlock: !Ref PublicSubnetBCidr
      MapPublicIpOnLaunch: true
      Tags:
        - { Key: Name, Value: !Sub "dameokja-${Env}-public-b" }

  PrivateSubnetA:
    Type: AWS::EC2::Subnet
    Properties:
      VpcId: !Ref VpcId
      AvailabilityZone: !Ref AzA
      CidrBlock: !Ref PrivateSubnetACidr
      MapPublicIpOnLaunch: false
      Tags:
        - { Key: Name, Value: !Sub "dameokja-${Env}-private-a" }

  PrivateSubnetB:
    Type: AWS::EC2::Subnet
    Properties:
      VpcId: !Ref VpcId
      AvailabilityZone: !Ref AzB
      CidrBlock: !Ref PrivateSubnetBCidr
      MapPublicIpOnLaunch: false
      Tags:
        - { Key: Name, Value: !Sub "dameokja-${Env}-private-b" }
```

읽는 법:
- `Parameters`: 배포할 때 바깥에서 넣는 값. `Type: AWS::EC2::VPC::Id`처럼 AWS 전용 타입을 쓰면 없는 ID를 넣었을 때 배포 전에 막아 줍니다.
- `Resources`: 만들 리소스. 왼쪽 이름(`PublicSubnetA`)은 템플릿 안에서만 쓰는 논리 ID입니다.
- `!Ref`: 파라미터 값, 또는 다른 리소스의 ID를 가져옵니다. `!Sub`: 문자열 안에 `${...}`로 값을 끼워 넣습니다.

**확인**: VPC 콘솔 → 서브넷에 `dameokja-dev-*` 4개가 지정한 AZ·CIDR로 생겼는지.

---

## 3. 라우팅 + NAT + S3 엔드포인트

`Resources:` 아래에 이어서 추가합니다.

```yaml
  # --- public: 0.0.0.0/0 -> 기존 IGW
  PublicRouteTable:
    Type: AWS::EC2::RouteTable
    Properties:
      VpcId: !Ref VpcId
      Tags:
        - { Key: Name, Value: !Sub "dameokja-${Env}-public-rt" }

  PublicDefaultRoute:
    Type: AWS::EC2::Route
    Properties:
      RouteTableId: !Ref PublicRouteTable
      DestinationCidrBlock: 0.0.0.0/0
      GatewayId: !Ref InternetGatewayId

  PublicSubnetARouteTableAssociation:
    Type: AWS::EC2::SubnetRouteTableAssociation
    Properties:
      SubnetId: !Ref PublicSubnetA
      RouteTableId: !Ref PublicRouteTable

  PublicSubnetBRouteTableAssociation:
    Type: AWS::EC2::SubnetRouteTableAssociation
    Properties:
      SubnetId: !Ref PublicSubnetB
      RouteTableId: !Ref PublicRouteTable

  # --- NAT 1개 (AzA = 2b)
  NatEip:
    Type: AWS::EC2::EIP
    Properties:
      Domain: vpc
      Tags:
        - { Key: Name, Value: !Sub "dameokja-${Env}-nat-eip" }

  NatGateway:
    Type: AWS::EC2::NatGateway
    Properties:
      AllocationId: !GetAtt NatEip.AllocationId
      SubnetId: !Ref PublicSubnetA
      Tags:
        - { Key: Name, Value: !Sub "dameokja-${Env}-nat" }

  # --- private: 0.0.0.0/0 -> NAT
  PrivateRouteTable:
    Type: AWS::EC2::RouteTable
    Properties:
      VpcId: !Ref VpcId
      Tags:
        - { Key: Name, Value: !Sub "dameokja-${Env}-private-rt" }

  PrivateDefaultRoute:
    Type: AWS::EC2::Route
    Properties:
      RouteTableId: !Ref PrivateRouteTable
      DestinationCidrBlock: 0.0.0.0/0
      NatGatewayId: !Ref NatGateway

  PrivateSubnetARouteTableAssociation:
    Type: AWS::EC2::SubnetRouteTableAssociation
    Properties:
      SubnetId: !Ref PrivateSubnetA
      RouteTableId: !Ref PrivateRouteTable

  PrivateSubnetBRouteTableAssociation:
    Type: AWS::EC2::SubnetRouteTableAssociation
    Properties:
      SubnetId: !Ref PrivateSubnetB
      RouteTableId: !Ref PrivateRouteTable

  # --- S3 게이트웨이 엔드포인트 (무료, S3 트래픽이 NAT를 타지 않음)
  S3GatewayEndpoint:
    Type: AWS::EC2::VPCEndpoint
    Properties:
      VpcId: !Ref VpcId
      ServiceName: !Sub "com.amazonaws.${AWS::Region}.s3"
      VpcEndpointType: Gateway
      RouteTableIds:
        - !Ref PrivateRouteTable
```

- `!GetAtt 리소스.속성`: `!Ref`가 주는 ID 말고 다른 값(여기선 EIP의 AllocationId)이 필요할 때 씁니다.
- **이 단계부터 NAT 요금(시간당)이 나옵니다.**

**확인**: VPC 콘솔 → 라우팅 테이블에서 `private-rt`의 `0.0.0.0/0` 대상이 `nat-...`, `public-rt`는 `igw-...`인지. 기존 서브넷의 라우팅 테이블은 바뀌지 않았는지(기존 DB·v1 서버 영향 없음).

---

## 4. 보안그룹

SG끼리 서로 참조하면 "A를 만들려면 B가 필요하고 B를 만들려면 A가 필요한" 순환 참조 오류가 납니다. 그래서 **SG 본체는 먼저 만들고, 서로를 가리키는 규칙은 별도 리소스**(`SecurityGroupIngress`/`SecurityGroupEgress`)로 뺍니다.

```yaml
  AlbSg:
    Type: AWS::EC2::SecurityGroup
    Properties:
      GroupName: !Sub "dameokja-${Env}-alb-sg"
      GroupDescription: ALB - public 80/443
      VpcId: !Ref VpcId
      SecurityGroupIngress:
        - { IpProtocol: tcp, FromPort: 80, ToPort: 80, CidrIp: 0.0.0.0/0 }
        - { IpProtocol: tcp, FromPort: 443, ToPort: 443, CidrIp: 0.0.0.0/0 }
      SecurityGroupEgress:
        - { IpProtocol: tcp, FromPort: 80, ToPort: 80, DestinationSecurityGroupId: !GetAtt AppSg.GroupId }

  AppSg:
    Type: AWS::EC2::SecurityGroup
    Properties:
      GroupName: !Sub "dameokja-${Env}-app-sg"
      GroupDescription: ECS task (nginx+FE+BE)
      VpcId: !Ref VpcId
      SecurityGroupEgress:
        - { IpProtocol: tcp, FromPort: 443, ToPort: 443, CidrIp: 0.0.0.0/0 }
        - { IpProtocol: tcp, FromPort: 80, ToPort: 80, CidrIp: 0.0.0.0/0 }

  DbSg:
    Type: AWS::EC2::SecurityGroup
    Properties:
      GroupName: !Sub "dameokja-${Env}-db-sg"
      GroupDescription: MySQL - 3306 from app only
      VpcId: !Ref VpcId
      SecurityGroupIngress:
        - { IpProtocol: tcp, FromPort: 3306, ToPort: 3306, SourceSecurityGroupId: !GetAtt AppSg.GroupId }
      SecurityGroupEgress:
        - { IpProtocol: tcp, FromPort: 443, ToPort: 443, CidrIp: 0.0.0.0/0 }

  RedisSg:
    Type: AWS::EC2::SecurityGroup
    Properties:
      GroupName: !Sub "dameokja-${Env}-redis-sg"
      GroupDescription: Redis - 6379 from app only
      VpcId: !Ref VpcId
      SecurityGroupIngress:
        - { IpProtocol: tcp, FromPort: 6379, ToPort: 6379, SourceSecurityGroupId: !GetAtt AppSg.GroupId }

  # --- 서로를 가리키는 규칙은 따로 (순환 참조 방지)
  AppSgIngressFromAlb:
    Type: AWS::EC2::SecurityGroupIngress
    Properties:
      GroupId: !GetAtt AppSg.GroupId
      IpProtocol: tcp
      FromPort: 80
      ToPort: 80
      SourceSecurityGroupId: !GetAtt AlbSg.GroupId

  AppSgEgressToDb:
    Type: AWS::EC2::SecurityGroupEgress
    Properties:
      GroupId: !GetAtt AppSg.GroupId
      IpProtocol: tcp
      FromPort: 3306
      ToPort: 3306
      DestinationSecurityGroupId: !GetAtt DbSg.GroupId

  AppSgEgressToRedis:
    Type: AWS::EC2::SecurityGroupEgress
    Properties:
      GroupId: !GetAtt AppSg.GroupId
      IpProtocol: tcp
      FromPort: 6379
      ToPort: 6379
      DestinationSecurityGroupId: !GetAtt RedisSg.GroupId
```

- `SecurityGroupEgress`를 하나라도 적으면 기본 "아웃바운드 전체 허용"이 사라지고 적은 것만 남습니다. 그래서 `RedisSg`처럼 아웃바운드를 안 적은 SG는 기본 전체 허용이 남습니다(Redis는 먼저 밖으로 나갈 일이 없어 영향은 작음).
- `AppSg` 아웃바운드는 v1 앱 서버와 같이 80·443만 열었습니다. BE가 AI 서버 등 다른 포트로 나가는 곳이 있으면 여기에 추가하세요.
- `DbSg`는 오늘 만들기만 하고 기존 DB에는 붙이지 않습니다.

**확인**: EC2 콘솔 → 보안 그룹에서 4개와 규칙. `app-sg` 인바운드 출발지가 `alb-sg`인지.

---

## 5. ACM 인증서

```yaml
  Certificate:
    Type: AWS::CertificateManager::Certificate
    Properties:
      DomainName: !Ref DomainName
      ValidationMethod: DNS
      DomainValidationOptions:
        - DomainName: !Ref DomainName
          HostedZoneId: !Ref HostedZoneId
```

- `HostedZoneId`를 주면 CloudFormation이 Route 53에 검증용 CNAME을 자동으로 만들고, 발급될 때까지 기다립니다. 보통 몇 분, 스택이 `CREATE_IN_PROGRESS`에서 멈춘 것처럼 보여도 정상입니다.

**확인**: ACM 콘솔(서울 리전)에서 상태 "발급됨".

---

## 6. ALB + 타겟 그룹 2개 + 리스너

```yaml
  Alb:
    Type: AWS::ElasticLoadBalancingV2::LoadBalancer
    Properties:
      Name: !Sub "dameokja-${Env}-alb"
      Type: application
      Scheme: internet-facing
      Subnets: [!Ref PublicSubnetA, !Ref PublicSubnetB]
      SecurityGroups: [!GetAtt AlbSg.GroupId]
      LoadBalancerAttributes:
        - { Key: idle_timeout.timeout_seconds, Value: "300" }   # SSE 연결 유지 (임시 기본값)

  TgBlue:
    Type: AWS::ElasticLoadBalancingV2::TargetGroup
    Properties:
      Name: !Sub "dameokja-${Env}-tg-blue"
      VpcId: !Ref VpcId
      Protocol: HTTP
      Port: 80
      TargetType: ip                 # ECS awsvpc 모드
      HealthCheckPath: /nginx-health # v1 nginx 설정에 있는 경로
      Matcher: { HttpCode: "200" }
      TargetGroupAttributes:
        - { Key: deregistration_delay.timeout_seconds, Value: "30" }

  TgGreen:
    Type: AWS::ElasticLoadBalancingV2::TargetGroup
    Properties:
      Name: !Sub "dameokja-${Env}-tg-green"
      VpcId: !Ref VpcId
      Protocol: HTTP
      Port: 80
      TargetType: ip
      HealthCheckPath: /nginx-health
      Matcher: { HttpCode: "200" }
      TargetGroupAttributes:
        - { Key: deregistration_delay.timeout_seconds, Value: "30" }

  HttpListener:
    Type: AWS::ElasticLoadBalancingV2::Listener
    Properties:
      LoadBalancerArn: !Ref Alb
      Port: 80
      Protocol: HTTP
      DefaultActions:
        - Type: redirect
          RedirectConfig: { Protocol: HTTPS, Port: "443", StatusCode: HTTP_301 }

  HttpsListener:
    Type: AWS::ElasticLoadBalancingV2::Listener
    Properties:
      LoadBalancerArn: !Ref Alb
      Port: 443
      Protocol: HTTPS
      SslPolicy: ELBSecurityPolicy-TLS13-1-2-2021-06
      Certificates:
        - CertificateArn: !Ref Certificate
      DefaultActions:
        - Type: fixed-response
          FixedResponseConfig: { StatusCode: "404", ContentType: text/plain, MessageBody: not found }

  # ECS Native Blue/Green(M16)이 이 규칙의 가중치를 바꿔 가며 전환함
  ProductionListenerRule:
    Type: AWS::ElasticLoadBalancingV2::ListenerRule
    Properties:
      ListenerArn: !Ref HttpsListener
      Priority: 1
      Conditions:
        - Field: path-pattern
          PathPatternConfig: { Values: ["/*"] }
      Actions:
        - Type: forward
          ForwardConfig:
            TargetGroups:
              - { TargetGroupArn: !Ref TgBlue, Weight: 100 }
              - { TargetGroupArn: !Ref TgGreen, Weight: 0 }
```

- ALB·타겟 그룹·리스너·인증서는 `!Ref`가 ARN을 돌려줍니다.
- **이 단계부터 ALB 요금(시간당)이 나옵니다.**

**확인**:
```powershell
$alb = aws elbv2 describe-load-balancers --names dameokja-dev-alb --query "LoadBalancers[0].DNSName" --output text
curl.exe -I "http://$alb"     # 301 (https로 리다이렉트)
curl.exe -kI "https://$alb"   # 503 (타겟이 아직 없어서 정상)
```
PowerShell의 `curl`은 다른 명령의 별칭이라 `curl.exe`로 씁니다.

---

## 7. S3 버킷 + Redis (Redis는 형태 결정 후)

```yaml
  ProfileImageBucket:
    Type: AWS::S3::Bucket
    DeletionPolicy: Retain          # 스택을 지워도 사진은 남김
    UpdateReplacePolicy: Retain
    Properties:
      BucketName: !Sub "dameokja-${Env}-profile-images-${AWS::AccountId}"
      PublicAccessBlockConfiguration:
        BlockPublicAcls: true
        BlockPublicPolicy: true
        IgnorePublicAcls: true
        RestrictPublicBuckets: true
      VersioningConfiguration:
        Status: Enabled
      BucketEncryption:
        ServerSideEncryptionConfiguration:
          - ServerSideEncryptionByDefault: { SSEAlgorithm: AES256 }

  RedisSubnetGroup:
    Type: AWS::ElastiCache::SubnetGroup
    Properties:
      Description: !Sub "dameokja-${Env} redis subnets"
      SubnetIds: [!Ref PrivateSubnetA, !Ref PrivateSubnetB]

  Redis:
    Type: AWS::ElastiCache::CacheCluster
    Properties:
      ClusterName: !Sub "dameokja-${Env}-redis"
      Engine: redis
      CacheNodeType: cache.t4g.micro
      NumCacheNodes: 1
      CacheSubnetGroupName: !Ref RedisSubnetGroup
      VpcSecurityGroupIds: [!GetAtt RedisSg.GroupId]
```

- 위 Redis 부분은 **ElastiCache로 정했을 때**의 예시입니다. EC2 자체구축으로 정하면 이 부분을 EC2 인스턴스 리소스로 바꿉니다(`Redis ElastiCache vs EC2 비교.md`, 저장소에 올리지 않은 비교 메모).
- ElastiCache는 생성에 10분 안팎 걸려 이번 배포가 가장 오래 걸립니다.

**확인**: S3 콘솔에 버킷(퍼블릭 액세스 차단 "켜짐"), ElastiCache 콘솔에서 상태 "available".

---

## 8. Outputs (10/9 ecs·service 스택이 가져다 씀)

`Resources:`와 같은 들여쓰기 수준(맨 왼쪽)에 추가합니다.

```yaml
Outputs:
  PublicSubnetA:  { Value: !Ref PublicSubnetA,  Export: { Name: !Sub "${AWS::StackName}-PublicSubnetA" } }
  PublicSubnetB:  { Value: !Ref PublicSubnetB,  Export: { Name: !Sub "${AWS::StackName}-PublicSubnetB" } }
  PrivateSubnetA: { Value: !Ref PrivateSubnetA, Export: { Name: !Sub "${AWS::StackName}-PrivateSubnetA" } }
  PrivateSubnetB: { Value: !Ref PrivateSubnetB, Export: { Name: !Sub "${AWS::StackName}-PrivateSubnetB" } }
  AlbSgId:   { Value: !GetAtt AlbSg.GroupId,   Export: { Name: !Sub "${AWS::StackName}-AlbSgId" } }
  AppSgId:   { Value: !GetAtt AppSg.GroupId,   Export: { Name: !Sub "${AWS::StackName}-AppSgId" } }
  DbSgId:    { Value: !GetAtt DbSg.GroupId,    Export: { Name: !Sub "${AWS::StackName}-DbSgId" } }
  RedisSgId: { Value: !GetAtt RedisSg.GroupId, Export: { Name: !Sub "${AWS::StackName}-RedisSgId" } }
  AlbArn:          { Value: !Ref Alb,                           Export: { Name: !Sub "${AWS::StackName}-AlbArn" } }
  AlbDnsName:      { Value: !GetAtt Alb.DNSName,                Export: { Name: !Sub "${AWS::StackName}-AlbDnsName" } }
  AlbHostedZoneId: { Value: !GetAtt Alb.CanonicalHostedZoneID,  Export: { Name: !Sub "${AWS::StackName}-AlbHostedZoneId" } }
  HttpsListenerArn:          { Value: !Ref HttpsListener,          Export: { Name: !Sub "${AWS::StackName}-HttpsListenerArn" } }
  ProductionListenerRuleArn: { Value: !Ref ProductionListenerRule, Export: { Name: !Sub "${AWS::StackName}-ProductionListenerRuleArn" } }
  TgBlueArn:  { Value: !Ref TgBlue,  Export: { Name: !Sub "${AWS::StackName}-TgBlueArn" } }
  TgGreenArn: { Value: !Ref TgGreen, Export: { Name: !Sub "${AWS::StackName}-TgGreenArn" } }
  ProfileImageBucketName: { Value: !Ref ProfileImageBucket, Export: { Name: !Sub "${AWS::StackName}-ProfileImageBucketName" } }
  RedisEndpoint: { Value: !GetAtt Redis.RedisEndpoint.Address, Export: { Name: !Sub "${AWS::StackName}-RedisEndpoint" } }
  RedisPort:     { Value: !GetAtt Redis.RedisEndpoint.Port,    Export: { Name: !Sub "${AWS::StackName}-RedisPort" } }
```

- 다음 스택에서는 `!ImportValue dameokja-dev-network-PrivateSubnetA`처럼 가져다 씁니다.
- Export된 값을 다른 스택이 쓰는 중이면 그 Output은 지우거나 바꿀 수 없으니 이름을 처음에 잘 정해 두세요.

**확인**: CloudFormation 콘솔 → 스택 → **출력** 탭에 값이 다 나오는지. 스택 상태 `UPDATE_COMPLETE`.

---

## 완료 기준 체크

- [ ] 스택 상태 `CREATE_COMPLETE` 또는 `UPDATE_COMPLETE`
- [ ] `http://<ALB DNS>` → 301, `https://<ALB DNS>` → 503
- [ ] 출력 탭 값 18개
- [ ] Route 53 레코드는 아직 ALB로 연결하지 않음 (ECS가 응답하는 10/9 이후 서브도메인부터)
- [ ] CLOUD 저장소에 `infra/v2/network.yaml` 커밋

## 오늘 이후 정리할 것

- v2를 `dev.dameokja.com`으로 전환할 때(I4)는 인증서에 그 도메인을 추가해 다시 배포
- 하루 작업이 끝나도 스택은 지우지 않음 (다음 스택이 이 스택 위에 쌓임). 비용이 걱정되면 NAT만 따로 지우는 건 스택과 어긋나므로 하지 말고, 지울 땐 스택 단위로
