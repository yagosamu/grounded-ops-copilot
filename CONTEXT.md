# GroundedOps Domain Language

Este contexto descreve o conhecimento técnico versionado usado para responder perguntas e investigar incidentes com evidências verificáveis.

## Language

**Knowledge Corpus**:
O conjunto versionado de fontes autorizadas que pode fornecer evidências ao produto.
_Avoid_: Database, knowledge base, documents

**Public Technical Source**:
Um artefato real e redistribuível do ecossistema OpenTelemetry, como documentação, especificação, release, issue ou pull request.
_Avoid_: Public document, external data

**Synthetic Company**:
A organização fictícia que contextualiza aplicações, equipes, serviços e regras de acesso sem representar uma empresa real.
_Avoid_: Demo tenant, fake client

**Synthetic Operational Artifact**:
Um runbook, ADR ou postmortem criado para a Synthetic Company e identificado como sintético em seus metadados.
_Avoid_: Fake document, mock data

**Evidence**:
Um trecho autorizado e versionado do Knowledge Corpus que pode sustentar uma afirmação ou etapa de investigação.
_Avoid_: Context, search result, source

**Grounded Answer**:
Uma resposta cujas afirmações factuais estão ligadas a Evidence verificável ou foram explicitamente marcadas como não sustentadas.
_Avoid_: RAG response, AI answer

**Investigation**:
Uma análise assíncrona e delimitada que combina múltiplas Evidence e ferramentas para produzir um relatório auditável.
_Avoid_: Agent run, deep search
