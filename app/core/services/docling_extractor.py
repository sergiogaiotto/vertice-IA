"""Wrapper para o Docling — extração de markdown estruturado de documentos.

O Docling (https://github.com/sergiogaiotto/docling, fork do IBM/docling)
recebe um arquivo binário (PDF/DOCX/PPTX/HTML/imagens via OCR) e produz
markdown com hierarquia de seções, tabelas em formato tabular e metadata
de página. É a melhor escolha open-source para preservar estrutura — vs
pdfminer/python-docx puros que jogam tudo em texto plano.

Modo síncrono com pesada CPU: extração de PDF de 30 páginas leva 5-20s.
Por isso o `knowledge_service.process_document()` chama essa função
DENTRO de `asyncio.to_thread`, mantendo o event loop livre.

Fallback: quando o Docling não está instalado ou falha (lib OS ausente,
formato exótico), tenta um decoder UTF-8 simples. Documentos texto-puro
ainda passam; binários quebram com mensagem clara.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("vertice.knowledge")


@dataclass
class ExtractionResult:
    markdown: str
    structure: dict   # JSONB-friendly: {"sections": [...], "tables_count": N, ...}
    error: str | None = None


def extract(file_bytes: bytes, filename: str) -> ExtractionResult:
    """Extrai markdown + estrutura de um arquivo arbitrário.

    Args:
        file_bytes: conteúdo bruto do arquivo.
        filename: usado para inferir o formato (extensão).

    Returns:
        ExtractionResult sempre — `error` preenchido em caso de falha
        (chama-se em background task; raise quebraria silenciosamente
        sem persistir o status).
    """
    suffix = (Path(filename).suffix or "").lower().lstrip(".")
    if not suffix:
        suffix = "txt"

    # Caminho rápido para texto puro — Docling é overkill e mais lento.
    if suffix in {"txt", "md", "markdown", "log"}:
        try:
            text = file_bytes.decode("utf-8", errors="replace")
            return ExtractionResult(
                markdown=text,
                structure={"format": suffix, "fast_path": True},
            )
        except Exception as e:  # noqa: BLE001
            return ExtractionResult(
                markdown="",
                structure={"format": suffix},
                error=f"decode falhou: {e}",
            )

    # Docling path.
    try:
        from docling.document_converter import DocumentConverter
    except ImportError:
        return ExtractionResult(
            markdown="",
            structure={"format": suffix},
            error="docling não está instalado (pip install docling)",
        )

    # Docling lê de arquivo, então persistimos temporariamente em disco.
    # Usar tempfile.NamedTemporaryFile causa problemas no Windows (lock do
    # arquivo aberto). Escrevemos manualmente e removemos no finally.
    import tempfile
    import os

    tmp_path: str | None = None
    try:
        fd, tmp_path = tempfile.mkstemp(suffix=f".{suffix}")
        os.close(fd)
        Path(tmp_path).write_bytes(file_bytes)

        converter = DocumentConverter()
        result = converter.convert(tmp_path)
        # `result.document` é o Document do Docling; `.export_to_markdown()`
        # produz a representação markdown estruturada.
        markdown = result.document.export_to_markdown()

        # Estrutura útil para retrieval/inspeção. A API exata do Docling
        # varia entre versões; capturamos o que está disponível com fallback.
        structure: dict = {"format": suffix}
        try:
            structure["pages"] = len(getattr(result.document, "pages", []) or [])
        except Exception:  # noqa: BLE001
            pass
        try:
            structure["tables_count"] = len(getattr(result.document, "tables", []) or [])
        except Exception:  # noqa: BLE001
            pass
        try:
            # Headings de primeiro nível, úteis para mostrar TOC na UI.
            headings = []
            for line in markdown.splitlines():
                if line.startswith("# "):
                    headings.append(line[2:].strip())
                elif line.startswith("## "):
                    headings.append(line[3:].strip())
                if len(headings) >= 20:
                    break
            structure["headings"] = headings
        except Exception:  # noqa: BLE001
            pass

        return ExtractionResult(markdown=markdown, structure=structure)
    except Exception as e:  # noqa: BLE001
        logger.exception("docling falhou em %s", filename)
        return ExtractionResult(
            markdown="",
            structure={"format": suffix},
            error=f"docling falhou: {type(e).__name__}: {e}",
        )
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
