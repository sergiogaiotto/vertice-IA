"""Chunker markdown-aware com cap de tokens.

Estratégia:
  1. Split por boundaries de cabeçalho (H1/H2/H3) — preserva contexto
     hierárquico em cada chunk.
  2. Para cada bloco resultante, se exceder `chunk_size`, sub-divide por
     parágrafos (\\n\\n).
  3. Se um parágrafo individual ainda exceder, fatia por sentenças.
  4. Aplica overlap configurável entre chunks consecutivos para evitar
     que um fato fique cortado na fronteira.

Tokens são estimados pela heurística len(text)//4 (mesma do model_router).
Não vale a pena instalar tiktoken só pra isso — a precisão de ±10% é
aceitável para chunking.

Cada chunk carrega metadata com os headings que o precedem (`section_path`),
o que é injetado no retrieval como contexto: "Trecho da seção: X › Y › Z".
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class Chunk:
    content: str
    metadata: dict
    tokens_estimated: int


def _approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)


def chunk_markdown(
    markdown: str,
    *,
    chunk_size: int = 800,
    chunk_overlap: int = 80,
) -> list[Chunk]:
    """Quebra markdown em chunks, respeitando hierarquia.

    Args:
        markdown: documento já extraído pelo Docling (ou texto puro).
        chunk_size: alvo de tokens por chunk (heurístico).
        chunk_overlap: tokens de sobreposição entre chunks consecutivos.

    Returns:
        Lista de Chunk com `metadata['section_path']` populado quando o
        chunk começa abaixo de um heading.
    """
    if not markdown or not markdown.strip():
        return []

    # Passo 1: segmenta por headings, preservando a posição do heading
    # em cada segmento subsequente.
    segments: list[tuple[list[str], str]] = []
    current_path: list[str] = []
    buf: list[str] = []
    for line in markdown.splitlines(keepends=True):
        m = _HEADING_RE.match(line)
        if m:
            # Flush do buffer atual com o path corrente.
            if buf:
                text = "".join(buf).strip()
                if text:
                    segments.append((list(current_path), text))
                buf = []
            # Atualiza o path. Profundidade = número de # (1..6).
            level = len(m.group(1))
            title = m.group(2).strip()
            # Trunca o path para o nível atual e adiciona o novo.
            current_path = current_path[: max(0, level - 1)] + [title]
            # Mantém o próprio heading no buffer — útil para o chunk
            # subsequente saber onde está.
            buf.append(line)
        else:
            buf.append(line)
    if buf:
        text = "".join(buf).strip()
        if text:
            segments.append((list(current_path), text))

    if not segments:
        segments = [([], markdown.strip())]

    # Passo 2: para cada segmento, gera 1+ chunks respeitando chunk_size.
    chunks: list[Chunk] = []
    for path, text in segments:
        for piece in _split_to_size(text, chunk_size, chunk_overlap):
            metadata = {}
            if path:
                metadata["section_path"] = " › ".join(path)
                metadata["section_depth"] = len(path)
            chunks.append(
                Chunk(
                    content=piece,
                    metadata=metadata,
                    tokens_estimated=_approx_tokens(piece),
                )
            )
    return chunks


def _split_to_size(text: str, target_tokens: int, overlap_tokens: int) -> list[str]:
    """Divide um texto em pedaços ~target_tokens, com overlap.

    Tenta cortar em parágrafos primeiro, depois em sentenças, por fim em
    caracteres (caso degenerado).
    """
    target_chars = target_tokens * 4
    overlap_chars = overlap_tokens * 4
    if len(text) <= target_chars:
        return [text]

    # Quebra preferencial por parágrafo.
    paragraphs = re.split(r"\n\s*\n", text)
    pieces: list[str] = []
    buf: list[str] = []
    cur_len = 0
    for p in paragraphs:
        plen = len(p)
        if cur_len + plen + 2 > target_chars and buf:
            pieces.append("\n\n".join(buf).strip())
            # Inicia novo buf com overlap do final do anterior.
            tail = pieces[-1][-overlap_chars:] if overlap_chars > 0 else ""
            buf = [tail, p] if tail else [p]
            cur_len = len(tail) + plen + 2
        else:
            buf.append(p)
            cur_len += plen + 2
    if buf:
        pieces.append("\n\n".join(buf).strip())

    # Se algum pedaço ainda passou do alvo (parágrafo gigante), divide
    # por sentenças.
    out: list[str] = []
    for piece in pieces:
        if len(piece) <= target_chars * 1.5:
            out.append(piece)
            continue
        # Subdivide por sentença.
        sentences = re.split(r"(?<=[.!?])\s+", piece)
        sbuf: list[str] = []
        slen = 0
        for s in sentences:
            if slen + len(s) > target_chars and sbuf:
                out.append(" ".join(sbuf).strip())
                tail = out[-1][-overlap_chars:] if overlap_chars > 0 else ""
                sbuf = [tail, s] if tail else [s]
                slen = len(tail) + len(s)
            else:
                sbuf.append(s)
                slen += len(s) + 1
        if sbuf:
            out.append(" ".join(sbuf).strip())

    # Fallback degenerado: força corte por char.
    final: list[str] = []
    for piece in out:
        if len(piece) <= target_chars * 2:
            final.append(piece)
            continue
        # Fatia bruta com overlap.
        i = 0
        while i < len(piece):
            final.append(piece[i : i + target_chars])
            i += max(1, target_chars - overlap_chars)
    return [p for p in final if p.strip()]
