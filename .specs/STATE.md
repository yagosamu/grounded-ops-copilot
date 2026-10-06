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
- **Phase / Task**: Phase 8, T51B complete; T52 next.
- **Completed**: T01-T51B and CI repair commit `7f4be7f` (digest-pinned local Silo image). T51B added an offline Production HA Terraform root, keyless SQS worker broker, two-replica search layout, alerts and runbook. Terraform mock tests passed 3/3; Release passed 401 tests, 90.43% coverage, 100% Python diff coverage, Gitleaks, floor guard and 138 selected tests.
- **In-progress**: No task in progress. No AWS resource was created; the live Pilot remains T54B, after T54.
- **Next step**: Run explicit `make pre-push BASE=origin/main`, then hand both local commits to the owner for push and GitHub Actions verification. Start T52 blue-green index rebuild afterward.
- **Blockers**: None for T52. A no-domain HTTPS entry point, JWT issuer, current cost review and explicit authorization remain prerequisites for T54B. Terraform tests do not prove live availability or recovery.
- **Uncommitted files**: `progress.md` remains ignored and local.
- **Branch**: `main`, with the CI repair and T51B commits pending user push; no push performed by the agent.
