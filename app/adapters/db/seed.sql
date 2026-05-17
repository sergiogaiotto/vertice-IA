-- Vértice — seed inicial (PostgreSQL)
--
-- ON CONFLICT DO NOTHING substitui o INSERT OR IGNORE do SQLite.

-- Roles base. Hierarquia conceitual:
--   root        — supremo; única role que cria/destitui root; bypassa todos os gates
--   admin       — gerencia tudo exceto roles 'root'; matriz "Funcionalidades por
--                 Perfil" em modo read-only
--   supervisor  — gerencia só analistas_n* do próprio departamento; vê /users limitado
--   analista_n3 — sênior / casos complexos (convenção; sem distinção funcional por nível)
--   analista_n2 — especialista (convenção; sem distinção funcional por nível)
--   analista_n1 — front-line / atendimento básico (convenção; sem distinção funcional)
--   finops      — governança financeira (FinOps + Failsafe + Auditoria); NÃO recebe
--                 shares de "público analista" (ver radar_card_visibility_repo)
-- O sistema trata n1/n2/n3 IGUALMENTE em todos os gates (`role.startswith("analista_")`).
-- A distinção por nível existe pra mapear a hierarquia de call center / suporte —
-- pode ser usada via matriz "Funcionalidades por Perfil" para criar regras por nível.
INSERT INTO roles (name) VALUES
    ('root'),
    ('admin'),
    ('supervisor'),
    ('analista_n3'),
    ('analista_n2'),
    ('analista_n1'),
    ('finops')
ON CONFLICT (name) DO NOTHING;

INSERT INTO permissions (code) VALUES
    ('execute:agent_analysis'),
    ('manage:prompts'),
    ('manage:modules'),
    ('approve:failsafe'),
    ('view:finops')
ON CONFLICT (code) DO NOTHING;
