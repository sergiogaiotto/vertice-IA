"""Use case: Cockpit pessoal — atividade do usuário logado.

Lê audit_events para construir KPIs, heatmap (7 dias × 24h), timeline e
top módulos do próprio usuário. Substitui a antiga visão de custos do
cockpit por uma visão "minha produtividade".

Filtra eventos relevantes:
  - category = 'module_run' (execução de módulo configurável via Radar)
  - category = 'http' AND action in ('POST','PATCH','DELETE') AND feature
    em FEATURES_OF_INTEREST (ações que efetivamente geram/modificam análises)
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Any

from app.adapters.db.sqlite import connect


# Features cuja atividade conta como "análise gerada" pelo usuário
FEATURES_OF_INTEREST = {"radar", "churn", "raiox", "modules", "presentations"}

# Targets que NÃO são análises (só leitura ou navegação)
_EXCLUDED_TARGET_PARTS = ("/health", "/api/audit", "/static/", "/login")


def _is_analysis_event(category: str, action: str, target: str, feature: str | None) -> bool:
    """Heurística: o evento representa uma análise gerada/modificada?"""
    if not feature or feature not in FEATURES_OF_INTEREST:
        return False
    if any(p in (target or "") for p in _EXCLUDED_TARGET_PARTS):
        return False
    if category == "module_run":
        return True
    if category == "http" and (action or "").upper() in {"POST", "PATCH", "PUT"}:
        # ignora endpoints de leitura puro (search/list)
        if "/search" in (target or "") or target.endswith("/cases"):
            return False
        return True
    return False


def _humanize_relative(dt: datetime, ref: datetime | None = None) -> str:
    ref = ref or datetime.utcnow()
    delta = ref - dt
    secs = int(delta.total_seconds())
    if secs < 60:
        return "agora há pouco"
    if secs < 3600:
        return f"há {secs // 60} min"
    if secs < 86400:
        return f"há {secs // 3600} h"
    days = secs // 86400
    if days == 1:
        return "ontem"
    if days < 30:
        return f"há {days} dias"
    months = days // 30
    return f"há {months} meses"


def _label_for_event(category: str, action: str, target: str, feature: str | None) -> str:
    """Devolve um texto amigável para o stream de atividade."""
    feat = (feature or "outros").upper()
    if category == "module_run":
        return f"Executou módulo · {feat}"
    if "/run-module" in (target or ""):
        return f"Análise no Radar · {feat}"
    if "/charts" in (target or "") and "PATCH" in (action or "").upper():
        return f"Editou chart · Raio X"
    if "/charts" in (target or ""):
        return f"Adicionou chart · Raio X"
    if "/boards" in (target or "") and "PATCH" in (action or "").upper():
        return f"Editou dashboard · Raio X"
    if "/boards" in (target or ""):
        return f"Criou dashboard · Raio X"
    if "/classify" in (target or "") or feature == "churn":
        return f"Classificou churn"
    if feature == "presentations":
        return f"Trabalhou em apresentação"
    if feature == "modules":
        return f"Configurou módulo"
    return f"{(action or '').upper()} {feat}"


class CockpitService:

    async def user_activity(
        self,
        user_id: str,
        days: int = 30,
    ) -> dict[str, Any]:
        """Coleta toda a atividade do usuário nos últimos N dias para o cockpit."""
        cutoff = datetime.utcnow() - timedelta(days=days)
        async with connect() as db:
            cur = await db.execute(
                "SELECT ts, category, action, target, feature, status_code "
                "FROM audit_events "
                "WHERE user_id = ? AND ts >= ? "
                "ORDER BY ts DESC LIMIT 5000",
                (user_id, cutoff.isoformat()),
            )
            rows = await cur.fetchall()

        events: list[dict[str, Any]] = []
        for r in rows:
            ts_str = r[0]
            try:
                ts = datetime.fromisoformat(ts_str) if isinstance(ts_str, str) else ts_str
            except ValueError:
                continue
            category = r[1] or ""
            action = r[2] or ""
            target = r[3] or ""
            feature = r[4]
            status = r[5]
            if status is not None and status >= 400:
                continue  # só conta sucessos
            if not _is_analysis_event(category, action, target, feature):
                continue
            events.append({
                "ts": ts,
                "category": category,
                "action": action,
                "target": target,
                "feature": feature,
                "label": _label_for_event(category, action, target, feature),
                "relative": _humanize_relative(ts),
            })

        # ----- KPIs -----
        now = datetime.utcnow()
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        last_7d = now - timedelta(days=7)
        last_30d = now - timedelta(days=30)
        kpi_today = sum(1 for e in events if e["ts"] >= midnight)
        kpi_7d = sum(1 for e in events if e["ts"] >= last_7d)
        kpi_30d = sum(1 for e in events if e["ts"] >= last_30d)

        # ----- Top módulos/features -----
        feat_counts = Counter(e["feature"] for e in events if e["feature"])
        top_features = [
            {"feature": f, "count": c}
            for f, c in feat_counts.most_common(5)
        ]
        favorite = top_features[0]["feature"] if top_features else None

        # ----- Streak (dias consecutivos com >=1 análise, partindo de hoje) -----
        days_with_activity = {e["ts"].date() for e in events}
        streak = 0
        cursor = now.date()
        while cursor in days_with_activity:
            streak += 1
            cursor = cursor - timedelta(days=1)

        # ----- Heatmap (7 dias × 24h) -----
        # Linhas = dias da semana mais recentes (D-6 .. hoje)
        heatmap: dict[str, list[int]] = {}
        labels_days: list[str] = []
        for delta in range(6, -1, -1):
            day = (now - timedelta(days=delta)).date()
            labels_days.append(day.isoformat())
            heatmap[day.isoformat()] = [0] * 24
        for e in events:
            day_key = e["ts"].date().isoformat()
            if day_key in heatmap:
                heatmap[day_key][e["ts"].hour] += 1
        heatmap_max = max((max(row) for row in heatmap.values()), default=0)

        # ----- Timeline (12 mais recentes) -----
        timeline = events[:12]

        return {
            "kpis": {
                "today": kpi_today,
                "last_7d": kpi_7d,
                "last_30d": kpi_30d,
                "favorite_feature": favorite,
                "streak_days": streak,
            },
            "top_features": top_features,
            "heatmap": {
                "days": labels_days,
                "matrix": [heatmap[d] for d in labels_days],
                "max": heatmap_max,
            },
            "timeline": [
                {
                    "ts": e["ts"].isoformat(),
                    "feature": e["feature"],
                    "label": e["label"],
                    "target": e["target"],
                    "relative": e["relative"],
                }
                for e in timeline
            ],
            "total_30d": kpi_30d,
        }
