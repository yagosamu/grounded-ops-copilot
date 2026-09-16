# Contrato de qualidade

Última revisão: 2026-09-16

Este arquivo define o mínimo necessário para aceitar uma mudança. Os limites só podem ser alterados em um commit próprio, com justificativa e ADR. Eles não podem ser reduzidos no mesmo commit da mudança que falhou.

## Piso obrigatório

- Nenhum segredo, token ou credencial no código, fixtures, logs ou histórico.
- Nenhum `# noqa`, `# type: ignore`, teste ignorado ou regra desativada sem exceção registrada abaixo.
- Nenhum stub não implementado, `except` vazio ou `TODO` usado no lugar da entrega.
- Nenhum teste removido, afrouxado ou reescrito para acomodar a implementação.
- Todo comportamento novo deve ter teste derivado do requisito, no mesmo task que cria o comportamento.
- Nenhum push é permitido sem o gate `pre-push` verde.
- Este arquivo não pode ser enfraquecido para aprovar uma mudança.

## Gates planejados

Os comandos passam a valer assim que o bootstrap da Fase 0 os instalar.

| Nível | Quando executa | Comando canônico | Orçamento | Bloqueia |
| --- | --- | --- | --- | --- |
| Rápido | Durante cada task | `make check` | <= 30 s | conclusão do task |
| Completo | Ao concluir uma task que exige integração | `make test` | gate da task <= 90 s | conclusão do task |
| Pre-push | Imediatamente antes de todo push | `make pre-push` | <= 5 min | push |
| Release | Pull request para a branch principal | `make release-check` | meta <= 15 min no CI | merge/release |
| Operacional | Antes de release de produção | `make operational-test` | pipeline separado, sem limite local rígido | release de produção |

`make pre-push` deve executar formatação em modo check, lint, tipos, testes unitários, testes de integração disponíveis localmente, coverage e detecção de segredos. `make release-check` adiciona E2E, evals, scans de segurança e testes de migração. Testes de carga e recuperação rodam em ambiente dedicado antes de uma release de produção.

Se um gate ultrapassar seu orçamento, a resposta é otimizar a execução, limitar o
trabalho ao diff aplicável ou mover a parcela cara para o próximo nível. A
verificação não pode ser removida ou enfraquecida para cumprir o orçamento.

## Restrições mensuráveis

| Dimensão | Regra inicial | Razão | Verificação planejada |
| --- | --- | --- | --- |
| Tipos | zero erro | Erros de contrato devem aparecer antes do runtime | `uv run mypy src` |
| Lint e formato | zero erro | Mantém mudanças previsíveis e revisáveis | `uv run ruff check .` e `uv run ruff format --check .` |
| Testes | 100% dos critérios de aceite e edge cases mapeados | Coverage isolado não prova comportamento | matriz requisito-teste |
| Coverage | pelo menos 80% das linhas alteradas | Força evidência sem tornar configuração trivial um bloqueio | `pytest-cov` + diff coverage |
| Segurança de código | zero finding alto ou crítico | Um finding grave bloqueia produção | Semgrep/Opengrep |
| Dependências | zero vulnerabilidade alta ou crítica sem exceção vigente | Dependências fazem parte da superfície de ataque | OSV Scanner |
| Segredos | zero ocorrência | Credenciais vazadas são incidente, não warning | Gitleaks com `--redact` |
| Retrieval | Recall@10 não pode regredir; melhoria promovida deve ganhar pelo menos 5% relativo em nDCG@10 | Impede complexidade sem ganho mensurável | harness de eval versionado |
| Reranking | ganho mínimo de 3% relativo em nDCG@10 e acréscimo p95 máximo de 400 ms | Explicita qualidade versus latência | harness de eval e benchmark |
| Respostas | citation coverage >= 95% e zero citação inexistente no golden set | Citação é requisito de confiança | eval de resposta |
| Autorização | zero acesso cruzado entre tenants nos testes adversariais | Um único vazamento inviabiliza o produto | suíte de segurança/E2E |
| Agente | ganho mínimo de 10% em sucesso multi-hop; custo <= 2,5x do RAG padrão | Justifica a rota mais cara e variável | benchmark agentic versus baseline |
| Promoção de modelo | citation coverage >= 95%, zero citação inexistente, queda máxima de 2 pontos percentuais em task success e redução de custo >= 30% frente ao baseline mais caro | Permite adotar modelos menores sem esconder regressões de qualidade | benchmark de respostas por modelo |
| Arquitetura | zero import proibido entre módulos e interfaces | Impede que adapters e frameworks contaminem o domínio | `lint-imports` |
| Acessibilidade web | zero violação crítica ou séria | O frontend precisa ser utilizável e não apenas demonstrável | Axe contra preview |
| Performance web | LCP <= 2,5 s e CLS <= 0,1 | Usa os limites iniciais de boa experiência do usuário | Lighthouse contra preview |

## Ativação escalonada

| Dimensão | Começa a bloquear |
| --- | --- |
| Floor, testes, tipos, lint, formato, coverage e secrets | Fase 0 |
| Limites arquiteturais | Assim que os primeiros módulos forem criados |
| Scans completos de código e dependências | Primeiro CI funcional |
| Budgets de retrieval e API | Primeiro vertical slice medido |
| Evals de respostas e modelos | Fase de Grounded Answering |
| Evals agentic | Fase de Bounded Agentic Investigation |
| Acessibilidade e performance web | Primeiro preview do frontend |
| Testes operacionais e recuperação | Primeiro ambiente AWS Pilot |

## Política de bloqueio progressivo

### Bloqueiam imediatamente

- Testes automatizados obrigatórios para todo critério de aceite aplicável.
- Formatação, lint e verificação de tipos sem erros.
- Cobertura mínima das linhas alteradas.
- Detecção de segredos e credenciais versionadas.
- Respeito às fronteiras arquiteturais e ausência de imports proibidos.
- Nenhum teste removido, enfraquecido ou ignorado para obter sucesso artificial.
- Nenhum stub não autorizado, `TODO` crítico ou tratamento vazio de exceções.
- Migrações de banco válidas e reversíveis conforme a política do projeto.
- Isolamento entre tenants e autorização por documento sem vazamentos conhecidos.

### Começam como warning e passam a bloquear após baseline representativo

- Cobertura total do projeto.
- Mutation score.
- Qualidade de retrieval em relação ao baseline BM25.
- Latência e custo por classe de consulta.
- Tamanho do bundle da interface web.

O primeiro benchmark representativo registra o baseline e o limite aprovado. Depois
disso, qualquer redução do limite exige ADR com evidências e aprovação explícita.

### Passam a bloquear quando a capacidade correspondente existir

- Scans de segurança da aplicação, dependências, imagens e infraestrutura.
- Cobertura e validade das citações.
- Avaliações do fluxo agentic e do roteamento.
- Acessibilidade e Web Vitals da interface.
- Exercícios de backup, restauração e reindexação.
- Testes de carga e validação dos SLOs operacionais.

## Métricas inicialmente observadas

| Métrica | Baseline | Direção |
| --- | --- | --- |
| Coverage total do projeto | Medir após o primeiro vertical slice | Não pode regredir |
| Mutation score | Medir por diff após existir lógica de domínio suficiente | Começa como warning; depois vira ratchet |
| Bundle size do frontend | Medir no primeiro preview | Não pode regredir sem justificativa |

Os limites de retrieval e agente são hipóteses iniciais. Se os dados mostrarem que outro limite representa melhor o produto, a mudança exige experimento registrado e ADR próprio.

## Política para definição dos limites

Adotamos uma política híbrida:

- Invariantes com padrão conhecido nascem fixos: zero erros de tipos, lint e
  formato; zero segredos; coverage das linhas alteradas >= 80%; zero acesso
  cruzado entre tenants; zero citação inexistente; e citation coverage >= 95%.
- Métricas dependentes do corpus, workload ou maturidade são medidas primeiro e
  depois congeladas como ratchets: coverage total, mutation score, métricas de
  retrieval, latência, custo, tamanho do bundle e capacidade.
- As metas iniciais de retrieval, fluxo agentic e operação são hipóteses de
  engenharia. O primeiro benchmark representativo deve confirmá-las ou propor
  novos valores por meio de evidências e ADR.
- Depois que um limite for confirmado, ele não pode regredir silenciosamente.
  Qualquer redução exige experimento reproduzível, ADR e aprovação explícita.

## Orçamentos operacionais iniciais

Até o primeiro benchmark em ambiente representativo, estes valores são metas de
projeto. Após validação ou revisão por ADR, tornam-se gates bloqueantes.

| Indicador | Meta inicial | Aplicação |
| --- | --- | --- |
| Retrieval p95 | <= 800 ms | ambiente equivalente a produção |
| Time-to-first-token p95 | <= 2,5 s | rota `Ask` com streaming |
| Resposta completa p95 | <= 10 s | rota `Ask` |
| Disponibilidade mensal | >= 99,9% | após início do piloto |
| Falha silenciosa de ingestão | 0 | todo erro termina observável ou em DLQ |
| RPO de metadados | <= 24 h | produção |
| RTO documentado e testado | <= 4 h | produção |

## Exceções

| ID | Regra | Escopo | Motivo | Responsável | Expira em |
| --- | --- | --- | --- | --- | --- |
| Nenhuma | - | - | - | - | - |
