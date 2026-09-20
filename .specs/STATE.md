# Estado do projeto

## Decisões

| ID | Status | Decisão | Razão |
| --- | --- | --- | --- |
| AD-001 | active | Começar como monólito modular com API e worker implantáveis separadamente | Preserva interfaces claras sem o custo prematuro de microserviços |
| AD-002 | active | Usar PostgreSQL e object storage como fontes duráveis; tratar OpenSearch como projeção reconstruível | Permite reindexação, troca de embeddings e recuperação |
| AD-003 | active | Manter `Ask` determinístico separado de `Investigate` agentic | Controla latência, custo e modos de falha |
| AD-004 | active | Exigir evidência experimental antes de promover embeddings, hybrid search, reranker, cache ou agente | Complexidade só entra quando supera o baseline |
| AD-005 | active | Bloquear todo push quando `make pre-push` falhar | Testes locais são parte do fluxo de entrega, não uma etapa opcional |
| AD-006 | active | Código, commits e documentação técnica principal serão escritos em inglês; material explicativo pode ser bilíngue | Maximiza valor de portfólio e colaboração internacional |
| AD-007 | active | O corpus inicial combinará fontes públicas do OpenTelemetry com artefatos empresariais sintéticos claramente identificados | Preserva autenticidade e reprodutibilidade enquanto permite incidentes, ACLs e casos de avaliação controlados |
| AD-008 | active | A plataforma usará Python 3.13, FastAPI/Pydantic, PostgreSQL, OpenSearch, S3-compatible storage, Celery/Redis e OpenTelemetry/Langfuse; o frontend Next.js entrará após o backend vertical | A composição demonstra search engineering e operação real mantendo o início como monólito modular com API e worker separados |
| AD-009 | active | A geração v1 usará a OpenAI atrás de uma interface própria; GPT-5 nano, GPT-4o Mini e GPT-5.6 Luna competirão na mesma suíte e o modelo mais barato que passar todos os gates será promovido; Terra será apenas upper bound em casos difíceis | Evita pagar por capacidade não demonstrada e transforma seleção de modelo em uma decisão reproduzível orientada por qualidade e custo |
| AD-010 | active | AWS será o cloud-alvo e Terraform será a IaC; haverá um perfil Pilot econômico e um perfil Production HA multi-AZ, enquanto desenvolvimento local continuará em Docker Compose | Permite demonstrar uma implantação real e reproduzível sem manter redundância cara antes de existirem usuários que a justifiquem |
| AD-011 | active | O contrato de qualidade usará bloqueio progressivo, invariantes fixos, ratchets medidos e gates com orçamentos de 30 s, 90 s, 5 min e 15 min | Mantém o rigor necessário para produção sem incentivar que checks lentos sejam ignorados; complexidade e limites dependentes do workload passam a bloquear somente após evidência representativa |
| AD-012 | active | Promover GPT-4o Mini para geração v1 e manter GPT-5.6 Luna como baseline medido | GPT-4o Mini preservou 100% de sucesso, citações e abstention no dataset v1 com custo 45,76% menor; Nano custou mais e Terra permaneceu apenas como upper bound |

## Handoff

- **Feature**: GroundedOps production RAG platform.
- **Phase / Task**: Phase 6 in progress; T39 is complete and T37 closed Phase 5.
- **Completed**: T01-T39, including policy-aware bounded retrieval, version comparison and incident search tools.
- **In-progress**: none.
- **Next step**: T40, implement the bounded investigation graph and lifecycle state.
- **Blockers**: none.
- **Uncommitted files**: none after the T39 commit; `progress.md` remains ignored and local.
- **Branch**: `main`; no push performed.
