-- Vértice — seed inicial

-- Roles e permissões base
INSERT OR IGNORE INTO roles (name) VALUES ('root'), ('admin'), ('analista_n3'), ('supervisor'), ('finops');

INSERT OR IGNORE INTO permissions (code) VALUES
    ('execute:agent_analysis'),
    ('manage:prompts'),
    ('manage:modules'),
    ('approve:failsafe'),
    ('view:finops');
