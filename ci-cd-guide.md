# CI/CD Guide

## 1. 현재 배포 방식 요약

현재 이 프로젝트는 **완전 자동 배포가 아니라 반자동 배포** 방식으로 운영한다.

흐름은 아래와 같다.

1. 로컬에서 코드 수정
2. `git push origin main`
3. GitHub Actions가 코드를 받아 Docker 이미지를 빌드
4. 빌드한 이미지를 `GHCR`에 push
5. 배포 서버에서 최신 이미지를 `pull`
6. 배포 서버에서 `docker compose up -d`로 컨테이너 재기동

즉:

- **CI**: 자동
- **CD**: 반자동

이다.

---

## 2. GHCR이란?

`GHCR`은 `GitHub Container Registry`의 약자다.

쉽게 말하면:

- GitHub 저장소는 **소스코드 저장소**
- GHCR은 **Docker 이미지 저장소**

역할을 한다.

이 프로젝트에서는 GitHub Actions가 Docker 이미지를 빌드한 뒤, 아래 같은 형태로 GHCR에 올린다.

```text
ghcr.io/sfdg4869/service-integration-control:latest
```

구성은 다음과 같다.

- `ghcr.io`: GitHub의 컨테이너 레지스트리 주소
- `sfdg4869`: GitHub 계정 또는 조직명
- `service-integration-control`: 이미지 이름
- `latest`: 태그

즉 서버는 GitHub 저장소에서 직접 코드를 받는 것이 아니라, **GHCR에 올라간 최신 Docker 이미지를 받아서 실행**한다.

---

## 3. 왜 서버 소스 파일은 안 바뀌는가?

현재 방식은 **코드 배포가 아니라 이미지 배포**다.

즉:

- 로컬에서 `main.py`, `deploy.yml`을 수정
- GitHub가 그 코드로 Docker 이미지를 새로 만듦
- 서버는 새 이미지를 pull 해서 컨테이너를 바꿈

이 구조에서는 서버 디렉터리의 파일:

- `main.py`
- `.github/workflows/deploy.yml`
- 기타 소스 파일

은 자동으로 바뀌지 않을 수 있다.

왜냐면 서버에서 `git pull`을 한 것이 아니라, **컨테이너 이미지만 교체했기 때문**이다.

중요한 것은:

- 서버 폴더의 소스가 아니라
- **컨테이너 안의 `/app/...` 파일이 최신인지**

이다.

즉 지금 구조는:

- 서버 폴더 = 소스 참고용일 수 있음
- 실행 기준 = 컨테이너 안의 파일

이라고 이해하면 된다.

---

## 4. 지금 가능한 반자동 배포 방식

현재 가장 현실적인 방식은 아래와 같다.

### 4-1. 로컬에서 push

```bash
git add .
git commit -m "Update application"
git push origin main
```

### 4-2. GitHub Actions가 자동으로 수행하는 것

현재 `.github/workflows/deploy.yml`은 **배포까지 하지 않고**, 아래까지만 수행한다.

1. Python 문법 체크
2. 프론트 JS 문법 체크
3. Docker image build
4. GHCR push

즉 현재 워크플로는 **Build And Publish Image**만 담당한다.

### 4-3. 서버에서 수동 반영

배포 서버에서 아래 명령으로 새 이미지를 반영한다.

```bash
cd /home/maxgauge/stop_start/start_stop_automatic
IMAGE_NAME=ghcr.io/sfdg4869/service-integration-control IMAGE_TAG=latest APP_PORT=5051 docker compose pull
IMAGE_NAME=ghcr.io/sfdg4869/service-integration-control IMAGE_TAG=latest APP_PORT=5051 docker compose up -d
docker compose ps
docker compose logs -f
```

이 과정의 의미는 다음과 같다.

- `docker compose pull`
  - GHCR에서 최신 이미지 내려받기
- `docker compose up -d`
  - 새 이미지로 컨테이너 재생성 / 재실행
- `docker compose ps`
  - 정상 실행 여부 확인
- `docker compose logs -f`
  - 실행 로그 확인

---

## 5. docker-compose.dev.yml 과 docker-compose.yml 차이

### `docker-compose.dev.yml`

개발용 파일이다.

- 서버에서 직접 `build`
- 코드 수정 후 빠르게 테스트할 때 사용

### `docker-compose.yml`

배포용 파일이다.

- GHCR 이미지 `pull`
- 운영 반영 시 사용

즉 현재 CI/CD 방향에서는:

- **테스트**: `docker-compose.dev.yml`
- **운영 반영**: `docker-compose.yml`

로 이해하면 된다.

---

## 6. 원래 완전 자동 배포가 안 된 이유

원래는 아래 흐름으로 완전 자동 배포를 하려고 했다.

1. `git push`
2. GitHub Actions가 이미지 빌드
3. GitHub Actions가 서버로 SSH 접속
4. 서버에서 `docker compose pull && up -d`

하지만 현재 서버 환경에서는 이 방식이 막혔다.

### 이유 1. GitHub hosted runner에서 내부망 서버 직접 접근 불가

배포 서버는 사설망 IP(`10.10.x.x`)를 사용하고 있고, GitHub hosted runner는 외부망에서 동작한다.

그래서 GitHub Actions가 서버로 직접 SSH 접속하려고 하면 아래 에러가 발생했다.

```text
dial tcp ***:***: i/o timeout
```

즉:

- secret 문제는 해결되었어도
- GitHub hosted runner -> 내부망 서버 SSH 연결 자체가 안 됨

### 이유 2. GitHub self-hosted runner를 현재 서버에 설치하려 했지만 실패

배포 서버에 self-hosted runner를 설치해보려 했지만, 서버 라이브러리 버전이 낮아 실행되지 않았다.

실제 에러:

```text
GLIBCXX_3.4.20 not found
GLIBCXX_3.4.21 not found
```

즉 현재 서버는 최신 GitHub runner 바이너리를 직접 올리기에는 너무 오래된 환경이다.

---

## 7. 원래 가장 이상적인 완전 자동 방식

가장 좋은 구조는 다음이다.

1. 로컬에서 `push`
2. GitHub Actions가 Docker image build
3. GitHub Actions가 **내부망에서 동작하는 self-hosted runner**에서 deploy 수행
4. 서버 내부에서 `docker compose pull && up -d`
5. 서비스 자동 반영

이 구조의 장점:

- `push`만 하면 자동 반영
- 운영자가 서버에 직접 접속할 필요가 거의 없음
- 배포 서버를 외부에 열 필요 없음

하지만 현재 서버는 self-hosted runner 설치가 불가능하므로, 지금은 이 구조를 바로 쓸 수 없다.

---

## 8. 지금 수정된 GitHub Actions 방향

현재 `.github/workflows/deploy.yml`은 **이미지 발행 전용**으로 정리되었다.

즉 현재 워크플로는:

- `Build And Publish Image`

이고, 더 이상 아래 단계는 하지 않는다.

- 서버 SSH 접속
- 배포 서버에서 `docker compose pull`
- 배포 서버에서 `docker compose up -d`

즉 현재 GitHub Actions 역할은:

- 코드 검증
- Docker image build
- GHCR publish

까지다.

---

## 9. 앞으로 완전 자동으로 가려면

현재 서버가 아닌 **더 최신 내부망 서버** 하나를 확보할 수 있다면, 그 서버에 GitHub self-hosted runner를 설치하는 것이 가장 추천되는 방향이다.

조건:

- 내부망에서 배포 서버 접근 가능
- GitHub self-hosted runner 설치 가능
- Docker 실행 가능

그 경우 다시 다음 구조로 확장할 수 있다.

1. build job: GitHub hosted runner
2. deploy job: self-hosted runner

그러면 `git push`만으로 완전 자동 배포가 가능해진다.

---

## 10. 현재 운영 체크리스트

### 로컬

```bash
git add .
git commit -m "Update application"
git push origin main
```

### GitHub

- `Actions` 탭에서 `Build And Publish Image` 성공 확인

### 서버

```bash
cd /home/maxgauge/stop_start/start_stop_automatic
IMAGE_NAME=ghcr.io/sfdg4869/service-integration-control IMAGE_TAG=latest APP_PORT=5051 docker compose pull
IMAGE_NAME=ghcr.io/sfdg4869/service-integration-control IMAGE_TAG=latest APP_PORT=5051 docker compose up -d
docker compose ps
docker compose logs -f
```

### 웹 확인

```text
http://10.10.47.147:5051
```

---

## 11. 결론

현재 이 프로젝트의 CI/CD는 아래처럼 이해하면 가장 정확하다.

- 로컬에서 코드를 수정하고 push
- GitHub가 Docker 이미지를 만든다
- 이미지는 GHCR에 올라간다
- 서버는 GHCR에서 이미지를 pull 한다
- 서버는 새 이미지로 컨테이너를 다시 띄운다

즉 현재는:

- **CI는 자동**
- **CD는 반자동**

이다.

그리고 지금 방식은 **코드 배포가 아니라 이미지 배포**이기 때문에, 서버 소스 파일이 안 바뀌더라도 컨테이너가 최신 이미지로 바뀌면 실제 서비스는 최신 코드로 동작할 수 있다.
