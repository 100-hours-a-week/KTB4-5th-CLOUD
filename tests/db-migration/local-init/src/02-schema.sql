-- KTB4-5th-BE origin/dev 11c624e8a220e727b411002074dbed047e8d0de2 의 src/main/resources/db/schema.sql 사본 (로컬 리허설 전용)
CREATE DATABASE dameokja DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_as_cs;
USE dameokja;
-- Source: https://www.erdcloud.com/d/y767tdHDsaZ8MA4NQ (2026-09-17)
-- MySQL 8.0.16+ / InnoDB / innodb_page_size=16384 / strict SQL mode.
-- Execute against a NEW, explicitly selected database. No DROP / USE / automatic startup execution.
-- in_memory_recommendation_cache is an application-memory model, not a MySQL table.
-- Design choices and index rationale: docs/database/README.md.
SET NAMES utf8mb4;

CREATE TABLE users (
    user_id BIGINT NOT NULL AUTO_INCREMENT COMMENT '회원 내부 대리키',
    nickname VARCHAR(10) NOT NULL COMMENT '2~10자. 탈퇴 시 치환값도 중복 불가',
    profile_image_key VARCHAR(1024) NOT NULL COMMENT '프로필 이미지 URL. 미등록 시 기본 이미지 URL 저장',
    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE' COMMENT 'ACTIVE / WITHDRAWN',
    role VARCHAR(20) NOT NULL DEFAULT 'USER' COMMENT 'USER / ADMIN',
    cooking_count INT NOT NULL DEFAULT 0 COMMENT '재고 차감을 완료한 누적 횟수',
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    deleted_at DATETIME(6) NULL COMMENT '탈퇴 일시. 활성 회원은 NULL',
    login_id VARCHAR(10) NULL COMMENT '완성형 한글 영문 숫자 2~10자. OAuth-only 및 탈퇴 회원은 NULL',
    password_hash VARCHAR(60) NULL COMMENT 'bcrypt. login_id와 동시 존재. 일반 응답 제외',
    password_changed_at DATETIME(6) NULL COMMENT '이 시각보다 먼저 발급된 토큰 무효화. OAuth-only는 NULL',
    PRIMARY KEY (user_id),
    UNIQUE KEY uk_users_nickname (nickname),
    UNIQUE KEY uk_users_login_id (login_id),
    CONSTRAINT ck_users_nickname CHECK (CHAR_LENGTH(nickname) BETWEEN 2 AND 10),
    CONSTRAINT ck_users_status CHECK (status IN ('ACTIVE','WITHDRAWN')),
    CONSTRAINT ck_users_role CHECK (role IN ('USER','ADMIN')),
    CONSTRAINT ck_users_cooking_count CHECK (cooking_count >= 0),
    CONSTRAINT ck_users_login_format CHECK (login_id IS NULL OR REGEXP_LIKE(login_id, '^[가-힣A-Za-z0-9]{2,10}$', 'c')),
    CONSTRAINT ck_users_credentials CHECK (
        (login_id IS NULL AND password_hash IS NULL AND password_changed_at IS NULL)
        OR (login_id IS NOT NULL AND password_hash IS NOT NULL)
    ),
    CONSTRAINT ck_users_withdrawal CHECK (
        (status = 'ACTIVE' AND deleted_at IS NULL)
        OR (status = 'WITHDRAWN' AND deleted_at IS NOT NULL AND login_id IS NULL AND password_hash IS NULL)
    )
) ENGINE=InnoDB ROW_FORMAT=DYNAMIC DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_as_cs;

CREATE TABLE refrigerators (
    refrigerator_id BIGINT NOT NULL AUTO_INCREMENT,
    name VARCHAR(20) NOT NULL COMMENT '생성 시 소유자 닉네임으로 초기화. 이후 독립 관리',
    capacity SMALLINT NOT NULL DEFAULT 100 COMMENT '냉장고당 재고 행 등록 상한. 서비스에서 동시성 제어',
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    deleted_at DATETIME(6) NULL COMMENT '마지막 구성원 탈퇴 시 soft delete. 접근, 가입, 재고 변경, 추천, 푸시 차단. 보관 종료 및 FK 정리 후 hard delete',
    expired_count INT NOT NULL DEFAULT 0 COMMENT '이번 달 만료 재고 행 누적. 재고 삭제 후에도 유지',
    expired_count_month CHAR(7) NOT NULL COMMENT 'YYYY-MM. 생성 시 현재 월. 월 변경 시 카운트와 원자 갱신. 과거 월 미보관',
    PRIMARY KEY (refrigerator_id),
    CONSTRAINT ck_refrigerators_capacity CHECK (capacity > 0),
    CONSTRAINT ck_refrigerators_expired_count CHECK (expired_count >= 0),
    CONSTRAINT ck_refrigerators_month CHECK (REGEXP_LIKE(expired_count_month, '^[0-9]{4}-(0[1-9]|1[0-2])$', 'c'))
) ENGINE=InnoDB ROW_FORMAT=DYNAMIC DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_as_cs;

CREATE TABLE refrigerator_members (
    refrigerator_member_id BIGINT NOT NULL AUTO_INCREMENT,
    refrigerator_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    is_active TINYINT(1) NOT NULL DEFAULT 0 COMMENT '현재 사용 중인 냉장고. 전환 시 개인 관계 보존',
    active_marker BIGINT GENERATED ALWAYS AS (IF(is_active=1,user_id,NULL)) STORED COMMENT '회원당 활성 냉장고 최대 1개. 가입 자격과 무관',
    role VARCHAR(20) NOT NULL DEFAULT 'MEMBER' COMMENT 'OWNER / MEMBER. 냉장고에 소유자 FK 중복 저장하지 않음',
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (refrigerator_member_id),
    UNIQUE KEY uk_members_refrigerator_user (refrigerator_id, user_id),
    UNIQUE KEY uk_members_active_marker (active_marker),
    KEY ix_members_user (user_id),
    CONSTRAINT ck_members_active CHECK (is_active IN (0,1)),
    CONSTRAINT ck_members_role CHECK (role IN ('OWNER','MEMBER')),
    CONSTRAINT fk_members_refrigerator FOREIGN KEY (refrigerator_id) REFERENCES refrigerators (refrigerator_id),
    CONSTRAINT fk_members_user FOREIGN KEY (user_id) REFERENCES users (user_id)
) ENGINE=InnoDB ROW_FORMAT=DYNAMIC DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_as_cs;

CREATE TABLE social_accounts (
    social_account_id BIGINT NOT NULL AUTO_INCREMENT,
    user_id BIGINT NOT NULL,
    provider VARCHAR(20) NOT NULL COMMENT 'KAKAO / GOOGLE',
    provider_user_id VARCHAR(255) NOT NULL COMMENT 'OAuth 제공자 사용자 식별자',
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    provider_email VARCHAR(255) NULL COMMENT '이메일 스냅샷. 로그인 식별자 아님. UNIQUE 미적용. 성공 로그인 시 최신 값 갱신',
    PRIMARY KEY (social_account_id),
    UNIQUE KEY uk_social_user_provider (user_id, provider),
    UNIQUE KEY uk_social_provider_identity (provider, provider_user_id),
    CONSTRAINT ck_social_provider CHECK (provider IN ('KAKAO','GOOGLE')),
    CONSTRAINT fk_social_user FOREIGN KEY (user_id) REFERENCES users (user_id)
) ENGINE=InnoDB ROW_FORMAT=DYNAMIC DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_as_cs;

CREATE TABLE invite_codes (
    invite_code_id BIGINT NOT NULL AUTO_INCREMENT,
    code CHAR(6) CHARACTER SET ascii COLLATE ascii_bin NOT NULL COMMENT '영문 대문자와 숫자 6자리',
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    refrigerator_id BIGINT NOT NULL COMMENT '재발급 가능. 냉장고 ID에는 UNIQUE 미적용',
    PRIMARY KEY (invite_code_id),
    UNIQUE KEY uk_invites_code (code),
    KEY ix_invites_refrigerator_created (refrigerator_id, created_at DESC),
    CONSTRAINT ck_invites_code CHECK (REGEXP_LIKE(code, '^[A-Z0-9]{6}$', 'c')),
    CONSTRAINT fk_invites_refrigerator FOREIGN KEY (refrigerator_id) REFERENCES refrigerators (refrigerator_id)
) ENGINE=InnoDB ROW_FORMAT=DYNAMIC DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_as_cs;

CREATE TABLE ingredients (
    ingredient_id BIGINT NOT NULL AUTO_INCREMENT,
    refrigerator_id BIGINT NOT NULL,
    name VARCHAR(100) NOT NULL COMMENT '사용자 입력 또는 OCR 원문',
    category VARCHAR(20) NOT NULL,
    storage_type VARCHAR(20) NOT NULL DEFAULT 'REFRIGERATED' COMMENT 'REFRIGERATED / FROZEN',
    quantity SMALLINT NULL COMMENT 'COUNT일 때 1~100. WEIGHT일 때 NULL',
    weight_value DECIMAL(10,3) NULL COMMENT 'WEIGHT일 때 G/ML 기준 총량, 50000 이하. COUNT일 때 NULL',
    weight_unit VARCHAR(10) NOT NULL DEFAULT 'NONE' COMMENT 'COUNT=NONE, WEIGHT=G 또는 ML. kg/L은 저장 전 환산',
    measure_type VARCHAR(10) NOT NULL DEFAULT 'COUNT' COMMENT 'COUNT / WEIGHT',
    expiration_date DATE NOT NULL COMMENT '해당일 23:59:59까지 유효',
    ingredient_image_key VARCHAR(512) NULL COMMENT 'S3 실물 사진 객체 키',
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    registration_source VARCHAR(20) NOT NULL COMMENT 'DIRECT / RECEIPT / FOOD_IMAGE. 합산 및 수정 시 최초 값 유지',
    PRIMARY KEY (ingredient_id),
    KEY ix_ingredients_refrigerator_expiration (refrigerator_id, expiration_date, ingredient_id),
    KEY ix_ingredients_expiration (expiration_date, refrigerator_id),
    CONSTRAINT ck_ingredients_category CHECK (category IN ('VEGETABLE','FRUIT','MEAT','SEAFOOD','DAIRY','TOFU_BEAN','GRAINS_NOODLE','PROCESSED_FOOD','SEASONING','BEVERAGE','OTHER')),
    CONSTRAINT ck_ingredients_storage CHECK (storage_type IN ('REFRIGERATED','FROZEN')),
    CONSTRAINT ck_ingredients_source CHECK (registration_source IN ('DIRECT','RECEIPT','FOOD_IMAGE')),
    CONSTRAINT ck_ingredients_measure CHECK (
        (measure_type = 'COUNT' AND quantity IS NOT NULL AND quantity BETWEEN 1 AND 100 AND weight_value IS NULL AND weight_unit = 'NONE')
        OR (measure_type = 'WEIGHT' AND quantity IS NULL AND weight_value IS NOT NULL AND weight_value > 0 AND weight_value <= 50000 AND weight_unit IN ('G','ML'))
    ),
    CONSTRAINT fk_ingredients_refrigerator FOREIGN KEY (refrigerator_id) REFERENCES refrigerators (refrigerator_id)
) ENGINE=InnoDB ROW_FORMAT=DYNAMIC DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_as_cs;

CREATE TABLE recipes (
    recipe_id BIGINT NOT NULL AUTO_INCREMENT COMMENT '백엔드 레시피 사본 ID',
    source VARCHAR(32) NOT NULL,
    source_recipe_id VARCHAR(191) NOT NULL COMMENT '원천 안정 식별자. 대소문자 구분',
    source_revision VARCHAR(128) NOT NULL COMMENT '버전 토큰. 문자열 대소 비교로 최신 순서 판단 금지',
    name VARCHAR(255) NOT NULL,
    image_url VARCHAR(2048) NULL,
    base_servings DECIMAL(10,3) NULL COMMENT '기준 인분. NULL 또는 양수',
    status VARCHAR(16) NOT NULL COMMENT 'ACTIVE / INACTIVE. 원천 삭제는 INACTIVE로 반영',
    synced_at DATETIME(6) NOT NULL COMMENT '마지막 성공 적재 시각. 부모, 재료, 단계는 원자적 반영',
    PRIMARY KEY (recipe_id),
    UNIQUE KEY uk_recipes_source_identity (source, source_recipe_id),
    CONSTRAINT ck_recipes_servings CHECK (base_servings IS NULL OR base_servings > 0),
    CONSTRAINT ck_recipes_status CHECK (status IN ('ACTIVE','INACTIVE'))
) ENGINE=InnoDB ROW_FORMAT=DYNAMIC DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_as_cs;

CREATE TABLE recipe_ingredients (
    recipe_ingredient_id BIGINT NOT NULL AUTO_INCREMENT,
    name VARCHAR(255) NOT NULL COMMENT '원천 재료명',
    amount_text VARCHAR(255) NULL COMMENT '소금 약간 등 원천 필요량 표현',
    measure_type VARCHAR(16) NOT NULL COMMENT 'COUNT / WEIGHT / VOLUME / UNKNOWN',
    amount_value DECIMAL(10,3) NULL COMMENT '확인된 필요량. UNKNOWN=NULL. COUNT는 양수 정수',
    unit VARCHAR(8) NULL COMMENT 'COUNT=EA, WEIGHT=G, VOLUME=ML, UNKNOWN=NULL',
    ingredient_no INT NOT NULL COMMENT '양수 순번. 재료명 중복 여부와 별개',
    recipe_id BIGINT NOT NULL COMMENT '사용자 재고 ingredients와 직접 FK 없음',
    PRIMARY KEY (recipe_ingredient_id),
    UNIQUE KEY uk_recipe_ingredients_order (recipe_id, ingredient_no),
    CONSTRAINT ck_recipe_ingredients_order CHECK (ingredient_no > 0),
    CONSTRAINT ck_recipe_ingredients_measure CHECK (
        (measure_type = 'UNKNOWN' AND amount_value IS NULL AND unit IS NULL)
        OR (measure_type = 'COUNT' AND amount_value IS NOT NULL AND amount_value > 0 AND amount_value = FLOOR(amount_value) AND unit IS NOT NULL AND unit = 'EA')
        OR (measure_type = 'WEIGHT' AND amount_value IS NOT NULL AND amount_value > 0 AND unit IS NOT NULL AND unit = 'G')
        OR (measure_type = 'VOLUME' AND amount_value IS NOT NULL AND amount_value > 0 AND unit IS NOT NULL AND unit = 'ML')
    ),
    CONSTRAINT fk_recipe_ingredients_recipe FOREIGN KEY (recipe_id) REFERENCES recipes (recipe_id) ON DELETE CASCADE
) ENGINE=InnoDB ROW_FORMAT=DYNAMIC DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_as_cs;

CREATE TABLE recipe_steps (
    recipe_step_id BIGINT NOT NULL AUTO_INCREMENT,
    instruction TEXT NOT NULL,
    image_url VARCHAR(2048) NULL,
    step_no INT NOT NULL COMMENT '양수 단계 순번. 조회 시 ORDER BY step_no',
    recipe_id BIGINT NOT NULL,
    PRIMARY KEY (recipe_step_id),
    UNIQUE KEY uk_recipe_steps_order (recipe_id, step_no),
    CONSTRAINT ck_recipe_steps_order CHECK (step_no > 0),
    CONSTRAINT fk_recipe_steps_recipe FOREIGN KEY (recipe_id) REFERENCES recipes (recipe_id) ON DELETE CASCADE
) ENGINE=InnoDB ROW_FORMAT=DYNAMIC DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_as_cs;

CREATE TABLE cooking_records (
    cooking_record_id BIGINT NOT NULL AUTO_INCREMENT,
    recommendation_id VARCHAR(64) NOT NULL COMMENT '캐시 추천 결과 식별자. 레시피 FK 아님',
    recipe_name_snapshot VARCHAR(150) NOT NULL COMMENT '적용 당시 표시된 요리명',
    idempotency_key VARCHAR(64) NOT NULL COMMENT '동일 적용 요청의 중복 차감 방지',
    cooked_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    user_id BIGINT NOT NULL,
    refrigerator_id BIGINT NOT NULL,
    PRIMARY KEY (cooking_record_id),
    UNIQUE KEY uk_cooking_idempotency (idempotency_key),
    KEY ix_cooking_user_time (user_id, cooked_at DESC, cooking_record_id DESC),
    KEY ix_cooking_refrigerator (refrigerator_id),
    CONSTRAINT fk_cooking_user FOREIGN KEY (user_id) REFERENCES users (user_id),
    CONSTRAINT fk_cooking_refrigerator FOREIGN KEY (refrigerator_id) REFERENCES refrigerators (refrigerator_id)
) ENGINE=InnoDB ROW_FORMAT=DYNAMIC DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_as_cs;

CREATE TABLE notifications (
    notification_id BIGINT NOT NULL AUTO_INCREMENT,
    type VARCHAR(20) NOT NULL COMMENT 'EXPIRED / EXPIRING / MEMBER_JOINED / MEMBER_KICKED. 추천 푸시는 저장하지 않음',
    title VARCHAR(150) NOT NULL COMMENT '발송 시점 제목',
    body VARCHAR(500) NOT NULL COMMENT '발송 시점 본문. 대상 재료명 및 수량 포함',
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '정렬: created_at DESC, notification_id DESC',
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    refrigerator_id BIGINT NOT NULL COMMENT '알림 발생 냉장고. 활성 냉장고별 조회',
    PRIMARY KEY (notification_id),
    KEY ix_notifications_refrigerator_time (refrigerator_id, created_at DESC, notification_id DESC),
    CONSTRAINT ck_notifications_type CHECK (type IN ('EXPIRED','EXPIRING','MEMBER_JOINED','MEMBER_KICKED')),
    CONSTRAINT fk_notifications_refrigerator FOREIGN KEY (refrigerator_id) REFERENCES refrigerators (refrigerator_id)
) ENGINE=InnoDB ROW_FORMAT=DYNAMIC DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_as_cs;

CREATE TABLE notification_recipients (
    notification_recipient_id BIGINT NOT NULL AUTO_INCREMENT,
    read_at DATETIME(6) NULL COMMENT '개인별 읽음. NULL이면 미확인',
    user_id BIGINT NOT NULL,
    notification_id BIGINT NOT NULL,
    PRIMARY KEY (notification_recipient_id),
    UNIQUE KEY uk_recipients_user_notification (user_id, notification_id),
    KEY ix_recipients_notification (notification_id),
    CONSTRAINT fk_recipients_user FOREIGN KEY (user_id) REFERENCES users (user_id),
    CONSTRAINT fk_recipients_notification FOREIGN KEY (notification_id) REFERENCES notifications (notification_id)
) ENGINE=InnoDB ROW_FORMAT=DYNAMIC DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_as_cs;

CREATE TABLE notification_preferences (
    preference_id BIGINT NOT NULL AUTO_INCREMENT,
    user_id BIGINT NOT NULL,
    type VARCHAR(20) NOT NULL COMMENT 'EXPIRATION / RECIPE / ANALYSIS',
    is_enabled TINYINT(1) NOT NULL DEFAULT TRUE COMMENT '수신 설정 변경은 UPDATE. 행 삭제하지 않음',
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (preference_id),
    UNIQUE KEY uk_preferences_user_type (user_id, type),
    CONSTRAINT ck_preferences_type CHECK (type IN ('EXPIRATION','RECIPE','ANALYSIS')),
    CONSTRAINT ck_preferences_enabled CHECK (is_enabled IN (0,1)),
    CONSTRAINT fk_preferences_user FOREIGN KEY (user_id) REFERENCES users (user_id)
) ENGINE=InnoDB ROW_FORMAT=DYNAMIC DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_as_cs;

CREATE TABLE user_devices (
    user_device_id BIGINT NOT NULL AUTO_INCREMENT COMMENT '브라우저 푸시 구독. 물리 기기 전체 정보 아님',
    endpoint VARCHAR(2048) CHARACTER SET ascii COLLATE ascii_bin NOT NULL COMMENT 'ASCII 직렬화된 푸시 URL. 전체 길이 UNIQUE, 대소문자 구분. 16KB InnoDB DYNAMIC 전제',
    p256dh_key VARCHAR(128) NOT NULL COMMENT '구독 공개키 Base64URL',
    auth_secret_encrypted BLOB NOT NULL COMMENT 'auth 암호문, nonce, 인증태그. 암호화 키는 외부 보관',
    encryption_key_version VARCHAR(64) NOT NULL COMMENT '저장 암호화 키 버전',
    vapid_key_version VARCHAR(64) NOT NULL COMMENT '구독 생성에 사용한 서버 VAPID 공개키 버전',
    subscription_version BIGINT NOT NULL DEFAULT 1 COMMENT '소유자, 키, 활성화 변경 시 증가',
    status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE' COMMENT 'ACTIVE / DISABLED / INVALID. 로그아웃은 해당 구독 비활성',
    created_at DATETIME(6) NOT NULL,
    updated_at DATETIME(6) NOT NULL,
    user_id BIGINT NOT NULL COMMENT '현재 구독 소유 회원. NOT UNIQUE',
    PRIMARY KEY (user_device_id),
    UNIQUE KEY uk_devices_endpoint (endpoint),
    KEY ix_devices_user (user_id),
    CONSTRAINT ck_devices_version CHECK (subscription_version > 0),
    CONSTRAINT ck_devices_status CHECK (status IN ('ACTIVE','DISABLED','INVALID')),
    CONSTRAINT fk_devices_user FOREIGN KEY (user_id) REFERENCES users (user_id)
) ENGINE=InnoDB ROW_FORMAT=DYNAMIC DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_as_cs;

CREATE TABLE push_notifications (
    push_notification_id BIGINT NOT NULL AUTO_INCREMENT COMMENT '구독별 논리 작업. 재시도는 같은 행 사용',
    dispatch_key VARCHAR(191) NOT NULL COMMENT '논리 이벤트 키',
    kind VARCHAR(20) NOT NULL COMMENT 'INBOX / RECOMMENDATION / ANALYSIS',
    user_device_id BIGINT NOT NULL,
    subscription_version BIGINT NOT NULL COMMENT '생성 당시 구독 버전',
    payload JSON NOT NULL COMMENT '문구 및 내부 경로 스냅샷. 재시도로 추천 재생성하지 않음',
    status VARCHAR(16) NOT NULL DEFAULT 'PENDING',
    attempt_count INT NOT NULL DEFAULT 0,
    next_attempt_at DATETIME(6) NULL COMMENT 'PENDING / RETRY는 필수',
    lease_until DATETIME(6) NULL COMMENT 'PROCESSING은 필수. 작업 점유 만료',
    lease_token VARCHAR(64) NULL COMMENT 'PROCESSING은 필수. 늦은 worker 결과 갱신 차단',
    expires_at DATETIME(6) NOT NULL COMMENT '경과 시 재시도 금지',
    accepted_at DATETIME(6) NULL COMMENT 'ACCEPTED만 필수, 나머지는 NULL. 푸시 서비스 수락이며 읽음 아님',
    last_error_code VARCHAR(64) NULL COMMENT '오류 분류. URL 또는 비밀키 원문 기록 금지',
    created_at DATETIME(6) NOT NULL,
    updated_at DATETIME(6) NOT NULL,
    notification_id BIGINT NULL COMMENT 'INBOX는 필수, RECOMMENDATION / ANALYSIS는 NULL',
    user_id BIGINT NOT NULL COMMENT '생성 당시 수신자. 발송 전 현재 구독 소유자 및 접근권한 재검증',
    refrigerator_id BIGINT NULL COMMENT 'ANALYSIS는 NULL, 나머지는 생성 당시 냉장고',
    PRIMARY KEY (push_notification_id),
    UNIQUE KEY uk_push_dispatch_subscription (dispatch_key, user_device_id, subscription_version),
    KEY ix_push_device (user_device_id),
    KEY ix_push_notification (notification_id),
    KEY ix_push_user (user_id),
    KEY ix_push_refrigerator (refrigerator_id),
    CONSTRAINT ck_push_kind CHECK (kind IN ('INBOX','RECOMMENDATION','ANALYSIS')),
    CONSTRAINT ck_push_status CHECK (status IN ('PENDING','PROCESSING','RETRY','ACCEPTED','FAILED','CANCELLED')),
    CONSTRAINT ck_push_version CHECK (subscription_version > 0),
    CONSTRAINT ck_push_attempts CHECK (attempt_count >= 0),
    CONSTRAINT ck_push_schedule CHECK (status NOT IN ('PENDING','RETRY') OR next_attempt_at IS NOT NULL),
    CONSTRAINT ck_push_claim CHECK (status <> 'PROCESSING' OR (lease_until IS NOT NULL AND lease_token IS NOT NULL)),
    CONSTRAINT ck_push_accepted CHECK ((status = 'ACCEPTED' AND accepted_at IS NOT NULL) OR (status <> 'ACCEPTED' AND accepted_at IS NULL)),
    CONSTRAINT ck_push_notification CHECK ((kind = 'INBOX' AND notification_id IS NOT NULL) OR (kind IN ('RECOMMENDATION','ANALYSIS') AND notification_id IS NULL)),
    CONSTRAINT ck_push_refrigerator CHECK ((kind = 'ANALYSIS' AND refrigerator_id IS NULL) OR (kind <> 'ANALYSIS' AND refrigerator_id IS NOT NULL)),
    CONSTRAINT fk_push_device FOREIGN KEY (user_device_id) REFERENCES user_devices (user_device_id),
    CONSTRAINT fk_push_notification FOREIGN KEY (notification_id) REFERENCES notifications (notification_id),
    CONSTRAINT fk_push_user FOREIGN KEY (user_id) REFERENCES users (user_id),
    CONSTRAINT fk_push_refrigerator FOREIGN KEY (refrigerator_id) REFERENCES refrigerators (refrigerator_id)
) ENGINE=InnoDB ROW_FORMAT=DYNAMIC DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_as_cs;
