# Por Dentro — Dashboard de Metas

Repositório de analytics acumulativo do projeto **Por Dentro** (@ingrydinparis / @ingrydlotierzo). Lê `insights.json` e mostra o dashboard em `index.html` — sem backend, sem build, sem login.

## Estrutura

```
por-dentro-analytics/
├── index.html            ← dashboard (abre via servidor local ou GitHub Pages)
├── insights.json         ← memória acumulada: metas + semanas + ciclos mensais
├── atualizar_semana.py   ← script opcional para adicionar check-ins semanais
└── README.md
```

## Como rodar localmente

O `index.html` lê `insights.json` via `fetch`, então precisa de um servidor local (não abre com duplo clique):

```
python3 -m http.server
```

Depois acesse `http://localhost:8000`.

## As 3 camadas do `insights.json`

**`metas`** — o objetivo. Seguidores (baseline, meta, prazo) e o piso de engajamento (calculado pela sua própria média móvel, não pelo benchmark do setor). O dashboard recalcula o ritmo necessário toda vez que é aberto, com base nos dias que realmente passaram.

**`semanas`** — o check-in rápido. Cada entrada é uma semana: alcance, interações, seguidores ganhos, engajamento, e uma nota livre. É essa camada que permite corrigir o calendário editorial toda semana, sem esperar o fechamento do mês. Semanas migradas de ciclos antigos têm `"conta_para_meta": false` (são só referência histórica); semanas novas devem ter `"conta_para_meta": true`.

**`ciclos`** — o bilan mensal, com a leitura estratégica completa (insights, ações, decisão editorial). Continua existindo — a camada semanal não substitui, complementa.

## Como adicionar uma semana

Opção 1 — script (recomendado, evita JSON quebrado):
```
python3 atualizar_semana.py
```
Responda as perguntas no terminal. O engajamento é calculado automaticamente.

Opção 2 — manual: copie o modelo mostrado na aba **Semanal** do dashboard, preencha os números e cole dentro do array `"semanas"` em `insights.json`. Sempre com `"conta_para_meta": true`.

## Como adicionar um novo ciclo mensal

Siga a estrutura de `ciclos` já existente em `insights.json`: nunca sobrescrever ciclos anteriores, sempre adicionar ao final do array. Ao fechar um novo mês com dados reais de Instagram, revise também `metas.engajamento.ciclos_considerados` e `metas.engajamento.piso_pct` — o piso deve virar a média dos últimos até 3 ciclos com dado real, menos a margem de tolerância.

## Metas atuais

- Seguidores: 985 → 10.000 em 365 dias (baseline 02/07/2026)
- Engajamento: piso de 11,1% (média própria de 15,06% menos 4 pontos de tolerância)
