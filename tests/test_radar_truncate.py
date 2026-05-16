"""Testes unitários de `_smart_truncate_transcript`.

Função pura — testa sem subir banco, container ou LLM.
"""

from __future__ import annotations

import pytest

from app.core.services.radar_service import (
    _TRUNCATE_MARKER,
    _smart_truncate_transcript,
)


def test_text_curto_passa_intacto():
    """Texto abaixo do limite NÃO é truncado nem ganha marker."""
    text = "Cliente: Olá. Atendente: Boa tarde."
    out = _smart_truncate_transcript(text, max_chars=12000)
    assert out == text


def test_texto_no_limite_exato_passa_intacto():
    """Boundary: len(text) == max_chars → passa intacto."""
    text = "A" * 1000
    out = _smart_truncate_transcript(text, max_chars=1000)
    assert out == text
    assert _TRUNCATE_MARKER not in out


def test_texto_longo_recebe_head_e_tail_com_marker():
    """Texto acima do limite vira head + marker + tail."""
    # 1000 chars de 'H' + 1000 chars de 'T' = 2000 total
    text = "H" * 1000 + "T" * 1000
    out = _smart_truncate_transcript(text, max_chars=500, tail_ratio=0.4)
    # Resultado deve conter o marker
    assert _TRUNCATE_MARKER in out
    # Deve começar com chars do head (H)
    assert out.startswith("H")
    # Deve terminar com chars do tail (T)
    assert out.endswith("T")
    # Tamanho total <= max_chars (incluindo marker)
    assert len(out) <= 500


def test_tail_ratio_controla_proporcao():
    """tail_ratio=0.4 → 40% do espaço útil é o final. Mais alto = mais final."""
    # Texto onde início é puramente 'A' e fim puramente 'B'
    text = "A" * 500 + "B" * 500
    out_low_tail = _smart_truncate_transcript(text, max_chars=200, tail_ratio=0.2)
    out_high_tail = _smart_truncate_transcript(text, max_chars=200, tail_ratio=0.6)

    # Conta quantos 'B' (do final) cada resultado tem — mais alto deve ter mais
    b_count_low = out_low_tail.count("B")
    b_count_high = out_high_tail.count("B")
    assert b_count_high > b_count_low, (
        f"tail_ratio maior deveria preservar mais do final: "
        f"low={b_count_low}, high={b_count_high}"
    )


def test_input_vazio_retorna_string_vazia():
    assert _smart_truncate_transcript("") == ""
    assert _smart_truncate_transcript(None) == ""  # type: ignore[arg-type]


def test_max_chars_minusculo_fallback_sem_marker():
    """Caso degenerado: max_chars menor que o marker. Para evitar resultado
    bizarro com marker maior que conteúdo, cai pra truncate clássico do
    início, sem marker."""
    text = "A" * 100
    # marker tem ~70 chars; max_chars=20 não acomoda
    out = _smart_truncate_transcript(text, max_chars=20)
    assert out == "A" * 20
    assert _TRUNCATE_MARKER not in out


def test_caso_realistic_22631_chars():
    """Regressão direta: simula uma transcrição como a reportada
    (~22631 chars). Garante que NUNCA mais teremos 'só os primeiros
    6000 chars' chegando ao LLM — final preservado."""
    head = "Atendente: Início da chamada. " * 500     # ~15000 chars
    middle = "Cliente: bla bla bla. " * 300            # ~6600 chars
    tail = "Cliente: Por favor, cancele MEU plano agora." * 50  # ~2200 chars
    text = head + middle + tail
    assert len(text) > 20000, "preparação do teste falhou"
    out = _smart_truncate_transcript(text)  # defaults max=12000, tail=0.4
    # Marker presente
    assert _TRUNCATE_MARKER in out
    # Final preservado — o trecho mais importante da ligação chega ao LLM
    assert "cancele MEU plano agora" in out, (
        "final da transcrição não foi preservado — "
        "regressão do comportamento desejado"
    )
    # Início também preservado
    assert "Atendente: Início" in out
