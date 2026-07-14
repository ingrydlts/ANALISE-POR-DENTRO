"""
MÓDULO 5 — Sugestões de Conteúdo (automação recorrente)
Roda toda segunda-feira de manhã via GitHub Actions — depois do calendário
da semana já estar visível, antes da criadora planejar a semana.

Fluxo:
1. Lê os bilans de audiência mais recentes (dores_nao_atendidas + pedidos_
   conteudo + pautas_sugeridas + temas_audiencia) da base "Análises de
   Audiência (Semanal)".
2. Lê o benchmark de concorrência mais recente (lacunas_oportunidades +
   recomendacao_editorial + o_que_nao_fazer) da base "Benchmarks de
   Concorrência (Mensal)" — segue mesmo sem nenhuma linha ainda.
3. Lê o calendário atual (INSTA TO POR DENTRO): títulos já agendados/em
   produção nos próximos ~60 dias + títulos já parados em Idea/Backlog —
   evita sugerir duplicata do que já existe ou já foi sugerido antes.
4. Manda tudo pra Claude, que propõe até 4 posts novos (título, formato,
   pilar, público, promessa, justificativa) — só o que não está coberto.
5. Cria uma página rascunho (Stage=Idea, sem data) por sugestão aceita em
   INSTA TO POR DENTRO, com a Promessa do conteudo prefixada por
   "[Sugestão automática — <data>]: <justificativa>" para rastreabilidade.
6. Atualiza insights.json: recarrega "calendario_posts" inteiro a partir do
   calendário real do Notion — assim toda sugestão nova (e qualquer edição
   manual feita no Notion durante a semana) aparece automaticamente no
   Calendário Real do dashboard, sem sincronização manual.

Variáveis de ambiente esperadas:
  NOTION_TOKEN, ANTHROPIC_API_KEY, NOTION_ANALISES_DB_ID,
  NOTION_BENCHMARKS_DB_ID, NOTION_CALENDARIO_DB_ID
  INSIGHTS_JSON_PATH (opcional — default "insights.json")
"""

import os
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from dotenv import load_dotenv
from notion_client import Client as NotionClient
from notion_client.errors import APIResponseError
import anthropic

load_dotenv()

NOTION_TOKEN         = os.environ["NOTION_TOKEN"]
ANTHROPIC_API_KEY    = os.environ["ANTHROPIC_API_KEY"]
NOTION_ANALISES_DB_ID    = os.environ["NOTION_ANALISES_DB_ID"]     # 📊 Análises de Audiência (Semanal)
NOTION_BENCHMARKS_DB_ID  = os.environ["NOTION_BENCHMARKS_DB_ID"]   # 🎯 Benchmarks de Concorrência (Mensal)
NOTION_CALENDARIO_DB_ID  = os.environ["NOTION_CALENDARIO_DB_ID"]   # INSTA TO POR DENTRO
INSIGHTS_JSON_PATH   = os.environ.get("INSIGHTS_JSON_PATH", "insights.json")

notion = NotionClient(auth=NOTION_TOKEN)
claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

AGORA = datetime.now(timezone.utc)
HOJE_STR = AGORA.strftime("%Y-%m-%d")

MAX_SUGESTOES = 4
PILARES_VALIDOS  = {"Sistema", "Trajetória", "Identidade", "Sociedade", "viral"}
PUBLICO_VALIDOS  = {"Pré-chegada", "Recém-chegado", "Adaptado"}
FORMATOS_SAIDA   = {"Reel": "Reel", "Carrossel": "Carrousel"}  # nome amigável -> valor exato do select no Notion

_data_source_cache = {}


def resolver_data_source_id(database_id: str) -> str:
    if database_id in _data_source_cache:
        return _data_source_cache[database_id]
    db = notion.databases.retrieve(database_id=database_id)
    data_sources = db.get("data_sources", [])
    if not data_sources:
        raise RuntimeError(f"O database {database_id} não retornou nenhum data_source.")
    data_source_id = data_sources[0]["id"]
    _data_source_cache[database_id] = data_source_id
    return data_source_id


def _rt(props: dict, campo: str) -> str:
    return "".join(rt.get("text", {}).get("content", "") for rt in props.get(campo, {}).get("rich_text", []))


def _title(props: dict, campo: str) -> str:
    t = props.get(campo, {}).get("title", [])
    return t[0]["text"]["content"] if t else ""


# ── 1. Bilans de audiência recentes ──────────────────────────────────────────

def buscar_bilans_audiencia_recentes(limite: int = 3) -> list:
    data_source_id = resolver_data_source_id(NOTION_ANALISES_DB_ID)
    resp = notion.data_sources.query(
        data_source_id=data_source_id,
        sorts=[{"property": "Data", "direction": "descending"}],
        page_size=limite,
    )
    bilans = []
    for page in resp.get("results", []):
        p = page["properties"]
        bilans.append({
            "semana": _title(p, "Semana"),
            "dores_nao_atendidas": _rt(p, "Dores Não Atendidas"),
            "pedidos_conteudo": _rt(p, "Pedidos de Conteúdo"),
            "pautas_sugeridas": _rt(p, "Pautas Sugeridas"),
            "perguntas_recorrentes": _rt(p, "Perguntas Recorrentes"),
        })
    return bilans


# ── 2. Benchmark de concorrência mais recente ────────────────────────────────

def buscar_benchmark_concorrencia_recente() -> dict | None:
    data_source_id = resolver_data_source_id(NOTION_BENCHMARKS_DB_ID)
    resp = notion.data_sources.query(
        data_source_id=data_source_id,
        sorts=[{"property": "Data da Rodada", "direction": "descending"}],
        page_size=1,
    )
    resultados = resp.get("results", [])
    if not resultados:
        return None
    p = resultados[0]["properties"]
    return {
        "mes": _title(p, "Mês"),
        "lacunas": _rt(p, "Lacunas"),
        "recomendacao": _rt(p, "Recomendação Editorial"),
        "o_que_nao_fazer": _rt(p, "O Que Não Fazer"),
    }


# ── 3. Calendário atual (dedup + datas ocupadas) ─────────────────────────────

# Cadência semanal definida pela criadora (sessão de 14/07/2026): Domingo é
# reservado pra série "(in)digest" (notícia/política) — sugestão automática
# nunca ocupa domingo. Segunda/Quarta = Reel, Terça/Quinta = Carrossel.
# Python weekday(): Segunda=0 ... Domingo=6.
DIAS_POR_FORMATO = {"Reel": {0, 2}, "Carrossel": {1, 3}}


def buscar_titulos_calendario() -> tuple[list, list, set]:
    """Retorna (agendados_proximos_60_dias, ja_em_idea_backlog, datas_ocupadas)."""
    data_source_id = resolver_data_source_id(NOTION_CALENDARIO_DB_ID)

    daqui_60 = (AGORA + timedelta(days=60)).strftime("%Y-%m-%d")
    ha_7 = (AGORA - timedelta(days=7)).strftime("%Y-%m-%d")
    resp_agendados = notion.data_sources.query(
        data_source_id=data_source_id,
        filter={"and": [
            {"property": "Posting Date", "date": {"on_or_after": ha_7}},
            {"property": "Posting Date", "date": {"on_or_before": daqui_60}},
        ]},
        page_size=100,
    )
    agendados = []
    ocupadas = set()
    for pg in resp_agendados.get("results", []):
        titulo = _title(pg["properties"], "Nom")
        if titulo:
            agendados.append(titulo)
        data_val = (pg["properties"].get("Posting Date", {}).get("date") or {}).get("start")
        if data_val:
            ocupadas.add(data_val[:10])

    resp_ideas = notion.data_sources.query(
        data_source_id=data_source_id,
        filter={"property": "Posting Date", "date": {"is_empty": True}},
        page_size=100,
    )
    ideas = [_title(pg["properties"], "Nom") for pg in resp_ideas.get("results", []) if _title(pg["properties"], "Nom")]

    return agendados, ideas, ocupadas


def proxima_data_livre(formato: str, ocupadas: set, a_partir_de: datetime) -> str:
    """Primeiro dia, a partir de amanhã, cujo dia da semana bate com o formato
    (Reel = seg/qua, Carrossel = ter/qui) e ainda não está ocupado. Se não
    achar em 90 dias (não deveria acontecer), cai pro dia seguinte livre,
    de qualquer dia da semana, pra nunca deixar uma sugestão sem data."""
    dias_validos = DIAS_POR_FORMATO.get(formato, {0, 1, 2, 3})
    d = a_partir_de + timedelta(days=1)
    for _ in range(90):
        d_str = d.strftime("%Y-%m-%d")
        if d.weekday() in dias_validos and d_str not in ocupadas:
            return d_str
        d += timedelta(days=1)
    d = a_partir_de + timedelta(days=1)
    while d.strftime("%Y-%m-%d") in ocupadas:
        d += timedelta(days=1)
    return d.strftime("%Y-%m-%d")


# ── 4. Geração via Claude ────────────────────────────────────────────────────

SCHEMA_JSON = """{
  "sugestoes": [
    {
      "titulo": "string",
      "formato": "Reel|Carrossel",
      "pilar": "Sistema|Trajetória|Identidade|Sociedade|viral",
      "publico": ["Pré-chegada|Recém-chegado|Adaptado", "..."],
      "promessa": "string (o que o post entrega, até 280 caracteres)",
      "justificativa": "string (por que essa sugestão agora, cite a fonte: audiência, concorrência ou lacuna de calendário)"
    }
  ]
}"""


def gerar_sugestoes(bilans: list, benchmark: dict | None, agendados: list, ideas: list) -> list:
    bilans_txt = "\n\n".join(
        f"### {b['semana']}\nDores não atendidas: {b['dores_nao_atendidas']}\n"
        f"Pedidos de conteúdo: {b['pedidos_conteudo']}\nPautas já sugeridas: {b['pautas_sugeridas']}\n"
        f"Perguntas recorrentes: {b['perguntas_recorrentes']}"
        for b in bilans
    ) or "Nenhum bilan de audiência disponível ainda."

    benchmark_txt = (
        f"Mês: {benchmark['mes']}\nLacunas: {benchmark['lacunas']}\n"
        f"Recomendação editorial: {benchmark['recomendacao']}\nO que não fazer: {benchmark['o_que_nao_fazer']}"
        if benchmark else "Nenhum benchmark de concorrência disponível ainda."
    )

    prompt = f"""Você é o sistema editorial do canal Por Dentro — imigrante brasileira na França, conteúdo sobre trabalho, saúde, burocracia, moradia, cultura. Posicionamento: observador, lúcido, educativo. Nunca romantiza nem catastrofiza, nunca clickbait no corpo do conteúdo.

## BILANS DE AUDIÊNCIA RECENTES
{bilans_txt}

## BENCHMARK DE CONCORRÊNCIA MAIS RECENTE
{benchmark_txt}

## JÁ AGENDADO OU EM PRODUÇÃO (próximos 60 dias) — NÃO DUPLICAR TEMA
{chr(10).join('- ' + t for t in agendados) or '(nenhum)'}

## JÁ SUGERIDO ANTES, PARADO EM IDEA/BACKLOG — NÃO REPETIR
{chr(10).join('- ' + t for t in ideas) or '(nenhum)'}

---

Proponha até {MAX_SUGESTOES} posts novos que preencham lacunas reais — cruzando o que a audiência pediu, o que a concorrência não cobre, e o que ainda não está no calendário. Não invente dor que não apareceu nos dados acima. Se não houver base suficiente para {MAX_SUGESTOES} sugestões de qualidade, proponha menos.

Responda APENAS com um JSON válido (sem markdown, sem cercas de código, sem texto fora do JSON) no formato exato abaixo:

{SCHEMA_JSON}"""

    resp = claude.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}]
    )
    texto = resp.content[0].text.strip()
    if texto.startswith("```"):
        texto = texto.strip("`")
        if texto.lower().startswith("json"):
            texto = texto[4:]
        texto = texto.strip()

    try:
        data = json.loads(texto)
        return data.get("sugestoes", [])
    except json.JSONDecodeError:
        print(f"  ⚠ Claude não retornou JSON válido. Bruto: {texto[:300]}")
        return []


# ── 5. Criar páginas rascunho no calendário ──────────────────────────────────
# IMPORTANTE: toda sugestão SEMPRE ganha uma "Posting Date" (mesmo sendo só uma
# proposta, Stage continua "Idea"). Sem data, a página não aparece na grade do
# Calendário Real do dashboard — fica invisível pra criadora decidir. A data é
# só um slot sugerido; mover ou apagar fica a critério de quem revisa.

def criar_pagina_sugestao(sugestao: dict, ocupadas: set) -> bool:
    titulo = (sugestao.get("titulo") or "").strip()
    if not titulo:
        return False

    formato = FORMATOS_SAIDA.get(sugestao.get("formato"), "Reel")
    pilar = sugestao.get("pilar")
    pilares = [pilar] if pilar in PILARES_VALIDOS else []
    publico = [p for p in (sugestao.get("publico") or []) if p in PUBLICO_VALIDOS]
    data_sugerida = proxima_data_livre(formato, ocupadas, AGORA)
    ocupadas.add(data_sugerida)  # próxima sugestão desta rodada não cai no mesmo dia
    promessa = f"[Sugestão automática — {HOJE_STR}, data é um slot proposto]: {sugestao.get('justificativa', '')} — {sugestao.get('promessa', '')}"[:2000]

    properties = {
        "Nom": titulo,
        "Formato": formato,
        "Stage": "Idea",
        "Promessa do conteudo": promessa,
        "date:Posting Date:start": data_sugerida,
    }
    if pilares:
        properties["Pilars"] = json.dumps(pilares, ensure_ascii=False)
    if publico:
        properties["Público"] = json.dumps(publico, ensure_ascii=False)

    try:
        data_source_id = resolver_data_source_id(NOTION_CALENDARIO_DB_ID)
        notion.pages.create(parent={"data_source_id": data_source_id}, properties=properties)
        print(f"    data sugerida: {data_sugerida}")
        return True
    except APIResponseError as e:
        print(f"  ✖ Erro ao criar página para '{titulo}': {e}")
        return False


# ── 6. Refrescar calendario_posts no insights.json ───────────────────────────

def _normalizar_formato(f):
    if not f:
        return None
    return {"Reel": "Reel", "Reels": "Reel", "Carrousel": "Carrossel", "Carrossel": "Carrossel"}.get(f, f)


def refrescar_calendario_posts():
    """Recarrega TODOS os posts com Posting Date preenchida — mesma lógica da
    sincronização manual usada para construir o Calendário Real do dashboard."""
    data_source_id = resolver_data_source_id(NOTION_CALENDARIO_DB_ID)
    resp = notion.data_sources.query(
        data_source_id=data_source_id,
        filter={"property": "Posting Date", "date": {"is_not_empty": True}},
        sorts=[{"property": "Posting Date", "direction": "ascending"}],
        page_size=100,
    )

    def multi(props, campo):
        return [o["name"] for o in props.get(campo, {}).get("multi_select", [])]

    posts = []
    for page in resp.get("results", []):
        p = page["properties"]
        data_val = p.get("Posting Date", {}).get("date", {})
        posts.append({
            "url": page["url"],
            "titulo": _title(p, "Nom"),
            "data": (data_val or {}).get("start", "")[:10],
            "formato": _normalizar_formato((p.get("Formato", {}).get("select") or {}).get("name")),
            "estagio": (p.get("Stage", {}).get("status") or {}).get("name"),
            "pilares": multi(p, "Pilars"),
            "meta": multi(p, "META"),
            "publico": multi(p, "Público"),
            "serie": multi(p, "SÉRIE"),
            "promessa": p.get("Promessa do conteudo", {}).get("rich_text", [{}])[0].get("text", {}).get("content", "") if p.get("Promessa do conteudo", {}).get("rich_text") else "",
        })

    path = Path(INSIGHTS_JSON_PATH)
    if not path.exists():
        print(f"  ⚠ {INSIGHTS_JSON_PATH} não existe no repositório — criando esqueleto mínimo.")
        data = {}
    else:
        data = json.loads(path.read_text(encoding="utf-8"))

    data["calendario_posts"] = posts
    data["calendario_posts_atualizado_em"] = HOJE_STR
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  ✓ calendario_posts atualizado: {len(posts)} post(s)")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"\n=== Sugestões de Conteúdo — {HOJE_STR} ===\n")

    print("Lendo bilans de audiência recentes...")
    bilans = buscar_bilans_audiencia_recentes()
    print(f"  {len(bilans)} bilan(s) encontrado(s).")

    print("Lendo benchmark de concorrência mais recente...")
    benchmark = buscar_benchmark_concorrencia_recente()
    print("  encontrado." if benchmark else "  nenhum ainda — seguindo sem esse insumo.")

    print("Lendo calendário atual (dedup + datas ocupadas)...")
    agendados, ideas, ocupadas = buscar_titulos_calendario()
    print(f"  {len(agendados)} agendado(s)/em produção · {len(ideas)} já em Idea/Backlog · {len(ocupadas)} data(s) ocupada(s).")

    if not bilans and not benchmark:
        print("Sem insumos suficientes (nem audiência nem concorrência) — pulando geração desta rodada.")
    else:
        print("Gerando sugestões com Claude...")
        sugestoes = gerar_sugestoes(bilans, benchmark, agendados, ideas)
        print(f"  {len(sugestoes)} sugestão(ões) proposta(s).")

        criadas = 0
        for s in sugestoes:
            print(f"  → {s.get('titulo')}")
            if criar_pagina_sugestao(s, ocupadas):
                criadas += 1
                print(f"    ✓ página criada.")
        print(f"{criadas} página(s) nova(s) criada(s) em INSTA TO POR DENTRO, cada uma já com data sugerida.")

    print("Atualizando calendario_posts no insights.json...")
    refrescar_calendario_posts()

    print("\n=== Sugestões de conteúdo concluído ===")


if __name__ == "__main__":
    main()
