# **Documentação Técnica: Framework de Building Blocks de IA**

**Nome:** Vértice  
**Versão:** 1.0.0  
**Abordagem:** Spec-Driven Development (SDD)  
**Arquitetura:** Hexagonal / Portas e Adaptadores
**Linguagem:** Python
**Framework:** FastAPI
**Conectividade:** OpenAPI / Swagger
**Bibliotecas:** Deep-Agent Harness, LangGraph
**Modelos LLM API_KEY:** OpenAI GPT-4.1, Maritaca.ai Sabia-4,Gemma.GAIA 4Bi
**Banco de Dados:** SQLite
**Imterface Visual e Navegação:** (https://github.com/sergiogaiotto/agente-inteligencia; https://agente-inteligencia.onrender.com/), com menu a esquerda que pode ser aberto e minizado, parte central com operação de cada módulo e a direita deve oferecer detalhes por contexto de cada item de cada módulo
**UI:** template engine
**Arquivos:** readme.txt; requirements.txt; .env

Use o github como principal repositório para escolher as ferramentas mais adequadas.

## ---

**1\. Visão Geral e Objetivos**

Este documento define a especificação técnica para um ecossistema de agentes de IA modulares e altamente escaláveis. O objetivo é criar uma estrutura de "Building Blocks" onde cada funcionalidade (autenticação, processamento, FinOps) é um módulo independente, onde os guardrail de entrada - system prompt - guardrail de saída sejam parametrizáveis para todo e qualquer módulo criado, fracamente acoplado e pronto para implantação em ambiente Kubernetes (AI Mesh).

Plataforma de agentes hierárquica, poliárquica em execução e monárquica em governança, na qual cada agente é um processo computacional cuja identidade funcional é definida por um artefato declarativo `SKILL.md`. O `SKILL.md` não é documentação: é o **contrato executável** e a **alma semântica** do agente — carregado em tempo de ativação, interpretado pelo System Prompt Canônico de cada agente, e vinculante sobre quais ferramentas (MCP) podem ser invocadas, em que ordem, sob quais condições e com quais contratos de saída.

### **Princípios Fundamentais:**

* **Modularidade:** Módulos independentes que podem ser reutilizados em diferentes soluções.  
* **Segurança por Design:** Autenticação robusta e políticas de governança centralizadas.  
* **Transparência de Custo:** FinOps granular integrado ao ciclo de vida de cada chamada.  
* **Observabilidade Nativa:** Rastreamento completo via OpenTelemetry e LangFuse.

## ---

**2\. Arquitetura do Sistema**

### **2.1. Arquitetura Hexagonal**

O *Core Domain* da aplicação é isolado de tecnologias externas. Adaptadores de entrada (FastAPI) e adaptadores de saída (SQLite, OpenAI, Maritaca Sabiá-4 e Gaia 4Bi) conectam-se ao núcleo através de portas bem definidas.

### **2.2. AI Mesh & Infraestrutura**

| Componente | Tecnologia/Padrão | Função   |
| :---- | :---- | :---- |
| Orquestração | Kubernetes | Hospedagem e escalonamento dos microserviços. |
| Service Mesh | Istio / Linkerd | Segurança via mTLS e gerenciamento de tráfego. |
| Políticas | OPA (Open Policy Agent) | Autorização desacoplada do código. |
| Coletor | OpenTelemetry | Padronização de métricas e traces. |

## 

## **2.2. Arquitetura Hexagonal**

## **O Core Domain da aplicação é isolado de tecnologias**

## ---

**3\. Especificação de Dados (SQLite)**

### **3.1. Esquema de Autenticação e RBAC**

`CREATE TABLE users (`  
    `id UUID PRIMARY KEY,`  
    `username TEXT UNIQUE NOT NULL,`  
    `hashed_password TEXT NOT NULL,`  
    `salt TEXT NOT NULL,`  
    `is_active BOOLEAN DEFAULT TRUE`  
`);`

`CREATE TABLE roles (`  
    `id INTEGER PRIMARY KEY,`  
    `name TEXT UNIQUE NOT NULL -- ex: 'admin', 'analista_n3'`  
`);`

`CREATE TABLE permissions (`  
    `id INTEGER PRIMARY KEY,`  
    `code TEXT UNIQUE NOT NULL -- ex: 'execute:agent_analysis'`  
`);`

### **3.2. Registro de Módulos (Registry)**

Permite a descoberta dinâmica e o gerenciamento de building blocks.  
`CREATE TABLE modules (`  
    `id UUID PRIMARY KEY,`  
    `name TEXT UNIQUE NOT NULL,`  
    `endpoint_url TEXT NOT NULL,`  
    `status TEXT DEFAULT 'active',`  
    `config_params JSON -- Thresholds, Failsafe status, Sanitization level`  
`);`

### **3.3. FinOps Ledger**

`CREATE TABLE finops_ledger (`  
    `id INTEGER PRIMARY KEY AUTOINCREMENT,`  
    `user_id UUID,`  
    `module_id UUID,`  
    `model_name TEXT,`  
    `tokens_input INTEGER,`  
    `tokens_output INTEGER,`  
    `cost_estimated DECIMAL(10, 6),`  
    `context_tag TEXT`  
`);`

## ---

**4\. Contrato de API (OpenAPI Standard)**

Cada módulo deve expor e consumir a interface padrão abaixo para garantir a interoperabilidade no Spec-Driven Development.  
`openapi: 3.0.3`  
`info:`  
  `title: Standard Module Contract`  
  `version: 1.0.0`  
`paths:`  
  `/v1/process:`  
    `post:`  
      `summary: Execução do Building Block`  
      `security:`  
        `- OAuth2: [execute]`  
      `request_body:`  
        `content:`  
          `application/json:`  
            `schema:`  
              `type: object`  
              `properties:`  
                `input_data: { type: object }`  
                `config_override:`  
                  `type: object`  
                  `properties:`  
                    `threshold: { type: number }`  
                    `sanitization: { type: boolean }`  
                `finops_metadata:`  
                  `type: object`  
                  `properties:`  
                    `tag: { type: string }`

## ---

**5\. Segurança e Governança Operacional**

* **Sanitização de Inputs:** Filtros automáticos contra Prompt Injection em todos os adaptadores de entrada.  
* **Failsafe & Human-in-the-loop:** Implementação de gatilhos baseados em threshold de confiança. Ações críticas requerem confirmação via cockpit.  
* **Criptografia:** Senhas com SHA-256 e Salt único por usuário; comunicação interna via mTLS.

## ---

**6\. Observabilidade e Inovação**

### **6.1. Ciclo de Vida de Observabilidade**

1. **LangFuse:** Rastreamento de cadeias de pensamento (Chain of Thought) e custos.  
2. **MLflow:** Tracking de experimentos e versões de prompts.  
3. **Auto-Aperfeiçoamento:** Módulo que analisa logs do LangFuse para sugerir otimizações de prompts no próximo ciclo de desenvolvimento.

### **6.2. Cockpit Inteligente**

O cockpit é alimentado dinamicamente pelas tabelas de métricas e configurações, oferecendo:

* **Visão Executiva:** Burn-down de custos e ROI por módulo.  
* **Visão Técnica:** Taxa de sucesso de agentes e latência média.  
* **Visão Operacional:** Fila de aprovação de "Failsafe" para ações pendentes.

### **6.3. Módulo de Prompts Centralizado**

1. **Prompts:** Guardrail de entrada - system prompt - guardrail de saída para cada módulo.  

## ---

**7\. Guia de Implementação**

1. Definir a Porta (Interface Python/FastAPI).  
2. Implementar o Adaptador de Core (Lógica de Negócio).  
3. Registrar o módulo na tabela modules.  
4. Configurar políticas no OPA.  
5. Validar o rastreamento no LangFuse.

**8\. Módulos para Implementação (Modulados)**
1. Radar Voz do Cliente: Classificador de categorias que representam os motivos de contato do cliente e ações do operador. Com visão apartada por produto, Residencial/ móvel, perfil  de atendimento e parceiros. Escolher um Número de Contrato em uma lista a partir de uma tabela que será carregada por upload Excel, e deverá trazer exibir na lista o datetime "dd/mm/yyyy hh:mm:ss", Call ID, Contact ID, Operador. Com a seleção do Número do Contrato uma transcrição de atendimento é trazida e passada pelos prompts de "Análises", onde deve ser possível criar Cards de Análises organizados no centro da tela. Cada Card de Análise será criado a partir de um prompt, onde seja informado o "Nome da Análise", "Prompt", "Tipo de Saída:" que irá entregar o prompt de saída tamanho esperado em texto, palavras ou termos, podendo ser Sumário, Resumo, Identificação de Intenção, Uma Palavra.
2. Gestão Churn: classificação dos motivos verbalizados pelos clientes com intenção de cancelamento. Evolução modelo de classificação em múltiplas etapas (onde a modegalem de dados permita a criação de motivos e subs-motivos e subsub-motivos e assim por diaante) 

*Documento gerado para suporte ao processo de Spec-Driven Development.*