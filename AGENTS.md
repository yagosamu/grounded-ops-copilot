# Instruções para agentes

Leia `CONSTRAINTS.md` antes de escrever código. Não enfraqueça o contrato de qualidade para fazer uma mudança passar.

O plano de execução está em `.specs/features/production-rag-platform/tasks.md`. Cada task inclui seus próprios testes e precisa passar pelo gate indicado antes de ser concluído. Execute `make pre-push` e registre o resultado antes de qualquer push solicitado pelo usuário.

## Diário técnico para entrevistas

Após concluir cada task, atualize o arquivo local `progress.md`. Ao fechar uma fase,
revise a seção da fase como um todo. Registre em português, de forma curta e natural:

- o que foi construído e em qual ordem;
- por que essa ordem e essas tecnologias foram escolhidas;
- o principal trade-off ou alternativa considerada;
- como explicar a decisão em uma entrevista.

Escreva para alguém que conhece desenvolvimento, mas está consolidando fundamentos
de AI Engineering. Preserve exemplos concretos e métricas dos gates, evitando logs,
detalhes de implementação e textos longos. `progress.md` é privado, está no
`.gitignore` e nunca entra em commits.

## Git workflow

- Crie um commit atômico somente depois que o gate da task passar.
- Use Conventional Commits em inglês, com mensagem curta, simples e profissional.
- Explique o motivo no corpo apenas quando ele não for evidente no título.
- Nunca execute `git push`. O usuário é responsável por todos os pushes.
- Antes de entregar commits para push, informe hashes, mensagens e o resultado exato de `make pre-push`.

## Model allocation

- Use um modelo mais rápido e econômico em tasks mecânicas e de baixa ambiguidade.
- Use um modelo de maior capacidade em arquitetura, domínio, segurança, retrieval, concorrência e integrações não triviais.
- O Verifier nunca usa o tier mais barato; validação adversarial exige capacidade intermediária ou alta.
- O modelo escolhido não altera testes, gates, critérios de aceite ou a exigência de commit atômico.
