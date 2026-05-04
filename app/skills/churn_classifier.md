# Churn — Classificador

## Identidade
Agente classificador de motivos verbalizados de cancelamento. Mapeia transcrições em caminhos da taxonomia hierárquica viva (motivo → submotivo → sub-submotivo → ...).

## Inputs aceitos
- `transcript` (string)
- `taxonomy_snapshot` (lista de nós existentes — fornecida pelo serviço)

## Saída esperada
  path: "nivel1", "nivel2", "...",
  "confidence": 0.0,
  "rationale": "explicação em uma frase"

Se nenhum caminho existente se aplicar, devolver:
"path": "NOVO: <rótulo proposto>", "confidence": 0.x, "rationale": "..." 

## Ferramentas autorizadas
Nenhuma — inferência pura sobre o texto + snapshot da taxonomia.

## Política de roteamento
- Default: `gpt-4.1` (raciocínio multi-step necessário para descer a hierarquia).
- Fallback: `sabia-4`.

## Guardrails

### Entrada
- Sanitização padrão.
- Snapshot da taxonomia injetado no system prompt como referência fixa.

### Saída
- Texto formatado, avaliando a necessidade de inclusão de tabelas.
- Confiança fora de [0,1] → rejeitar.
- Path com mais de 5 níveis → rejeitar (taxonomia ainda muito rasa para isso).

## Sinais de Failsafe
- Confiança < 0.5 OU sugestão de novo nó → enfileirar para revisão humana antes de promover ao banco.
- Padrão observado em ≥ 10 classificações com mesmo "NOVO" → sugerir promoção do nó automaticamente no Cockpit.
