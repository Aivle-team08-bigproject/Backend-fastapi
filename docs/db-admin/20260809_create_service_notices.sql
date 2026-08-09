BEGIN;

CREATE TABLE service.notices (
    id BIGSERIAL PRIMARY KEY,
    title VARCHAR(200) NOT NULL,
    content TEXT NOT NULL,
    status VARCHAR(20) NOT NULL
        CONSTRAINT ck_notices_status
        CHECK (status IN ('DRAFT', 'PUBLISHED', 'ARCHIVED')),
    created_by_employee_id BIGINT NOT NULL
        REFERENCES service.employees(id),
    updated_by_employee_id BIGINT NOT NULL
        REFERENCES service.employees(id),
    published_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

ALTER TABLE service.notices OWNER TO hanacard_admin;
ALTER SEQUENCE service.notices_id_seq OWNER TO hanacard_admin;

CREATE INDEX ix_notices_created_by_employee_id
    ON service.notices (created_by_employee_id);

CREATE INDEX ix_notices_updated_by_employee_id
    ON service.notices (updated_by_employee_id);

CREATE INDEX ix_notices_status_published_at_id
    ON service.notices (status, published_at, id);

GRANT SELECT, INSERT, UPDATE, DELETE ON service.notices TO app_svc;
GRANT USAGE, SELECT ON SEQUENCE service.notices_id_seq TO app_svc;

COMMIT;
