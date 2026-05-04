---
name: schema-exploration
description: Lista tabelas, descreve colunas e tipos, identifica relacionamentos e mapeia o modelo de dados. Use quando o usuário perguntar quais tabelas existem, quais colunas uma tabela tem, como duas entidades se relacionam ou quando você precisar mapear o esquema antes de construir uma query complexa.
---

# Skill: Schema Exploration

## Workflow

### 1. Liste as tabelas autorizadas
Use `sql_db_list_tables` para ver as tabelas no escopo. **Importante**: o
escopo já é restrito às tabelas que o usuário escolheu — não tente
acessar tabelas fora dessa lista (vão falhar ou trazer dados sensíveis).

### 2. Examine schemas específicos
Use `sql_db_schema` com nomes de tabela para descobrir:
- **Nomes das colunas** disponíveis
- **Tipos de dados** (TEXT, INTEGER, REAL, TIMESTAMP)
- **Sample rows** (3 linhas de exemplo)
- Convenções: PKs costumam terminar em `_id` ou `id`

### 3. Identifique relacionamentos

Em SQLite os FKs nem sempre estão declarados — use heurísticas:

- Colunas terminadas em `_id`, `_number`, `_msisdn` costumam ser referências
- Compare nomes entre tabelas: `bko_cases.contract_msisdn` ↔ `transcripts.verint_nr_contrato`
- Verifique sample rows para confirmar que os valores batem

### 4. Mapeie cardinalidades quando relevante
Para uma pergunta tipo "quantas transcrições por caso?", confirme:
- 1:1, 1:N ou N:N entre as tabelas
- Existência de tabelas de junção

### 5. Devolva o JSON conforme `AGENTS.md`

Se a pergunta foi puramente de schema (ex: "quais tabelas existem?"), você
ainda DEVE devolver o JSON estrito. Coloque o levantamento de schema como
texto em `understanding` e use uma query simples (ex: `SELECT name FROM
sqlite_master WHERE type='table'`) para preencher `result_*`.

## Exemplos

### "Quais tabelas posso consultar?"

1. `sql_db_list_tables` → ['bko_cases', 'transcripts']
2. JSON:
   ```json
   {
     "understanding": "Você tem 2 tabelas no escopo: bko_cases (casos do BKO) e transcripts (transcrições JSON)",
     "sql": "SELECT name, type FROM sqlite_master WHERE type='table' ORDER BY name",
     "result_columns": ["name", "type"],
     "result_rows": [["bko_cases", "table"], ["transcripts", "table"]],
     "row_count": 2,
     "analyses": [
       "Para análise de casos por proprietário, comece pela tabela bko_cases",
       "Para análise textual de chamadas, use transcripts.transcription_text",
       "Para cruzar caso com transcrição, junte bko_cases.contract_msisdn = transcripts.verint_nr_contrato"
     ]
   }
   ```

### "Como ligar bko_cases com transcripts?"

1. `sql_db_schema` em ambas
2. Identifique: `bko_cases.contract_msisdn` semanticamente igual a `transcripts.verint_nr_contrato`
3. JSON com query de exemplo do JOIN funcionando

## Qualidade

- Não invente colunas
- Não acesse tabelas fora do escopo
- Sample rows valem mais do que tipos isolados — sempre olhe os dados de exemplo
- Para perguntas puramente de schema, ainda devolva JSON estrito
