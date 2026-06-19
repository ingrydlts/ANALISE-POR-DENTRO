"""
Módulo 7 — Execução de Insights
Lê o ciclo mais recente do insights.json e cria tarefas no Notion com:
- PRIO e Do Date calculados por urgência
- Propriedade Executor (🤖 Claude ou 🙋 Manual)
- Corpo da tarefa com contexto completo: insight de origem, impacto esperado e passos de execução
- Links para o bilan e para outras bases relevantes

Configuração necessária (GitHub Secrets):
  NOTION_TOKEN  — token da integration do Notion
  NOTION_DB_ID  — ID do database "Major Tasks Database"
                  valor padrão: 1abd6022-54ce-8137-b4d0-dca737cc669d
"""

import json
import os
import sys
import requests
from datetime import date, timedelta


# ─── Configuração ─────────────────────────────────────────────────────────────

NOTION_TOKEN = os.environ.get("NOTION_TOKEN")
NOTION_DB_ID = os.environ.get("NOTION_DB_ID", "1abd6022-54ce-8137-b4d0-dca737cc669d")

if not NOTION_TOKEN:
    print("ERRO: NOTION_TOKEN não encontrado. Configure em GitHub → Settings → Secrets.")
    sys.exit(1)
if not NOTION_DB_ID:
    print("ERRO: NOTION_DB_ID não encontrado. Configure em GitHub → Settings → Secrets.")
    sys.exit(1)

HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}

MAX_TASKS = 5

# ─── Links fixos do sistema Por Dentro ───────────────────────────────────────
# Atualize estes links conforme as páginas do seu Notion

LINKS_SISTEMA = {
    # ✅ Preenchidos por Claude (já conhecidos)
    "tarefas":      "https://app.notion.com/p/1abd602254ce8137b4d0dca737cc669d",
    "workspace":    "https://app.notion.com/p/topordentroviagens",

    # ✏️ Preencha você — cole o link da página Notion correspondente
    "competitivo":  "",   # Inteligência Competitiva / benchmark
    "calendario":   "",   # Calendário Editorial
    "boas_praticas": "",  # Boas Práticas / o que funcionou
    "dashboard":    "",   # GitHub Pages do dashboard de analytics
}


# ─── Diagnóstico de acesso ───────────────────────────────────────────────────

def verificar_acesso_database():
    url = f"https://api.notion.com/v1/databases/{NOTION_DB_ID}"
    resp = requests.get(url, headers=HEADERS)
    if resp.status_code != 200:
        print(f"ERRO: Não foi possível acessar o database '{NOTION_DB_ID}'")
        print(f"Status: {resp.status_code} — {resp.text}")
        sys.exit(1)
    db = resp.json()
    titulo = db.get("title", [{}])
    nome = titulo[0].get("plain_text", "sem título") if titulo else "sem título"
    print(f"✅ Database acessado: '{nome}'")


# ─── Setup da propriedade Executor ───────────────────────────────────────────

def garantir_propriedade_executor():
    """
    Adiciona a propriedade 'Executor' ao database se ainda não existir.
    Executor = select com opções '🤖 Claude' e '🙋 Manual'.
    """
    url = f"https://api.notion.com/v1/databases/{NOTION_DB_ID}"
    resp = requests.get(url, headers=HEADERS)
    resp.raise_for_status()
    props = resp.json().get("properties", {})

    if "Executor" in props:
        print("✅ Propriedade 'Executor' já existe.")
        return

    print("   Adicionando propriedade 'Executor' ao database...")
    patch_url = f"https://api.notion.com/v1/databases/{NOTION_DB_ID}"
    payload = {
        "properties": {
            "Executor": {
                "select": {
                    "options": [
                        {"name": "🤖 Claude", "color": "blue"},
                        {"name": "🙋 Manual", "color": "yellow"},
                    ]
                }
            }
        }
    }
    r = requests.patch(patch_url, headers=HEADERS, json=payload)
    if r.ok:
        print("✅ Propriedade 'Executor' criada.")
    else:
        print(f"⚠️  Não foi possível criar 'Executor': {r.status_code} — {r.text}")


# ─── Classificação por tipo de ação → PRIO ───────────────────────────────────

PRIO_1_KEYWORDS = [
    "gravar", "vídeo", "video", "roteiro", "série", "serie",
    "decisão", "decisao", "editorial", "posicionamento",
    "estratégia", "estrategia", "publicar", "lançar", "lancar",
]

PRIO_3_KEYWORDS = [
    "testar", "teste", "experimento", "experimentar",
    "hipótese", "hipotese", "validar", "tentar",
]


def classificar_prio(acao: str) -> str:
    acao_lower = acao.lower()
    for kw in PRIO_1_KEYWORDS:
        if kw in acao_lower:
            return "1"
    for kw in PRIO_3_KEYWORDS:
        if kw in acao_lower:
            return "3"
    return "2"


# ─── Classificação de Executor ────────────────────────────────────────────────

# Ações que Claude consegue executar diretamente em sessão Cowork
CLAUDE_KEYWORDS = [
    "analisar", "análise", "analise", "benchmark", "concorrência", "concorrencia",
    "pesquisar", "pesquisa", "roteiro", "rascunho", "pauta", "calendário",
    "calendario", "legenda", "newsletter", "monitorar", "monitoramento",
    "mapear", "comparar", "identificar", "gerar", "criar texto", "escrever",
    "estruturar", "organizar", "planejar",
]

# Ações que exigem execução física/manual sua
MANUAL_KEYWORDS = [
    "gravar", "filmar", "editar", "edição", "publicar", "postar", "upload",
    "thumbnail", "design", "canva", "reel", "cortar", "mixar", "revisar",
    "gravar áudio", "gravar vídeo",
]


def determinar_executor(acao: str) -> str:
    acao_lower = acao.lower()
    for kw in CLAUDE_KEYWORDS:
        if kw in acao_lower:
            return "🤖 Claude"
    for kw in MANUAL_KEYWORDS:
        if kw in acao_lower:
            return "🙋 Manual"
    return "🙋 Manual"  # padrão conservador


# ─── Extração de impacto e passos ────────────────────────────────────────────

def extrair_impacto(acao: str) -> str:
    """Extrai a parte após '→' como impacto esperado."""
    if "→" in acao:
        return acao.split("→", 1)[1].strip()
    return ""


def formatar_titulo(acao: str) -> str:
    if "→" in acao:
        acao = acao.split("→")[0].strip()
    return acao[:80]


def gerar_passos_execucao(prio: str, executor: str, tipo_acao: str) -> str:
    """Gera orientação de execução contextualizada."""
    if executor == "🤖 Claude":
        if "calendário" in tipo_acao.lower() or "calendario" in tipo_acao.lower():
            return "Abra uma sessão Cowork → ative a skill por-dentro-calendario → cole o insight → gere o calendário."
        if "roteiro" in tipo_acao.lower():
            return "Abra uma sessão Cowork → ative a skill por-dentro-pauta → informe o tema → Claude gera o roteiro completo."
        if "análise" in tipo_acao.lower() or "analise" in tipo_acao.lower() or "benchmark" in tipo_acao.lower():
            return "Abra uma sessão Cowork → ative a skill por-dentro-analytics ou por-dentro-concorrencia → Cole os dados ou informe o canal → Claude gera a análise."
        return "Abra uma sessão Cowork → descreva o que precisa → Claude executa diretamente."
    else:
        if prio == "1":
            return "Alta prioridade — execute ainda nesta semana. Consulte o Calendário Editorial para encaixar na produção."
        if prio == "3":
            return "Teste controlado — defina a métrica de sucesso antes de executar. Avalie o resultado após 7 dias."
        return "Execute conforme sua disponibilidade esta semana. Atualize o Status para 'In progress' ao começar."


# ─── Cálculo de datas ────────────────────────────────────────────────────────

def proxima_segunda(hoje: date) -> date:
    if hoje.weekday() == 0:
        return hoje
    if hoje.weekday() >= 5:
        return hoje + timedelta(days=(7 - hoje.weekday()))
    return hoje


def calcular_do_date(prio: str, base: date) -> str:
    offsets = {"1": 0, "2": 2, "3": 4}
    return (base + timedelta(days=offsets.get(prio, 2))).isoformat()


# ─── Verificação de duplicatas ───────────────────────────────────────────────

def ciclo_ja_processado(ciclo_id: str) -> bool:
    url = f"https://api.notion.com/v1/databases/{NOTION_DB_ID}/query"
    payload = {
        "filter": {
            "property": "AI summary",
            "rich_text": {"contains": f"[Ciclo: {ciclo_id}]"}
        },
        "page_size": 1
    }
    resp = requests.post(url, headers=HEADERS, json=payload)
    if not resp.ok:
        print(f"ERRO ao checar duplicatas: {resp.status_code} — {resp.text}")
        resp.raise_for_status()
    return len(resp.json().get("results", [])) > 0


# ─── Construção do corpo da página (blocos de conteúdo) ──────────────────────

def rich(text: str, bold: bool = False) -> dict:
    block = {"text": {"content": text}}
    if bold:
        block["annotations"] = {"bold": True}
    return block


def bloco_paragrafo(texto: str, bold: bool = False) -> dict:
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": [rich(texto, bold)]}
    }


def bloco_heading(texto: str, level: int = 3) -> dict:
    tipo = f"heading_{level}"
    return {
        "object": "block",
        "type": tipo,
        tipo: {"rich_text": [rich(texto)]}
    }


def bloco_divisor() -> dict:
    return {"object": "block", "type": "divider", "divider": {}}


def bloco_callout(texto: str, emoji: str = "💡") -> dict:
    return {
        "object": "block",
        "type": "callout",
        "callout": {
            "rich_text": [rich(texto)],
            "icon": {"type": "emoji", "emoji": emoji}
        }
    }


def bloco_bullet(texto: str) -> dict:
    return {
        "object": "block",
        "type": "bulleted_list_item",
        "bulleted_list_item": {"rich_text": [rich(texto)]}
    }


def construir_conteudo_pagina(
    acao: str,
    ciclo_id: str,
    periodo: str,
    insights: list,
    executor: str,
    prio: str,
    impacto: str,
) -> list:
    """Monta os blocos de conteúdo da página no Notion."""
    blocos = []

    # Contexto do ciclo
    blocos.append(bloco_callout(f"Gerado automaticamente pelo Módulo 7 — Ciclo {periodo} ({ciclo_id})", "🔄"))
    blocos.append(bloco_divisor())

    # Ação completa
    blocos.append(bloco_heading("🎯 Ação", 3))
    blocos.append(bloco_paragrafo(acao))

    # Impacto esperado
    if impacto:
        blocos.append(bloco_heading("📈 Impacto esperado", 3))
        blocos.append(bloco_paragrafo(impacto))

    blocos.append(bloco_divisor())

    # Insights do ciclo que deram origem a esta ação
    if insights:
        blocos.append(bloco_heading("💡 Insights do ciclo", 3))
        for insight in insights:
            blocos.append(bloco_bullet(insight))

    blocos.append(bloco_divisor())

    # Como executar
    passos = gerar_passos_execucao(prio, executor, acao)
    blocos.append(bloco_heading("⚡ Como executar", 3))
    blocos.append(bloco_callout(passos, "🤖" if executor == "🤖 Claude" else "🙋"))

    # Links do sistema
    links_uteis = {k: v for k, v in LINKS_SISTEMA.items() if v}
    if links_uteis:
        blocos.append(bloco_divisor())
        blocos.append(bloco_heading("🔗 Links do sistema", 3))
        nomes = {
            "analytics": "Analytics Por Dentro",
            "competitivo": "Inteligência Competitiva",
            "boas_praticas": "Boas Práticas",
            "calendario": "Calendário Editorial",
            "dashboard": "Dashboard GitHub Pages",
        }
        for chave, url in links_uteis.items():
            blocos.append(bloco_bullet(f"{nomes.get(chave, chave)}: {url}"))

    return blocos


# ─── Criação de tarefa no Notion ─────────────────────────────────────────────

def criar_tarefa(
    titulo: str,
    prio: str,
    do_date: str,
    ai_summary: str,
    executor: str,
    conteudo: list,
) -> dict:
    payload = {
        "parent": {"database_id": NOTION_DB_ID},
        "properties": {
            "Task": {
                "title": [{"text": {"content": titulo}}]
            },
            "Status": {
                "status": {"name": "To-do"}
            },
            "PRIO": {
                "select": {"name": prio}
            },
            "Do Date": {
                "date": {"start": do_date}
            },
            "AI summary": {
                "rich_text": [{"text": {"content": ai_summary[:2000]}}]
            },
            "Executor": {
                "select": {"name": executor}
            },
        },
        "children": conteudo,
    }
    resp = requests.post("https://api.notion.com/v1/pages", headers=HEADERS, json=payload)
    if not resp.ok:
        print(f"ERRO ao criar tarefa '{titulo}': {resp.status_code} — {resp.text}")
        resp.raise_for_status()
    return resp.json()


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    # 0. Diagnóstico e setup
    verificar_acesso_database()
    garantir_propriedade_executor()

    # 1. Lê o insights.json
    with open("insights.json", "r", encoding="utf-8") as f:
        data = json.load(f)

    ciclos = data.get("ciclos", [])
    if not ciclos:
        print("insights.json não tem ciclos. Nada a fazer.")
        sys.exit(0)

    # 2. Pega o ciclo mais recente
    ciclo = ciclos[-1]
    ciclo_id = ciclo.get("id", "")
    periodo = ciclo.get("periodo", ciclo_id)
    acoes = ciclo.get("acoes_proximo_ciclo", [])
    insights = ciclo.get("insights", [])

    if not acoes:
        print(f"Ciclo {ciclo_id} não tem ações. Nada a criar.")
        sys.exit(0)

    print(f"\n📋 Ciclo: {periodo} ({ciclo_id})")
    print(f"   {len(acoes)} ação(ões) | {len(insights)} insight(s)\n")

    # 3. Verifica duplicatas
    if ciclo_ja_processado(ciclo_id):
        print(f"⏭️  Ciclo {ciclo_id} já processado. Pulando.")
        sys.exit(0)

    # 4. Classifica e limita
    acoes_classificadas = []
    for acao in acoes:
        prio = classificar_prio(acao)
        executor = determinar_executor(acao)
        acoes_classificadas.append((prio, executor, acao))

    acoes_classificadas.sort(key=lambda x: x[0])
    acoes_classificadas = acoes_classificadas[:MAX_TASKS]

    # 5. Datas
    hoje = date.today()
    base = proxima_segunda(hoje)
    print(f"   Semana base: {base.isoformat()} (segunda-feira)\n")

    # 6. Cria as tarefas
    criadas = []
    for prio, executor, acao in acoes_classificadas:
        do_date = calcular_do_date(prio, base)
        titulo = formatar_titulo(acao)
        impacto = extrair_impacto(acao)
        ai_summary = f"[Ciclo: {ciclo_id}] {acao}"

        conteudo = construir_conteudo_pagina(
            acao=acao,
            ciclo_id=ciclo_id,
            periodo=periodo,
            insights=insights,
            executor=executor,
            prio=prio,
            impacto=impacto,
        )

        print(f"   {'🤖' if 'Claude' in executor else '🙋'} PRIO {prio} | {do_date} | {titulo}")
        result = criar_tarefa(titulo, prio, do_date, ai_summary, executor, conteudo)
        criadas.append({
            "titulo": titulo,
            "prio": prio,
            "executor": executor,
            "do_date": do_date,
            "url": result.get("url", "")
        })

    # 7. Resumo
    print(f"\n🎯 {len(criadas)} tarefa(s) criada(s) no Notion — ciclo {ciclo_id}")
    for t in criadas:
        dia = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"][
            date.fromisoformat(t["do_date"]).weekday()
        ]
        print(f"   [{dia}] {t['executor']} PRIO {t['prio']} — {t['titulo']}")

    print("\n✔️  Módulo 7 concluído.")


if __name__ == "__main__":
    main()
