CREATE TABLE knowledge_backend.accounts (
    id uuid PRIMARY KEY,
    email varchar(320) NOT NULL UNIQUE,
    password_hash varchar(1024) NOT NULL,
    is_active boolean NOT NULL DEFAULT true,
    is_admin boolean NOT NULL DEFAULT false,
    is_verified boolean NOT NULL DEFAULT false,
    department varchar(255),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE knowledge_backend.account_sessions (
    id uuid PRIMARY KEY,
    account_id uuid NOT NULL REFERENCES knowledge_backend.accounts(id) ON DELETE CASCADE,
    token_hash char(64) NOT NULL UNIQUE,
    expires_at timestamptz NOT NULL
);
CREATE INDEX account_sessions_by_account ON knowledge_backend.account_sessions (account_id);
CREATE INDEX account_sessions_by_expiry ON knowledge_backend.account_sessions (expires_at);

CREATE TABLE knowledge_backend.workspace_members (
    workspace_id varchar(64) NOT NULL REFERENCES knowledge_backend.workspaces(id) ON DELETE CASCADE,
    account_id uuid NOT NULL REFERENCES knowledge_backend.accounts(id) ON DELETE CASCADE,
    role varchar(8) NOT NULL CHECK (role IN ('READER', 'EDITOR')),
    PRIMARY KEY (workspace_id, account_id)
);
CREATE INDEX workspace_members_by_account ON knowledge_backend.workspace_members (account_id, workspace_id);
