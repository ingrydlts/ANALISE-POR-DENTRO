"""
Módulo 7 — Execução de Insights
Lê o ciclo mais recente do insights.json e cria tarefas no Notion
com PRIO e Do Date calculados automaticamente.

Configuração necessária (GitHub Secrets):
  NOTION_TOKEN  — token da integration do Notion
  NOTION_DB_ID  — ID do database "Major Tasks Database"
                  valor padrão: 1abd6022-54ce-81fa-8a36-000b754f10b3
"""

import json
import os
import sys
import requests
from datetime import date, timedelta


# ─── Configuração ─────────────────────────────────────────────────────────────

NOTION_TOKEN = os.environ["NOTION_TOKEN"]
NOTION_DB_ID = os.environ.get("NOTION_DB_ID", "1abd6022-54ce-81fa-8a36-000b754f10b3")

HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}

# Máximo de tarefas criadas por ciclo (regra do sistema)
MAX_TASKS = 5


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
    """Classifica uma ação em PRIO 1, 2 ou 3 por palavras-chave."""
    acao_lower = acao.lower()
    for kw in PRIO_1_KEYWORDS:
        if kw in acao_lower:
            return "1"
    for kw in PRIO_3_KEYWORDS:
        if kw in acao_lower:
            return "3"
    return "2"  # padrão: PRIO 2


# ─── Cálculo de datas ────────────────────────────────────────────────────────

def proxima_segunda(hoje: date) -> date:
    """Retorna a segunda-feira desta semana (ou hoje, se já for segunda)."""
    dias_ate_segunda = (7 - hoje.weekday()) % 7  # weekday: 0=segunda, 6=domingo
    if dias_ate_segunda == 0:
        return hoje
    # Se hoje é sáb (5) ou dom (6), vai para a próxima segunda
    if hoje.weekday() >= 5:
        return hoje + timedelta(days=(7 - hoje.weekday()))
    return hoje  # dias úteis: começa hoje mesmo


def calcular_do_date(prio: str, base: date) -> str:
    """
    Calcula o Do Date com base na PRIO e na data base (segunda da semana).
    PRIO 1 → segunda (base + 0)
    PRIO 2 → quarta  (base + 2)
    PRIO 3 → sexta   (base + 4)
    """
    offsets = {"1": 0, "2": 2, "3": 4}
    delta = offsets.get(prio, 2)
    return (base + timedelta(days=delta)).isoformat()


# ─── Verificação de duplicatas ───────────────────────────────────────────────

def ciclo_ja_processado(ciclo_id: str) -> bool:
    """
    Consulta o Notion para ver se já existem tarefas com este ciclo_id
    no campo AI summary. Evita criar tarefas duplicadas.
    """
    url = f"https://api.notion.com/v1/databases/{NOTION_DB_ID}/query"
    payload = {
        "filter": {
            "property": "AI summary",
            "rich_text": {
                "contains": f"[Ciclo: {ciclo_id}]"
            }
        },
        "page_size": 1
    }
    resp = requests.post(url, headers=HEADERS, json=payload)
    resp.raise_for_status()
    resultados = resp.json().get("results", [])
    return len(resultados) > 0


# ─── Criação de tarefa no Notion ─────────────────────────────────────────────

def criar_tarefa(task: str, prio: str, do_date: str, ai_summary: str) -> dict:
    """Cria uma página (tarefa) no database do Notion."""
    url = "https://api.notion.com/v1/pages"
    payload = {
        "parent": {"database_id": NOTION_DB_ID},
        "properties": {
            "Task": {
                "title": [{"text": {"content": task}}]
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
                "rich_text": [{"text": {"content": ai_summary}}]
            },
        }
    }
    resp = requests.post(url, headers=HEADERS, json=payload)
    resp.raise_for_status()
    return resp.json()


# ─── Formatação do título da tarefa ──────────────────────────────────────────

def formatar_titulo(acao: str) -> str:
    """
    Remove o sufixo '→ impacto esperado' se existir,
    e limita a 80 caracteres para caber bem no Notion.
    """
    if "→" in acao:
        acao = acao.split("→")[0].strip()
    return acao[:80]


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
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

    if not acoes:
        print(f"Ciclo {ciclo_id} não tem ações. Nada a criar.")
        sys.exit(0)

    print(f"\n📋 Processando ciclo: {periodo} ({ciclo_id})")
    print(f"   {len(acoes)} ação(ões) encontrada(s)")

    # 3. Verifica duplicatas
    if ciclo_ja_processado(ciclo_id):
        print(f"⏭️  Ciclo {ciclo_id} já foi processado. Pulando.")
        sys.exit(0)

    # 4. Limita ao máximo permitido e ordena por PRIO
    acoes_classificadas = []
    for acao in acoes:
        prio = classificar_prio(acao)
        acoes_classificadas.append((prio, acao))

    # ordena: PRIO 1 primeiro, depois 2, depois 3
    acoes_classificadas.sort(key=lambda x: x[0])
    acoes_classificadas = acoes_classificadas[:MAX_TASKS]

    # 5. Calcula base de datas (segunda desta semana)
    hoje = date.today()
    base = proxima_segunda(hoje)
    print(f"   Semana base: {base.isoformat()} (segunda-feira)\n")

    # 6. Cria as tarefas
    criadas = []
    for prio, acao in acoes_classificadas:
        do_date = calcular_do_date(prio, base)
        titulo = formatar_titulo(acao)
        ai_summary = f"[Ciclo: {ciclo_id}] {acao}"

        print(f"   ✅ PRIO {prio} | {do_date} | {titulo}")
        result = criar_tarefa(titulo, prio, do_date, ai_summary)
        criadas.append({
            "titulo": titulo,
            "prio": prio,
            "do_date": do_date,
            "url": result.get("url", "")
        })

    # 7. Resumo final
    print(f"\n🎯 {len(criadas)} tarefa(s) criada(s) no Notion para o ciclo {ciclo_id}.")
    for t in criadas:
        dia_semana = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"][
            date.fromisoformat(t["do_date"]).weekday()
        ]
        print(f"   [{dia_semana}] PRIO {t['prio']} — {t['titulo']}")

    print("\n✔️  Módulo 7 concluído.")


if __name__ == "__main__":
    main()
