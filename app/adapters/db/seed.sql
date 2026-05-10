-- Vértice — seed inicial (PostgreSQL)
--
-- ON CONFLICT DO NOTHING substitui o INSERT OR IGNORE do SQLite.

INSERT INTO roles (name) VALUES
    ('root'),
    ('admin'),
    ('analista_n3'),
    ('supervisor'),
    ('finops')
ON CONFLICT (name) DO NOTHING;

INSERT INTO permissions (code) VALUES
    ('execute:agent_analysis'),
    ('manage:prompts'),
    ('manage:modules'),
    ('approve:failsafe'),
    ('view:finops')
ON CONFLICT (code) DO NOTHING;
