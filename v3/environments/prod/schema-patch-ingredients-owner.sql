-- prod 전용 보완. BE 이관 DDL(20261109-main-schema-changes.sql)의 기준(main 524b209)에는 이미 있으나 v2 prod DB에는 없던 변경입니다.
-- ingredients.owner_id를 NOT NULL로 바꾸고 인덱스·FK 이름을 db/schema.sql과 맞춥니다.
-- 2026-11-09 v2 prod 확인: owner_id NULL 0건, users에 없는 owner_id 0건.
SET NAMES utf8mb4 COLLATE utf8mb4_0900_as_cs;
ALTER TABLE ingredients DROP FOREIGN KEY fk_ingredients_owner;
ALTER TABLE ingredients
    MODIFY COLUMN owner_id BIGINT NOT NULL COMMENT '최초 등록 회원. 도입 전 재고는 당시 냉장고 소유자로 보정',
    RENAME INDEX ix_ingredients_owner TO ix_ingredients_registered_by_user,
    ADD CONSTRAINT fk_ingredients_registered_by_user FOREIGN KEY (owner_id) REFERENCES users (user_id) ON DELETE RESTRICT ON UPDATE RESTRICT;
