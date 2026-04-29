"""Use case: CRUD de Skills (SKILL.md) baseado em filesystem.

Skills são contratos declarativos versionados em arquivos .md dentro de
`app/skills/`. Este serviço encapsula leitura/escrita/parsing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

SKILLS_DIR = Path(__file__).resolve().parents[2] / "skills"


@dataclass
class SkillFile:
    name: str               # ex: "radar_intent"
    title: str              # extraído do primeiro H1
    path: str               # caminho relativo
    content: str
    sections: dict[str, str]
    updated_at: datetime
    size_bytes: int


_SECTION_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)


def _parse_sections(content: str) -> dict[str, str]:
    """Extrai seções H2 e seu conteúdo até a próxima H2."""
    sections: dict[str, str] = {}
    matches = list(_SECTION_RE.finditer(content))
    for i, m in enumerate(matches):
        title = m.group(1).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        sections[title] = content[start:end].strip()
    return sections


def _extract_title(content: str) -> str:
    m = re.search(r"^#\s+(.+?)\s*$", content, re.MULTILINE)
    return m.group(1).strip() if m else ""


class SkillService:

    def __init__(self, skills_dir: Path | None = None):
        self.dir = skills_dir or SKILLS_DIR
        self.dir.mkdir(parents=True, exist_ok=True)

    def _skill_path(self, name: str) -> Path:
        # sanitize: só permitir nomes seguros
        safe = re.sub(r"[^a-z0-9_\-]", "", name.lower())
        if not safe:
            raise ValueError("nome inválido")
        return self.dir / f"{safe}.md"

    def list_all(self) -> list[SkillFile]:
        out: list[SkillFile] = []
        for p in sorted(self.dir.glob("*.md")):
            if p.name.upper() == "README.MD":
                continue
            try:
                content = p.read_text(encoding="utf-8")
            except OSError:
                continue
            stat = p.stat()
            out.append(
                SkillFile(
                    name=p.stem,
                    title=_extract_title(content) or p.stem,
                    path=f"app/skills/{p.name}",
                    content=content,
                    sections=_parse_sections(content),
                    updated_at=datetime.fromtimestamp(stat.st_mtime),
                    size_bytes=stat.st_size,
                )
            )
        return out

    def get(self, name: str) -> SkillFile | None:
        p = self._skill_path(name)
        if not p.exists():
            return None
        content = p.read_text(encoding="utf-8")
        stat = p.stat()
        return SkillFile(
            name=p.stem,
            title=_extract_title(content) or p.stem,
            path=f"app/skills/{p.name}",
            content=content,
            sections=_parse_sections(content),
            updated_at=datetime.fromtimestamp(stat.st_mtime),
            size_bytes=stat.st_size,
        )

    def save(self, name: str, content: str) -> SkillFile:
        p = self._skill_path(name)
        p.write_text(content, encoding="utf-8")
        return self.get(p.stem)

    def delete(self, name: str) -> bool:
        p = self._skill_path(name)
        if p.exists():
            p.unlink()
            return True
        return False

    @staticmethod
    def template() -> str:
        """Template inicial para novas skills."""
        return (
            "# Novo Agente\n\n"
            "## Identidade\n"
            "Quem é o agente, em uma frase. Define escopo e tom.\n\n"
            "## Inputs aceitos\n"
            "- `input_data`: descrição\n\n"
            "## Saída esperada\n"
            "Schema/forma. Exemplos curtos.\n\n"
            "## Ferramentas autorizadas\n"
            "- `tool_name(params)`: condição de uso\n\n"
            "## Política de roteamento\n"
            "- Default: `sabia-4`\n"
            "- Fallback: `gpt-4.1`\n\n"
            "## Guardrails\n\n"
            "### Entrada\n"
            "- ...\n\n"
            "### Saída\n"
            "- ...\n\n"
            "## Sinais de Failsafe\n"
            "- ...\n"
        )

    @staticmethod
    def detect_output_format(skill_content: str | None) -> dict:
        """Inspeciona seções 'Saída esperada' e 'Saída' para inferir formato.

        Devolve {format, mime_type, file_extension, is_downloadable}:
        - format: 'json' | 'csv' | 'markdown' | 'html' | 'text'
        - is_downloadable: True se o output deve virar arquivo (json/csv/html)
        """
        default = {"format": "markdown", "mime_type": "text/markdown",
                   "file_extension": "md", "is_downloadable": False}
        if not skill_content:
            return default

        sections = _parse_sections(skill_content)
        # procura "Saída esperada", "Saída", "Output"
        sec = None
        for k in ("Saída esperada", "Saída", "Output", "Saida esperada", "Saida"):
            if k in sections:
                sec = sections[k].lower()
                break
        if not sec:
            return default

        # CSV
        if "csv" in sec or "tsv" in sec or "tabular" in sec:
            return {"format": "csv", "mime_type": "text/csv",
                    "file_extension": "csv", "is_downloadable": True}
        # HTML
        if "html" in sec or "página" in sec or "pagina" in sec:
            return {"format": "html", "mime_type": "text/html",
                    "file_extension": "html", "is_downloadable": True}
        # JSON estrito
        if "json estrito" in sec or "json strict" in sec or "responda em json" in sec or "saída em json" in sec or "```json" in sec:
            return {"format": "json", "mime_type": "application/json",
                    "file_extension": "json", "is_downloadable": True}
        # JSON genérico (mais leve — exibe inline mas oferece download)
        if "json" in sec:
            return {"format": "json", "mime_type": "application/json",
                    "file_extension": "json", "is_downloadable": False}
        # XML
        if "xml" in sec:
            return {"format": "xml", "mime_type": "application/xml",
                    "file_extension": "xml", "is_downloadable": True}
        # default = markdown inline
        return default
