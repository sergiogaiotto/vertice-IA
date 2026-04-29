# Skills (`SKILL.md`) — contratos executáveis dos agentes

Cada arquivo `SKILL.md` neste diretório é o **contrato declarativo** de um agente do Vértice. Não é documentação acessória — é o artefato que o System Prompt Canônico carrega em runtime para decidir:

- **Quais ferramentas** podem ser invocadas pelo agente (MCP, tools internas)
- **Em que ordem** as ferramentas devem ser chamadas
- **Sob quais condições** cada ferramenta é apropriada
- **Qual o contrato de saída** que o agente deve respeitar

## Estrutura recomendada

```markdown
# <Nome do Agente>

## Identidade
Quem é o agente, em uma frase. Define escopo e tom.

## Inputs aceitos
- input_data: ...
- config_override: ...

## Saída esperada
Schema/forma. Exemplos curtos.

## Ferramentas autorizadas
- tool_name(params): condição de uso

## Política de roteamento
Quando preferir um modelo a outro.

## Guardrails
- Entrada: ...
- Saída: ...

## Sinais de Failsafe
Critérios que disparam human-in-the-loop.
```

## Promoção para produção

O fluxo recomendado é:

1. Editar o `SKILL.md` em ambiente de dev
2. Rodar testes do agente (incluindo casos sintéticos)
3. Promover via API (`POST /api/modules/`) — o registro vai para `modules.skill_path`
4. O System Prompt Canônico passa a carregar a versão promovida em produção

## Versionamento

A tabela `prompts` em SQLite versiona cada bundle (guardrail-system-guardrail) por módulo. O `SKILL.md` aponta a versão ativa em uso.
