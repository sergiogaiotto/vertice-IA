---
name: query-writing
description: Escreve e executa queries SELECT em SQLite, de simples a múltiplos JOINs, agregações e subqueries. Use quando o usuário pedir para consultar dados, contar registros, comparar grupos, ranquear, filtrar ou gerar relatórios tabulares.
---

# Skill: Query Writing

## Workflow para perguntas SIMPLES

Para perguntas sobre uma única tabela:

1. Identifique a tabela (use `sql_db_list_tables` se ainda não tiver)
2. Examine o schema com `sql_db_schema` para confirmar colunas e tipos
3. Construa a query: `SELECT cols FROM tabela WHERE ... ORDER BY ... LIMIT 20`
4. Execute com `sql_db_query`
5. Devolva o JSON estrito conforme `AGENTS.md`

## Workflow para perguntas COMPLEXAS

Para perguntas que precisam de múltiplas tabelas ou agregações:

### 1. Planeje com `write_todos`
- Tabelas necessárias
- Relacionamentos (FK→PK ou semânticos)
- Estrutura do JOIN
- Agregações e grupos

### 2. Examine schemas de TODAS as tabelas envolvidas
- Confirme nomes exatos das colunas de junção
- Verifique tipos (TEXT, INTEGER, REAL, TIMESTAMP)

### 3. Construa a query passo a passo
- `SELECT` colunas + agregados (`COUNT`, `SUM`, `AVG`, `MIN`, `MAX`)
- `FROM ... JOIN` com aliases curtos
- `WHERE` para filtros pré-agregação
- `GROUP BY` com TODAS as colunas não-agregadas
- `ORDER BY` significativo
- `LIMIT 20` (a menos que seja count global)

### 4. Valide e execute
- Confirme que cada JOIN tem condição
- Confirme GROUP BY completo
- Execute

## Recuperação de erros

Se a query falhar ou devolver resultado inesperado:

1. **Sem resultado** — verifique nomes de coluna no schema; cheque case-sensitivity e NULLs
2. **Erro de sintaxe** — re-examine JOINs, GROUP BY, aliases
3. **Timeout / muitos dados** — adicione filtros WHERE mais estritos ou reduza LIMIT
4. **Coluna não existe** — refaça `sql_db_schema` da tabela específica

## Exemplos

### Casos abertos por proprietário
```sql
SELECT
    owner,
    COUNT(*) AS total_casos
FROM bko_cases
GROUP BY owner
ORDER BY total_casos DESC
LIMIT 20;
```

### Transcrições com mais turnos do cliente
```sql
SELECT
    t.transaction_id,
    c.case_number,
    LENGTH(t.transcription_text) AS tamanho_texto
FROM transcripts t
LEFT JOIN bko_cases c ON c.contract_msisdn = t.verint_nr_contrato
WHERE t.transcription_text IS NOT NULL
ORDER BY tamanho_texto DESC
LIMIT 20;
```

### Sintaxe SQLite-específica útil

- Datas: `date(opened_at)`, `strftime('%Y-%m', opened_at)`
- Substring: `substr(col, 1, 100)`
- Concat: `col1 || ' ' || col2` (NÃO use `CONCAT`)
- Cast: `CAST(col AS REAL)` (sem `::` PostgreSQL-style)
- IFNULL: `IFNULL(col, 'desconhecido')`

## Qualidade

- Selecione apenas colunas relevantes
- Sempre aplique `LIMIT 20` (default)
- Use aliases curtos para clareza
- Para queries complexas: `write_todos` antes de executar
- NUNCA use INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE, CREATE, ATTACH
