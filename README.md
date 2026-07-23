# Por Dentro — Analytics Unificado

Repositório único de analytics do projeto **Por Dentro** (@ingrydlotierzo no YouTube / @ingrydinparis no Instagram). Reúne o que antes estava espalhado em dois repositórios:

- **Performance** (metas, check-ins semanais, ciclos mensais) — já vivia aqui.
- **Concorrência e Audiência** (scripts de coleta e análise via Notion + Claude) — migrado de `modulo3-Benchmark-Concorrencia-Audiencia`, que pode ser arquivado depois desta migração.

Mais duas seções novas que só existiam como conceito no documento de arquitetura: **Boas Práticas** (Módulo 4) e **Calendário Editorial** (Módulo 5).

Lê `insights.json` (+ `canais.json`) e mostra tudo em `index.html` — sem backend, sem build, sem login.

## Por que unificar

Os dois repositórios tinham, cada um, sua própria cópia de `insights.json`, desincronizadas entre si. Pior: o script de concorrência (`analyze_concorrencia.py`) nunca escrevia em nenhum `insights.json` — só na Notion — então a análise de concorrência nunca aparecia em lugar nenhum visível. Agora existe **um único `insights.json`**, e todo script que roda via GitHub Actions escreve nele.

## Estrutura

```
.
├── index.html                    ← dashboard (7 abas: Metas, Semanal, Performance, Concorrência, Audiência, Boas Práticas, Calendário)
├── painel-atualizacao.html       ← publica no GitHub sem terminal (cola um token, revisa, clica em Publicar)
├── insights.json                 ← memória acumulada: metas + semanas + ciclos + bilans_audiencia + concorrencia + boas_praticas + calendario + audiencia_personas
├── canais.json                   ← concorrentes monitorados (categorias A/B/C), exibido na aba Concorrência
├── dados/instagram/<data>/       ← CSVs de performance que a criadora sobe (gatilho por upload, não por agenda)
├── scripts/
│   ├── atualizar_semana.py           ← adiciona um check-in semanal (terminal)
│   ├── collect.py                    ← Módulo 3: coleta semanal de canais concorrentes via YouTube API → Notion
│   ├── analyze_concorrencia.py       ← Módulo 3: benchmark mensal de concorrência → Notion + insights.json
│   ├── analyze_audiencia.py          ← Módulo 3: bilan semanal de audiência → Notion + insights.json
│   ├── enriquecer_conversas_audiencia.py ← transcreve prints de DMs/comentários/concorrentes antes da análise
│   ├── gerar_sugestoes_conteudo.py   ← Módulo 5: sugere posts novos (audiência + concorrência + calendário) → Notion + insights.json
│   ├── processar_performance_instagram.py ← lê os CSVs de dados/instagram/ → insights.json["instagram_diario"]
│   ├── create_tasks.py               ← Módulo 7: cria tarefas no Notion a partir do ciclo mais recente
│   ├── requirements.txt
│   └── env.example
└── .github/workflows/
    ├── coleta-semanal.yml            ← sexta à noite
    ├── analise-audiencia.yml         ← sexta ~07h Paris
    ├── analise-concorrencia.yml      ← primeira segunda-feira do mês
    ├── sugestoes-conteudo.yml        ← segunda de manhã
    └── performance-instagram.yml     ← dispara quando dados/instagram/** muda (upload), não por agenda —
                                          processa os CSVs e encadeia audiência + concorrência + sugestões
```

> ⚠️ **Pendência conhecida:** o repositório original tinha um `.github/workflows/create-tasks.yml` para o Módulo 7, mas o conteúdo desse arquivo, ao migrar, revelou-se ser código Python (uma versão v2 de `create_tasks.py` com dedup por ação, mais recente que a que estava em `scripts/create_tasks.py`) — não um YAML de workflow válido. Aproveitei a versão v2 (é a melhor lógica) como o novo `scripts/create_tasks.py`, mas **não recriei o workflow** porque não sei o gatilho pretendido (push? cron?) e não quis adivinhar algo que dispara automações na sua Notion. Se você quiser o Módulo 7 automatizado, me diga quando rodar e eu escrevo o `.yml`.

## Como rodar o dashboard localmente

`index.html` lê `insights.json`/`canais.json` via `fetch`, então precisa de um servidor local (não abre com duplo clique):

```
python3 -m http.server
```

Depois acesse `http://localhost:8000`.

## As camadas do `insights.json`

| Chave | O que é | Quem escreve |
|---|---|---|
| `metas` | Objetivo de seguidores e piso de engajamento (recalculado toda vez que o dashboard abre) | manual, raramente muda |
| `semanas` | Check-in rápido semanal (alcance, interações, seguidores, engajamento) | `atualizar_semana.py` ou painel |
| `ciclos` | Bilan mensal completo (YouTube + Instagram + insights + ações + decisão editorial) | Cowork mensal + painel |
| `bilans_audiencia` | Bilan qualitativo semanal (perguntas recorrentes, dores, pautas sugeridas) | `analyze_audiencia.py` (automático, sexta) |
| `concorrencia` | Benchmark mensal de concorrência (o que funciona, lacunas, recomendação) | `analyze_concorrencia.py` (automático, 1ª segunda do mês) |
| `boas_praticas` | Síntese mensal — aplicar agora / testar / ignorar / padrões confirmados | manual, via painel |
| `calendario` | Calendário editorial mensal (tema, 4 semanas, formatos, datas sazonais) | manual, via painel |
| `calendario_posts` | Snapshot do calendário real do Notion (todo post com data marcada) — alimenta a grade "Calendário Real" no dashboard | `gerar_sugestoes_conteudo.py` (automático, segunda) |
| `sugestoes_conteudo` | Sugestões de posts (com fontes pesquisadas na web) da rodada mais recente — upsert por título, alimenta a seção "Sugestões de Conteúdo" na aba Calendário | `gerar_sugestoes_conteudo.py` (automático, segunda + a cada upload de performance) |
| `instagram_diario` | Série diária real (alcance, interações, visitas, cliques, seguidores ganhos) — alimenta "Instagram Diário" na aba Semanal | `processar_performance_instagram.py` (automático, ao subir CSV) |
| `audiencia_personas` | Personas fixas (P01–P04), jornada, princípio editorial — muda raramente | manual, via painel |

Regra inegociável (herdada do documento de arquitetura): **nada aqui é sobrescrito** — tudo é adicionado ao final do array correspondente, com upsert por `id` só quando é uma correção do mesmo período.

## Como publicar dados

### Sem terminal — `painel-atualizacao.html`

Abra no navegador, cole seu token do GitHub uma vez (fica salvo só no seu navegador via `localStorage`, nunca sai daqui além de falar direto com a API do GitHub):

1. **Passo 1** — configure owner/repo/branch/caminhos e teste a conexão.
2. **Passo 2–3** — ciclo mensal de performance: sobe CSV/JSON/HTML, revisa num formulário pré-preenchido, publica.
3. **Passo 4** — Concorrência / Boas Práticas / Calendário: cola um JSON no formato indicado (schema de exemplo já vem preenchido no campo), publica. Faz upsert por `id` — reprocessar o mesmo mês substitui, não duplica.
4. **Canais Monitorados** — edita `canais.json` inteiro (carregue a versão atual antes de editar, para não perder o que já está lá).

Gere o token em GitHub → Settings → Developer settings → Fine-grained tokens, com permissão **"Contents: Read and write"** só neste repositório.

### Automático — GitHub Actions

- `scripts/collect.py`: toda sexta à noite, coleta métricas de canais concorrentes (lidos da base Notion **COLETAS YOUTUBE**) via YouTube Data API.
- `scripts/analyze_audiencia.py`: toda sexta ~07h Paris, agrega DMs/comentários/stories enriquecidos da semana num bilan qualitativo — Notion + `insights.json`.
- `scripts/analyze_concorrencia.py`: primeira segunda-feira do mês, cruza as coletas de canais + observações de Instagram num benchmark mensal — Notion + `insights.json`.
- `scripts/gerar_sugestoes_conteudo.py`: toda segunda de manhã, cruza os bilans de audiência recentes + as ideias de conteúdo brutas por entrada (propriedade "Ideia de Conteúdo Gerada" em **Inputs Benchmark Instagram**, não só o resumo semanal) + o benchmark de concorrência + o que já está no calendário (evita duplicata) e propõe até 4 posts novos via Claude — já no tom de voz e nas regras de Reel/Carrossel da marca, com pesquisa na web (fontes reais citadas) quando o tema envolve fato verificável (burocracia, visto, imposto, mercado de trabalho). Cria uma página rascunho (Stage=Idea, sem data) por sugestão aceita em **INSTA TO POR DENTRO**, com a origem/justificativa/fontes registradas em "Promessa do conteudo". Grava também `sugestoes_conteudo` no `insights.json` (upsert por título) — aparece na seção "Sugestões de Conteúdo" da aba Calendário do dashboard, com as fontes como links clicáveis, mesmo se a criação da página no Notion falhar. Depois recarrega `calendario_posts` inteiro a partir do Notion — qualquer sugestão nova (ou edição manual feita durante a semana) aparece sozinha no Calendário Real do dashboard, sem sincronização manual.

  **Por que as sugestões nascem no Notion e não direto no site:** a API do Notion não libera CORS para chamadas vindas de um site diferente — não dá pra escrever nela direto do navegador (diferente do GitHub, que libera). Escrever a partir de um script (aqui, via GitHub Actions) é o único caminho direto; o dashboard só reflete o que já está no Notion.

- `scripts/processar_performance_instagram.py`: **gatilho por upload, não por agenda.** Roda toda vez que um CSV novo é adicionado em `dados/instagram/**` (pelo painel, aba "Performance Instagram", ou direto no GitHub). Processa os 6 CSVs que o Instagram exporta (Interações, Visitas, Visualizações, Cliques em link, Alcance, Novos seguidores — formato nativo da Meta, UTF-16), atualiza `instagram_diario` por data, e **encadeia na mesma execução** `enriquecer_conversas_audiencia.py` → `analyze_audiencia.py` → `analyze_concorrencia.py` → `gerar_sugestoes_conteudo.py`, com um único commit no final. Isso não substitui os agendamentos semanais/mensais das outras 3 automações — os dois gatilhos convivem: cron continua rodando sozinho, e cada upload de performance dispara uma rodada extra.

Todos precisam dos secrets listados em `scripts/env.example` configurados em GitHub → Settings → Secrets and variables → Actions.

**Como exportar os CSVs do Instagram:** app do Instagram → seu perfil → menu → **Configurações e atividade → Sua atividade → Estatísticas → Total → Exportar dados** (ou pelo gráfico de cada métrica na aba Insights, ícone de exportar). Repita para as 6 métricas: Interações, Visitas ao perfil, Visualizações, Cliques em um link, Alcance, Novos seguidores. Suba pelo painel quantas tiver — não precisa ser as 6 de uma vez, e o período pode se sobrepor com um upload anterior sem risco de duplicar.

### Manual — scripts locais

```
python3 scripts/atualizar_semana.py
```
Responde perguntas no terminal, calcula o engajamento automaticamente, grava dentro do array `semanas`.

## Canais monitorados (`canais.json`)

Três categorias (Módulo 3 do documento de arquitetura):

- **Categoria A — Diretos**: mesmo nicho, mesmo público (brasileiras na França).
- **Categoria B — Formato**: mesma lógica editorial (brasileiros em outros países europeus).
- **Categoria C — Qualidade**: mesmo nível de produção (criadores educativos explicativos).

Populado com os 8 canais reais mapeados manualmente (7 diretos + 1 de formato) — recuperados de notas que já existiam em FICHIERS INSTAGRAM no Notion mas nunca tinham sido sincronizadas pro dashboard.

## Metas atuais

- Seguidores: 985 → 10.000 em 365 dias (baseline 02/07/2026)
- Engajamento: piso de 11,1% (média própria de 15,06% menos 4 pontos de tolerância)

## Migração — o que mudou em relação aos dois repositórios antigos

- `modulo3-Benchmark-Concorrencia-Audiencia` pode ser arquivado — todos os scripts e workflows foram migrados para cá.
- `analyze_concorrencia.py` ganhou uma função nova (`salvar_no_insights_json`) que não existia antes — sem ela, a análise de concorrência nunca chegava ao dashboard.
- O `insights.json` deste repositório é a única fonte de verdade agora — não existe mais uma segunda cópia dessincronizada.
