"""
MÓDULO 3 — Análise de Audiência
Roda toda SEXTA-FEIRA às 07:00 (horário de Paris) via GitHub Actions —
antes do script de busca de fontes, para que a busca de conteúdo já
leve em conta os insights de audiência mais recentes.

IMPORTANTE: este script roda DEPOIS de enriquecer_conversas_audiencia.py.
Aquele script lê os prints (screenshots) de cada entrada com visão da Claude
e preenche "Texte" + os campos qualitativos por entrada, marcando
STATUS="Analisado". Este script aqui só agrega entradas que JÁ passaram por
esse enriquecimento (STATUS="Analisado") — ele não lê imagens.

Fluxo:
1. Busca entradas AUDIÊNCIA + Analisado do Notion (já enriquecidas por
   enriquecer_conversas_audiencia.py — texto já transcrito)
2. Organiza por plataforma (YouTube / Instagram)
3. Envia para Claude, que devolve um JSON estruturado de análise de audiência
4. Salva a análise em duas frentes:
   a) Notion — uma LINHA na base "📊 Análises de Audiência (Semanal)"
      (não mais uma subpágina solta: reprocessar a mesma semana atualiza
      a linha existente em vez de duplicar, usando "ID Semana" como chave)
   b) insights.json — bloco estruturado em "bilans_audiencia", para o
      dashboard (index.html) exibir na aba Detalhes → Bilan Qualitativo
      de Audiência
5. Marca as entradas usadas como PROCESSADO

Variáveis de ambiente esperadas:
  NOTION_TOKEN, ANTHROPIC_API_KEY, NOTION_DB_IG, NOTION_ANALISES_DB_ID
  INSIGHTS_JSON_PATH (opcional — caminho do insights.json no repo, default "insights.json")

  Notas de nomenclatura das secrets:
  - NOTION_DB_IG: database "🗨️ CONVERSAS AUDIÊNCIA (Instagram)" — de onde vêm as
    entradas cruas (DMs/comentários/stories). Não se chama NOTION_DB_ID porque
    esse nome já estava em uso por outro módulo/database no mesmo repositório.
  - NOTION_ANALISES_DB_ID: database "📊 Análises de Audiência (Semanal)" — para
    onde vai o bilan estruturado desta análise (uma linha por semana).
"""

import os
import json
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv
from notion_client import Client as NotionClient
from notion_client.errors import APIResponseError
import anthropic

load_dotenv()

# ── Configuração ──────────────────────────────────────────────────────────────
NOTION_TOKEN       = os.environ["NOTION_TOKEN"]
ANTHROPIC_API_KEY  = os.environ["ANTHROPIC_API_KEY"]
NOTION_DB_ID       = os.environ["NOTION_DB_IG"]           # Conversas Audiência (Instagram)
NOTION_ANALISES_DB_ID = os.environ["NOTION_ANALISES_DB_ID"]  # Análises de Audiência (Semanal)
INSIGHTS_JSON_PATH = os.environ.get("INSIGHTS_JSON_PATH", "insights.json")

notion = NotionClient(auth=NOTION_TOKEN)
claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

AGORA        = datetime.now(timezone.utc)
SEMANA_ID    = AGORA.strftime("%G-W%V")       # ex: 2026-W27 (ISO week, estável mesmo virando o mês)
SEMANA_LABEL = AGORA.strftime("%d/%m/%Y")

_data_source_cache = {}


# ── Busca de dados ────────────────────────────────────────────────────────────

def resolver_data_source_id(database_id: str) -> str:
    """
    Resolve o data_source_id atual de um database (API Notion 2025-09-03+).

    Desde essa versão da API, bancos de dados passaram a ter uma camada
    intermediária de "data sources", e criar/consultar páginas dentro de um
    database passa a exigir o data_source_id (em vez do database_id direto).
    Resolvemos dinamicamente a cada execução em vez de fixar numa secret
    separada, para não quebrar se o database for reestruturado.
    """
    if database_id in _data_source_cache:
        return _data_source_cache[database_id]

    db = notion.databases.retrieve(database_id=database_id)
    data_sources = db.get("data_sources", [])
    if not data_sources:
        raise RuntimeError(
            f"O database {database_id} não retornou nenhum data_source. "
            "Confirme se o ID é o de um database (não de uma página comum)."
        )
    data_source_id = data_sources[0]["id"]
    _data_source_cache[database_id] = data_source_id
    return data_source_id


def buscar_entradas_audiencia() -> list:
    """
    Busca entradas AUDIÊNCIA já enriquecidas (STATUS="Analisado") no banco
    Notion. O enriquecimento por entrada (transcrição do print + dor/insight/
    etc.) é feito antes, por enriquecer_conversas_audiencia.py.
    """
    data_source_id = resolver_data_source_id(NOTION_DB_ID)
    resp = notion.data_sources.query(
        data_source_id=data_source_id,
        filter={
            "and": [
                {"property": "CATEGORIA", "select": {"equals": "AUDIÊNCIA"}},
                {"property": "STATUS",    "select": {"equals": "Analisado"}}
            ]
        }
    )

    entradas = []
    for page in resp.get("results", []):
        nome_prop = page["properties"].get("Name", {}).get("title", [])
        nome = nome_prop[0]["text"]["content"] if nome_prop else "sem título"

        texto_prop = page["properties"].get("Texte", {}).get("rich_text", [])
        texto = "".join(rt.get("text", {}).get("content", "") for rt in texto_prop)

        plataforma_prop = page["properties"].get("PLATAFORMA", {}).get("select")
        plataforma = plataforma_prop["name"] if plataforma_prop else "DESCONHECIDA"

        entradas.append({
            "id":         page["id"],
            "nome":       nome,
            "texto":      texto,
            "plataforma": plataforma
        })

    return entradas


def formatar_entradas(entradas: list) -> str:
    """Organiza entradas por plataforma para o prompt."""
    youtube   = [e for e in entradas if e["plataforma"] == "YOUTUBE"]
    instagram = [e for e in entradas if e["plataforma"] == "INSTAGRAM"]
    outro     = [e for e in entradas if e["plataforma"] not in ("YOUTUBE", "INSTAGRAM")]

    partes = []

    if youtube:
        bloco = "### YouTube\n" + "\n\n".join(
            f"**{e['nome']}**\n{e['texto']}" for e in youtube
        )
        partes.append(bloco)

    if instagram:
        bloco = "### Instagram\n" + "\n\n".join(
            f"**{e['nome']}**\n{e['texto']}" for e in instagram
        )
        partes.append(bloco)

    if outro:
        bloco = "### Outros\n" + "\n\n".join(
            f"**{e['nome']}** [{e['plataforma']}]\n{e['texto']}" for e in outro
        )
        partes.append(bloco)

    return "\n\n---\n\n".join(partes) if partes else "Nenhuma entrada de audiência disponível."


# ── Análise Claude ────────────────────────────────────────────────────────────

SCHEMA_JSON = """{
  "perguntas_recorrentes": ["string"],
  "dores_nao_atendidas": ["string"],
  "pedidos_conteudo": ["string"],
  "perfil_momento": "string",
  "diferenca_plataformas": "string",
  "pautas_sugeridas": [{"plataforma": "YOUTUBE|INSTAGRAM", "titulo": "string", "porque": "string"}],
  "temas_audiencia": ["string"],
  "segmentos": {"brasil": 0, "processo": 0, "franca": 0}
}"""


def analisar_com_claude(dados_audiencia: str, total_entradas: int) -> dict:
    prompt = f"""Você é o sistema editorial do canal Por Dentro — canal de uma imigrante brasileira na França que explica como a França realmente funciona: trabalho, saúde, burocracia, moradia, cultura.

Posicionamento: observador, lúcido, educativo. O canal é 75% Instagram hoje e tem crescimento forte nessa plataforma.

Analise os dados de audiência abaixo (comentários, DMs e respostas de stories coletados desde a última rodada) e gere a ANÁLISE DE AUDIÊNCIA da semana de {SEMANA_LABEL}.

## DADOS DA AUDIÊNCIA ({total_entradas} entradas)
{dados_audiencia}

---

Responda APENAS com um JSON válido (sem markdown, sem cercas de código, sem texto fora do JSON) no formato exato abaixo. Use as palavras exatas da audiência sempre que possível. Se não houver dados suficientes para preencher um campo, use lista vazia ou string vazia — nunca invente.

{SCHEMA_JSON}

Onde:
- perguntas_recorrentes: dúvidas que aparecem mais de uma vez — essas viram pauta prioritária
- dores_nao_atendidas: o que a audiência precisa que o canal ainda não respondeu bem ou não cobriu
- pedidos_conteudo: quando alguém pediu diretamente um tema, formato ou continuação
- perfil_momento: quem está engajando agora (recém-chegado, planejando imigrar, já estabelecido) e em que fase de vida
- diferenca_plataformas: o que a audiência do YouTube quer vs. o que a audiência do Instagram quer, se houver diferença relevante
- pautas_sugeridas: 3 ideias concretas de conteúdo com ângulo específico que saem diretamente desses dados
- temas_audiencia: até 6 palavras/temas-chave recorrentes nos dados (vira tag no dashboard)
- segmentos: contagem aproximada de quantas entradas vêm de cada estágio — brasil (ainda no Brasil, pesquisando), processo (em processo de imigração/burocracia), franca (já vivendo na França)"""

    resp = claude.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=2500,
        messages=[{"role": "user", "content": prompt}]
    )
    texto = resp.content[0].text.strip()

    # Blindagem: se o modelo insistir em cercar o JSON com ```, remove.
    if texto.startswith("```"):
        texto = texto.strip("`")
        if texto.lower().startswith("json"):
            texto = texto[4:]
        texto = texto.strip()

    try:
        return json.loads(texto)
    except json.JSONDecodeError:
        # Fallback: não perde a análise, só marca que não veio estruturada.
        return {"erro_parse": True, "texto_bruto": texto}


# ── Salvar na base "📊 Análises de Audiência (Semanal)" ──────────────────────

def _erro_e_de_bloco_arquivado(exc: Exception) -> bool:
    return "archived" in str(exc).lower()


def _texto_lista(itens: list) -> str:
    if not itens:
        return "— nenhum registro nesta rodada —"
    return "\n".join(f"• {item}" for item in itens)


def _texto_pautas(pautas: list) -> str:
    if not pautas:
        return "— nenhuma pauta sugerida nesta rodada —"
    linhas = []
    for p in pautas:
        linhas.append(f"[{p.get('plataforma', '')}] {p.get('titulo', '')} — {p.get('porque', '')}")
    return "\n".join(linhas)


def _rt(texto: str) -> dict:
    """Propriedade rich_text no formato bruto da API Notion (max 2000 chars)."""
    return {"rich_text": [{"text": {"content": (texto or "")[:2000]}}]}


def _title(texto: str) -> dict:
    return {"title": [{"text": {"content": texto}}]}


def _montar_properties(bilan: dict, total_entradas: int, titulo: str) -> dict:
    erro = bilan.get("erro_parse", False)
    seg  = {} if erro else bilan.get("segmentos", {}) or {}
    temas = [] if erro else (bilan.get("temas_audiencia", []) or [])

    properties = {
        "Semana":                     _title(titulo),
        "ID Semana":                  _rt(SEMANA_ID),
        "Data":                       {"date": {"start": AGORA.strftime("%Y-%m-%d")}},
        "Total Entradas Analisadas":  {"number": total_entradas},
        "Status":                     {"select": {"name": "Novo"}},
        "Temas Audiência":            {"multi_select": [{"name": t} for t in temas]},
    }

    if erro:
        properties["Perguntas Recorrentes"] = _rt(bilan.get("texto_bruto", ""))
        properties["Dores Não Atendidas"] = _rt("")
        properties["Pedidos de Conteúdo"] = _rt("")
        properties["Perfil do Momento"] = _rt("")
        properties["Diferença Entre Plataformas"] = _rt("")
        properties["Pautas Sugeridas"] = _rt("")
        properties["Segmento Brasil"] = {"number": 0}
        properties["Segmento Processo"] = {"number": 0}
        properties["Segmento França"] = {"number": 0}
    else:
        properties["Perguntas Recorrentes"]       = _rt(_texto_lista(bilan.get("perguntas_recorrentes", [])))
        properties["Dores Não Atendidas"]         = _rt(_texto_lista(bilan.get("dores_nao_atendidas", [])))
        properties["Pedidos de Conteúdo"]         = _rt(_texto_lista(bilan.get("pedidos_conteudo", [])))
        properties["Perfil do Momento"]           = _rt(bilan.get("perfil_momento", ""))
        properties["Diferença Entre Plataformas"] = _rt(bilan.get("diferenca_plataformas", ""))
        properties["Pautas Sugeridas"]            = _rt(_texto_pautas(bilan.get("pautas_sugeridas", [])))
        properties["Segmento Brasil"]              = {"number": seg.get("brasil", 0) or 0}
        properties["Segmento Processo"]            = {"number": seg.get("processo", 0) or 0}
        properties["Segmento França"]              = {"number": seg.get("franca", 0) or 0}

    return properties


def _buscar_linha_semana_existente(data_source_id: str):
    """Procura uma linha já existente para SEMANA_ID, para atualizar em vez de duplicar."""
    resp = notion.data_sources.query(
        data_source_id=data_source_id,
        filter={"property": "ID Semana", "rich_text": {"equals": SEMANA_ID}}
    )
    resultados = resp.get("results", [])
    return resultados[0]["id"] if resultados else None


def salvar_analise_na_base(bilan: dict, total_entradas: int):
    """
    Salva o bilan como uma LINHA na base "📊 Análises de Audiência (Semanal)".
    Reprocessar a mesma semana ATUALIZA a linha existente (chave "ID Semana"),
    em vez de criar uma nova — sem duplicatas mesmo rodando o job mais de uma
    vez na mesma semana (ex.: reexecução manual após um erro).
    """
    titulo = f"Semana de {SEMANA_LABEL}"
    properties = _montar_properties(bilan, total_entradas, titulo)

    try:
        data_source_id = resolver_data_source_id(NOTION_ANALISES_DB_ID)
        existente = _buscar_linha_semana_existente(data_source_id)

        if existente:
            notion.pages.update(page_id=existente, properties=properties)
            print(f"  ✓ Linha atualizada na base de Análises: {titulo}")
        else:
            notion.pages.create(
                parent={"data_source_id": data_source_id},
                properties=properties
            )
            print(f"  ✓ Linha criada na base de Análises: {titulo}")
    except APIResponseError as e:
        if _erro_e_de_bloco_arquivado(e):
            print(
                "  ⚠ A base NOTION_ANALISES_DB_ID (ou a linha da semana) está arquivada no Notion. "
                "Abra '📊 Análises de Audiência (Semanal)' e restaure — pulando salvar no Notion "
                "nesta rodada (insights.json e marcação PROCESSADO seguem normalmente)."
            )
            return
        raise


# ── Salvar no insights.json (alimenta o dashboard) ───────────────────────────

def salvar_bilan_no_insights_json(bilan: dict, total_entradas: int):
    path = Path(INSIGHTS_JSON_PATH)

    if not path.exists():
        # Bootstrap defensivo: se o arquivo nunca foi commitado no repositório
        # (checkout "limpo"), criamos um esqueleto mínimo em vez de só pular —
        # assim o passo de commit do workflow sempre tem algo válido para
        # versionar. Isso NÃO substitui o insights.json "de verdade" (com
        # metas/semanas/ciclos do dashboard) — se esse arquivo existe só na
        # sua máquina, comite-o uma vez na raiz do repositório para que o
        # dashboard mostre o histórico completo, não só os bilans de audiência.
        print(f"  ⚠ {INSIGHTS_JSON_PATH} não existe no repositório — criando esqueleto mínimo.")
        data = {"bilans_audiencia": []}
    else:
        data = json.loads(path.read_text(encoding="utf-8"))
        data.setdefault("bilans_audiencia", [])

    if bilan.get("erro_parse"):
        print("  ⚠ Claude não retornou JSON válido — dashboard não atualizado nesta rodada (ver base no Notion).")
        return

    entrada = {
        "id": SEMANA_ID,
        "data": AGORA.strftime("%Y-%m-%d"),
        "periodo_label": f"Semana de {SEMANA_LABEL}",
        "total_entradas_analisadas": total_entradas,
        "perguntas_recorrentes": bilan.get("perguntas_recorrentes", []),
        "dores_nao_atendidas": bilan.get("dores_nao_atendidas", []),
        "pedidos_conteudo": bilan.get("pedidos_conteudo", []),
        "perfil_momento": bilan.get("perfil_momento", ""),
        "diferenca_plataformas": bilan.get("diferenca_plataformas", ""),
        "pautas_sugeridas": bilan.get("pautas_sugeridas", []),
        "temas_audiencia": bilan.get("temas_audiencia", []),
        "segmentos": bilan.get("segmentos", {}),
    }

    # Reprocessar a mesma semana substitui a entrada anterior em vez de duplicar.
    data["bilans_audiencia"] = [b for b in data["bilans_audiencia"] if b.get("id") != SEMANA_ID]
    data["bilans_audiencia"].append(entrada)

    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  ✓ Atualizado {INSIGHTS_JSON_PATH} (bilans_audiencia: {len(data['bilans_audiencia'])} rodada(s))")


def marcar_processado(page_id: str):
    try:
        notion.pages.update(
            page_id=page_id,
            properties={"STATUS": {"select": {"name": "PROCESSADO"}}}
        )
    except APIResponseError as e:
        if _erro_e_de_bloco_arquivado(e):
            # Acontece quando a entrada foi movida pra lixeira no Notion entre a
            # busca e esta etapa (ex.: teste manual arquivado). Não deve derrubar
            # o job inteiro — a análise e o insights.json já foram salvos antes.
            print(f"  ⚠ Página {page_id} está arquivada (lixeira) no Notion — pulando, não foi possível marcar PROCESSADO.")
        else:
            raise


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"\n=== Análise de Audiência — Semana de {SEMANA_LABEL} ===\n")

    print("Buscando entradas de audiência...")
    entradas = buscar_entradas_audiencia()

    if not entradas:
        print("Nenhuma entrada de audiência para processar.")
        return

    print(f"{len(entradas)} entrada(s) encontrada(s).")

    dados_formatados = formatar_entradas(entradas)

    print("Enviando para Claude...")
    bilan = analisar_com_claude(dados_formatados, len(entradas))

    print("Salvando na base de Análises de Audiência (Notion)...")
    salvar_analise_na_base(bilan, len(entradas))

    print("Atualizando insights.json (dashboard)...")
    salvar_bilan_no_insights_json(bilan, len(entradas))

    print("Marcando entradas como processadas...")
    for e in entradas:
        marcar_processado(e["id"])

    print("\n=== Análise de audiência concluída ===")


if __name__ == "__main__":
    main()
