# prod 이중 승인

## 배포 순서

1. FE·BE main push에서 AMD64 이미지를 한 번 빌드하고 stage에 배포한다.
2. 앱 저장소의 `prod` Environment reviewer가 승인한다.
3. 앱의 `request-prod` job이 CLOUD의 `prod-approval.yml`을 `main`에서 실행한다.
4. CLOUD가 요청 실행의 main push 여부, 재시도 번호, stage 성공 및 앱 승인 요청 job 성공을 확인한다.
5. CLOUD의 `prod` Environment reviewer가 이미지 digest와 요청 실행을 확인하고 승인한다.
6. 앱 파이프라인이 동일한 실행 ID·재시도 번호·이미지 digest에 대한 CLOUD 성공을 확인한 뒤 SSH로 prod에 배포한다.

CLOUD는 추가 승인 관문을 담당하며 실제 SSH 배포와 배포 비밀값은 앱 저장소에 유지한다. 앱은 최대 15분간 CLOUD 완료를 기다린다. 거절·취소·API 오류·시간 초과 시 prod 배포를 중단한다. 시간 초과 후에는 앱 파이프라인을 다시 실행하고 두 승인을 다시 받아야 한다.

## 머지 전 설정

- FE·BE와 CLOUD 세 저장소의 `prod` Environment에 Required reviewers를 직접 설정한다. `environment: prod` 선언만으로는 reviewer 승인이 강제되지 않는다.
- FE·BE에 `CLOUD_DISPATCH_TOKEN` Secret을 등록한다. CLOUD 저장소만 대상으로 하는 fine-grained PAT 또는 GitHub App 토큰을 사용하고 Actions read/write 권한을 부여한다. 토큰은 CLOUD workflow 실행과 결과 조회에 사용한다. 기본 `GITHUB_TOKEN`은 다른 저장소 workflow 실행에 사용할 수 없다.
- CLOUD의 워크플로 PR을 먼저 main에 머지한 뒤 앱 저장소 PR을 머지한다. 필요한 설정을 갖추기 전에는 앱 PR을 Draft로 유지한다.

현재 FE·BE는 공개 저장소이므로 CLOUD의 `GITHUB_TOKEN`으로 요청 실행을 읽는다. 비공개로 전환할 경우 별도의 원본 저장소 Actions read 권한 토큰을 구성해야 한다.

같은 사람을 두 저장소의 reviewer로 지정하면 같은 사람이 두 번 승인할 수 있다. 서로 다른 승인 주체를 원하면 reviewer와 self-review 정책을 별도로 설정한다.
