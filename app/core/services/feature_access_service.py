"""Use case: matriz "Funcionalidades por Perfil".

A matriz é uma tabela ``(role, department, feature_key) -> access (bool)``
que decide se um conjunto (roles, dept) de usuário enxerga uma feature
controlável. Hoje as features sob controle são ``radar`` e ``raiox`` —
correspondem ao grupo "Funcionalidade" do menu. Outras telas (Configurações,
Administrativo, Monitoramento) seguem os gates fixos de role.

Política de resolução (helper ``can_access``):

  1. **Root bypass**: usuário com ``root`` em ``roles`` → sempre ALLOW.
     Implementação da política "root tem todos os poderes".
  2. **Match mais específico vence**: regra com ``department`` exato
     ganha de regra com ``department=''`` (wildcard).
  3. **Default ALLOW**: sem regra para nenhuma combinação (role, dept,
     feature) → ALLOW. Migrate-safe — instalações existentes não
     perdem acesso no primeiro deploy.
  4. **Multi-role OR**: usuário com vários roles → ALLOW se PELO MENOS
     UM dos roles permite. (Política liberal: "se você é admin E
     analista, herda o melhor do admin".)

O catálogo ``CONTROLLED_FEATURES`` define o conjunto fixo de chaves
controláveis hoje. Mudanças nesse set exigem código (não é dinâmico) —
intencional: cada feature precisa de page guard correspondente, e
adicionar chave sem guard seria silenciosamente ignorado.
"""

from __future__ import annotations

from app.adapters.db.repositories.feature_access_repo import (
    PgFeatureAccessRepository,
)


# Catálogo de features controláveis. Mantenha em sync com:
#   - `pages.py`: cada feature precisa de page guard chamando `can_access`
#   - `nav_left.html`: a entry no menu mapeia para a feature_key via
#     `NAV_ENTRY_TO_FEATURE` abaixo (a entry key continua sendo o key
#     interno de UI, ex.: 'radar', que casa com `active_module` para
#     highlight; já a `feature_key` na matriz é o nome de DOMÍNIO,
#     ex.: 'vozcliente', que aparece em /access pra usuários finais)
#   - tela /access: lista as features como colunas da grid
#
# Histórico: 'radar' (interno) → 'vozcliente' (nome de produto exibido).
# A migration em schema.sql renomeia linhas pré-existentes para que regras
# antigas continuem aplicáveis ao mesmo módulo sob o novo nome.
CONTROLLED_FEATURES = ("vozcliente", "raiox")

# Mapeamento entry-key-no-menu → feature_key-na-matriz. Permite que o
# `active_module` (UI) e o nome técnico das pastas/rotas (`/radar`,
# `app/templates/radar/`) continuem como estão, sem renomear código,
# enquanto a face de domínio exibida ao usuário fica em sync com o
# label "Voz do Cliente".
NAV_ENTRY_TO_FEATURE = {
    "radar": "vozcliente",
    "raiox": "raiox",
}


class FeatureAccessService:
    def __init__(self, repo: PgFeatureAccessRepository | None = None):
        self.repo = repo or PgFeatureAccessRepository()

    async def list_matrix(self) -> list[dict]:
        """Devolve a matriz inteira pra renderizar na tela administrativa."""
        return await self.repo.list_all()

    async def set_rule(
        self,
        role: str,
        department: str,
        feature_key: str,
        access: bool,
        actor_id: str | None = None,
        actor_username: str | None = None,
    ) -> dict:
        """Cria ou atualiza uma regra. Valida que ``feature_key`` está
        no catálogo. Não valida ``role`` (qualquer string é aceita: novos
        roles podem ser adicionados sem mexer no service)."""
        if feature_key not in CONTROLLED_FEATURES:
            raise ValueError(
                f"feature_key inválida: '{feature_key}'. Conhecidas: {', '.join(CONTROLLED_FEATURES)}"
            )
        return await self.repo.upsert(
            role=role,
            department=(department or "").strip(),
            feature_key=feature_key,
            access=access,
            actor_id=actor_id,
            actor_username=actor_username,
        )

    async def remove_rule(
        self, role: str, department: str, feature_key: str
    ) -> bool:
        """Remove regra (volta ao default = allow). True se removeu."""
        return await self.repo.delete(role, (department or "").strip(), feature_key)

    async def can_access(
        self,
        roles: list[str] | set[str],
        department: str,
        feature_key: str,
    ) -> bool:
        """Resolve se o usuário enxerga ``feature_key``.

        Algoritmo:
          1. Se 'root' em roles → True (bypass)
          2. Se feature_key não está em CONTROLLED_FEATURES → True
             (features fora da matriz não são gateadas por esta camada)
          3. Para cada role do user, busca regras (role, *, feature_key):
             - regra (role, dept_exato, feature) → resolve usando ela
             - senão regra (role, '', feature) → resolve usando ela
             - senão sem regra pra esse role
          4. ALLOW se PELO MENOS UM role resolve ALLOW (allow-by-default
             quando nenhum role tem regra explícita).
        """
        if "root" in (roles or []):
            return True
        if feature_key not in CONTROLLED_FEATURES:
            return True

        dept = (department or "").strip()
        # Default allow — só vira deny se algum role resolve para False
        # E nenhum outro role resolve para True (vide loop abaixo).
        any_explicit_allow = False
        all_explicit_deny = None  # None = sem regra nenhuma vista ainda

        for role in (roles or []):
            rules = await self.repo.list_for_role(role)
            # Filtra para a feature em questão
            rules_for_feat = [r for r in rules if r["feature_key"] == feature_key]
            if not rules_for_feat:
                # Esse role não tem regra explícita — herda o default (allow).
                any_explicit_allow = True
                continue

            # Match mais específico vence: tenta dept exato primeiro.
            chosen = None
            for r in rules_for_feat:
                if r["department"] == dept:
                    chosen = r
                    break
            if chosen is None:
                # Fallback: wildcard
                for r in rules_for_feat:
                    if r["department"] == "":
                        chosen = r
                        break

            if chosen is None:
                # Há regras pra esse role+feature, mas nenhuma casa o dept
                # (nem wildcard) — herda o default (allow).
                any_explicit_allow = True
                continue

            if chosen["access"]:
                any_explicit_allow = True
            else:
                # explicit deny — só não barra se outro role permitir
                all_explicit_deny = True if all_explicit_deny is None else all_explicit_deny

        # ALLOW se algum role permite (explícito ou herdou default).
        # DENY só se TODOS os roles tinham regra deny explícita.
        return any_explicit_allow

    async def visible_features(
        self, roles: list[str], department: str
    ) -> set[str]:
        """Conveniência: devolve o set de feature_keys que o user enxerga,
        de todas as CONTROLLED_FEATURES. Usado pelo nav_left para filtrar
        entries do grupo "Funcionalidade"."""
        out: set[str] = set()
        for f in CONTROLLED_FEATURES:
            if await self.can_access(roles, department, f):
                out.add(f)
        return out
