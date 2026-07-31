"""
MÓDULO 5 — Sugestões de Conteúdo (automação recorrente)
Roda toda segunda-feira de manhã via GitHub Actions — depois do calendário
da semana já estar visível, antes da criadora planejar a semana.

Fluxo:
1. Lê os bilans de audiência mais recentes (dores_nao_atendidas + pedidos_
   conteudo + pautas_sugeridas + temas_audiencia) da base "Análises de
   Audiência (Semanal)" — resumo semanal já agregado.
1b. Lê também as entradas BRUTAS e recentes de "Inputs Benchmark Instagram"
   (CATEGORIA=AUDIÊNCIA), especificamente a propriedade "Ideia de Conteúdo
   Gerada" (preenchida por enriquecer_conversas_audiencia.py por entrada) —
   o bilan semanal já é um resumo do Claude, então a ideia concreta de cada
   conversa individual se perde na agregação. Isso complementa o bilan com o
   material bruto.
2. Lê o benchmark de concorrência mais recente (lacunas + insights por
   concorrente específico + sugestões de conteúdo já levantadas pela análise
   de concorrência + recomendação editorial + o que não fazer) da base
   "Benchmarks de Concorrência (Mensal)" — segue mesmo sem nenhuma linha
   ainda. Os dois campos "insights por concorrente" e "sugestões de
   conteúdo" foram adicionados em 31/07/2026 especificamente para que esta
   camada gere ideias também a partir do que concorrentes específicos fazem,
   não só do agregado por nicho.
3. Lê o calendário atual (INSTA TO POR DENTRO): títulos já agendados/em
   produção nos próximos ~60 dias + títulos já parados em Idea/Backlog —
   evita sugerir duplicata do que já existe ou já foi sugerido antes.
4. Manda tudo pra Claude — com o guia de voz/editorial da marca embutido no
   prompt e a ferramenta de busca na web ativada — que propõe até 4 posts
   novos (título, formato, pilar, público, promessa, justificativa, fontes
   reais quando pesquisou algo verificável) — só o que não está coberto.
5. Cria uma página rascunho (Stage=Idea, sem data) por sugestão aceita em
   INSTA TO POR DENTRO, com a Promessa do conteudo prefixada por
   "[Sugestão automática — <data>]: <justificativa>" para rastreabilidade.
6. Atualiza insights.json:
   - recarrega "calendario_posts" inteiro a partir do calendário real do
     Notion — assim toda sugestão nova (e qualquer edição manual feita no
     Notion durante a semana) aparece automaticamente no Calendário Real do
     dashboard, sem sincronização manual;
   - grava também "sugestoes_conteudo" (upsert por título) com a sugestão
     completa + fontes pesquisadas, pra aparecer na nova seção de sugestões
     da aba Calendário do dashboard — mesmo que a criação da página no
     Notion tenha falhado, a sugestão ainda fica visível pra revisão manual.

Variáveis de ambiente esperadas:
  NOTION_TOKEN, ANTHROPIC_API_KEY, NOTION_DB_IG, NOTION_ANALISES_DB_ID,
  NOTION_BENCHMARKS_DB_ID, NOTION_CALENDARIO_DB_ID
  INSIGHTS_JSON_PATH (opcional — default "insights.json")
"""

import os
import json
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from dotenv import load_dotenv
from notion_client import Client as NotionClient
from notion_client.errors import APIResponseError
import anthropic

load_dotenv()

NOTION_TOKEN         = os.environ["NOTION_TOKEN"]
ANTHROPIC_API_KEY    = os.environ["ANTHROPIC_API_KEY"]
NOTION_DB_IG             = os.environ["NOTION_DB_IG"]              # Inputs Benchmark Instagram (ideias brutas por entrada)
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


# ── 1b. Ideias brutas de audiência (por entrada, não agregadas) ──────────────

def buscar_ideias_brutas_audiencia(limite: int = 25) -> list:
    """Lê as entradas AUDIÊNCIA mais recentes de 'Inputs Benchmark Instagram'
    direto, sem passar pela agregação semanal — a propriedade 'Ideia de
    Conteúdo Gerada' (preenchida por enriquecer_conversas_audiencia.py) é o
    material bruto que o bilan semanal resume; aqui a ideia específica de cada
    conversa fica disponível pra Claude, sem se perder no resumo."""
    data_source_id = resolver_data_source_id(NOTION_DB_IG)
    resp = notion.data_sources.query(
        data_source_id=data_source_id,
        filter={"property": "CATEGORIA", "select": {"equals": "AUDIÊNCIA"}},
        sorts=[{"property": "Data da Coleta", "direction": "descending"}],
        page_size=limite,
    )
    ideias = []
    for page in resp.get("results", []):
        p = page["properties"]
        ideia = _rt(p, "Ideia de Conteúdo Gerada")
        if not ideia:
            continue
        ideias.append({
            "nome": _title(p, "Name"),
            "ideia_conteudo": ideia,
            "dor_necessidade": _rt(p, "Dor/Necessidade Identificada"),
            "palavras_chave": _rt(p, "Palavras-chave"),
        })
    return ideias


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
        # Campos adicionados em 31/07/2026 — antes disso, camada 5 só via
        # "lacunas" (agregado por nicho) e nunca via o que cada concorrente
        # específico faz, nem as ideias de conteúdo já elaboradas pelo módulo
        # de concorrência a partir dessas lacunas.
        "insights_por_concorrente": _rt(p, "Insights por Concorrente"),
        "sugestoes_da_concorrencia": _rt(p, "Sugestões de Conteúdo"),
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
      "justificativa": "string (por que essa sugestão agora, cite a fonte: audiência, concorrência ou lacuna de calendário)",
      "fontes": [{"titulo": "string (título da página/fonte)", "url": "string (URL real retornada pela busca)"}]
    }
  ]
}"""

# Guia de voz/editorial da marca Lo Tierzo / canal Por Dentro — embutido no
# prompt pra toda sugestão automática já nascer no tom certo, não só no tema
# certo. Fonte: brand-master-por-dentro.html (documento de arquitetura).
GUIA_VOZ = """## VOZ E EDITORIAL — MARCA LO TIERZO / POR DENTRO

Voz central: uma conversa inteligente entre amigas — não aula, não desabafo, não guia turístico. Direta, calorosa, lúcida, ancorada na experiência real. Teste: "isso soa como eu diria pra uma amiga inteligente que acabou de chegar em Paris?"

Vocabulário a usar: "na prática / funciona assim", "aprendi que / percebi que", "a realidade é que", "não é difícil, mas exige atenção", "vou te deixar por dentro de…", "sem romantizar, sem assustar".
Proibido: "impossível/horrível/catastrófico", "sonho realizado/paraíso", "você PRECISA agora", "segredo revelado", "inacreditável/absurdo/chocante", tom de coach ("você consegue"), "perfeito/incrível/maravilhoso".

Regras absolutas: experiência real antes do dado; nunca falar de cima, sempre de igual pra igual; realista com esperança, nunca assustar nem romantizar; frases curtas, uma ideia por vez; francês é tempero, nunca protagonista.

Formatos:
- Reel: 30–60s, hook nos primeiros 3s. Sensacionalismo leve é aceitável SÓ no hook (por causa do algoritmo) — no resto do post, zero clickbait.
- Carrossel: 6–10 slides, pensado pra salvamento/compartilhamento, não só like.

Pilares (todo post pertence a um): Sistema (Dicas & Vida Real, prático, tom didático não professoral), Trajetória (storytelling 1ª pessoa de chegada/adaptação), Identidade (Reflexões & Perspectivas, introspectivo, sobre solidão/amadurecimento), Sociedade (análise cultural crítica França×Brasil, tom analítico nunca panfletário)."""


def _extrair_texto_resposta(resp) -> str:
    """Com a ferramenta de busca ativada, resp.content pode ter vários blocos
    (server_tool_use, web_search_tool_result, text) — o texto final não é
    necessariamente o primeiro bloco."""
    return "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()


def _extrair_json_sugestoes(texto: str) -> dict | None:
    """Com busca na web ativada, o Claude costuma narrar o raciocínio (o que
    pesquisou, o que concluiu) antes do JSON final, mesmo pedindo pra
    responder só com JSON — testado ao vivo: a resposta real veio como
    "Vou analisar os dados... identifiquei 3 lacunas reais... {json}". Por
    isso não basta json.loads(texto_inteiro); localiza o objeto JSON embutido
    (a partir de '{"sugestoes"') e faz parsing só dessa parte."""
    try:
        return json.loads(texto)
    except json.JSONDecodeError:
        pass

    m = re.search(r'\{\s*"sugestoes"\s*:', texto)
    if not m:
        return None
    inicio = m.start()
    profundidade = 0
    for i in range(inicio, len(texto)):
        ch = texto[i]
        if ch == '{':
            profundidade += 1
        elif ch == '}':
            profundidade -= 1
            if profundidade == 0:
                try:
                    return json.loads(texto[inicio:i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def gerar_sugestoes(bilans: list, ideias_brutas: list, benchmark: dict | None, agendados: list, ideas: list) -> list:
    bilans_txt = "\n\n".join(
        f"### {b['semana']}\nDores não atendidas: {b['dores_nao_atendidas']}\n"
        f"Pedidos de conteúdo: {b['pedidos_conteudo']}\nPautas já sugeridas: {b['pautas_sugeridas']}\n"
        f"Perguntas recorrentes: {b['perguntas_recorrentes']}"
        for b in bilans
    ) or "Nenhum bilan de audiência disponível ainda."

    ideias_txt = "\n".join(
        f"- {i['nome']}: {i['ideia_conteudo']}"
        + (f" (dor: {i['dor_necessidade']})" if i['dor_necessidade'] else "")
        for i in ideias_brutas
    ) or "Nenhuma entrada individual de audiência disponível ainda."

    benchmark_txt = (
        f"Mês: {benchmark['mes']}\nLacunas: {benchmark['lacunas']}\n"
        f"Insights por concorrente específico: {benchmark['insights_por_concorrente']}\n"
        f"Ideias de conteúdo já levantadas a partir da concorrência: {benchmark['sugestoes_da_concorrencia']}\n"
        f"Recomendação editorial: {benchmark['recomendacao']}\nO que não fazer: {benchmark['o_que_nao_fazer']}"
        if benchmark else "Nenhum benchmark de concorrência disponível ainda."
    )

    prompt = f"""Você é o sistema editorial do canal Por Dentro — imigrante brasileira na França, conteúdo sobre trabalho, saúde, burocracia, moradia, cultura. Posicionamento: observador, lúcido, educativo. Nunca romantiza nem catastrofiza, nunca clickbait no corpo do conteúdo.

{GUIA_VOZ}

## BILANS DE AUDIÊNCIA RECENTES (resumo semanal)
{bilans_txt}

## IDEIAS DE CONTEÚDO POR ENTRADA (material bruto, não agregado — propriedade "Ideia de Conteúdo Gerada")
{ideias_txt}

## BENCHMARK DE CONCORRÊNCIA MAIS RECENTE
{benchmark_txt}

## JÁ AGENDADO OU EM PRODUÇÃO (próximos 60 dias) — NÃO DUPLICAR TEMA
{chr(10).join('- ' + t for t in agendados) or '(nenhum)'}

## JÁ SUGERIDO ANTES, PARADO EM IDEA/BACKLOG — NÃO REPETIR
{chr(10).join('- ' + t for t in ideas) or '(nenhum)'}

---

Proponha até {MAX_SUGESTOES} posts novos que preencham lacunas reais — cruzando o que a audiência pediu (bilans + ideias por entrada), o que a concorrência não cobre e o que concorrentes específicos fazem bem ou deixam passar (insights por concorrente + ideias de conteúdo já levantadas a partir da concorrência — pode adaptar/aprofundar essas ideias, nunca copiar o concorrente), e o que ainda não está no calendário. Não invente dor que não apareceu nos dados acima. Se não houver base suficiente para {MAX_SUGESTOES} sugestões de qualidade, proponha menos.

Pra cada sugestão, se o tema envolver um fato verificável (prazo, regra, procedimento — ex.: burocracia, visto, imposto, mercado de trabalho francês), PESQUISE na web pra confirmar e cite fontes reais (título + URL) no campo "fontes". Nunca invente URL — se não pesquisou ou não achou nada confiável pra essa sugestão específica, deixe "fontes" como lista vazia. Sugestões puramente de storytelling/trajetória pessoal não precisam de fontes.

Responda com um JSON válido (sem markdown, sem cercas de código, sem texto fora do JSON) no formato exato abaixo, como sua ÚLTIMA mensagem:

{SCHEMA_JSON}"""

    resp = claude.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=4000,
        tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 6}],
        messages=[{"role": "user", "content": prompt}]
    )
    texto = _extrair_texto_resposta(resp)
    data = _extrair_json_sugestoes(texto)
    if data is None:
        print(f"  ⚠ Claude não retornou JSON válido (nem embutido em texto). Bruto: {texto[:300]}")
        return []
    return data.get("sugestoes", [])


# ── 5. Criar páginas rascunho no calendário ──────────────────────────────────
# IMPORTANTE: toda sugestão SEMPRE ganha uma "Posting Date" (mesmo sendo só uma
# proposta, Stage continua "Idea"). Sem data, a página não aparece na grade do
# Calendário Real do dashboard — fica invisível pra criadora decidir. A data é
# só um slot sugerido; mover ou apagar fica a critério de quem revisa.

def criar_pagina_sugestao(sugestao: dict, ocupadas: set) -> dict | None:
    """Cria o rascunho no Notion e devolve a sugestão enriquecida (com data
    sugerida, formato normalizado e se a criação no Notion deu certo) — pra
    main() gravar em insights.json independente do resultado no Notion, já
    que a sugestão deve ficar visível no dashboard mesmo se essa parte falhar."""
    titulo = (sugestao.get("titulo") or "").strip()
    if not titulo:
        return None

    formato_pt = sugestao.get("formato") if sugestao.get("formato") in FORMATOS_SAIDA else "Reel"
    formato = FORMATOS_SAIDA[formato_pt]  # valor exato do select no Notion
    pilar = sugestao.get("pilar")
    pilares = [pilar] if pilar in PILARES_VALIDOS else []
    publico = [p for p in (sugestao.get("publico") or []) if p in PUBLICO_VALIDOS]
    fontes = [
        {"titulo": (f.get("titulo") or "").strip(), "url": (f.get("url") or "").strip()}
        for f in (sugestao.get("fontes") or [])
        if isinstance(f, dict) and f.get("url")
    ]
    # proxima_data_livre espera a chave em português (DIAS_POR_FORMATO), não o
    # valor traduzido do select do Notion — usar "formato" aqui sempre cai no
    # default {0,1,2,3} e quebra a cadência Reel=seg/qua, Carrossel=ter/qui.
    data_sugerida = proxima_data_livre(formato_pt, ocupadas, AGORA)
    ocupadas.add(data_sugerida)  # próxima sugestão desta rodada não cai no mesmo dia
    justificativa = sugestao.get("justificativa", "")
    fontes_txt = " Fontes: " + "; ".join(f"{f['titulo']} ({f['url']})" for f in fontes) if fontes else ""
    promessa = f"[Sugestão automática — {HOJE_STR}, data é um slot proposto]: {justificativa} — {sugestao.get('promessa', '')}{fontes_txt}"[:2000]

    properties = {
        "Nom": {"title": [{"text": {"content": titulo}}]},
        "Formato": {"select": {"name": formato}},
        "Stage": {"status": {"name": "Idea"}},
        "Promessa do conteudo": {"rich_text": [{"text": {"content": promessa}}]},
        "Posting Date": {"date": {"start": data_sugerida}},
    }
    if pilares:
        properties["Pilars"] = {"multi_select": [{"name": p} for p in pilares]}
    if publico:
        properties["Público"] = {"multi_select": [{"name": p} for p in publico]}

    resultado = {
        "titulo": titulo,
        "formato": formato_pt,
        "pilar": pilar if pilar in PILARES_VALIDOS else None,
        "publico": publico,
        "promessa": sugestao.get("promessa", ""),
        "justificativa": justificativa,
        "fontes": fontes,
        "data_sugerida": data_sugerida,
    }

    try:
        data_source_id = resolver_data_source_id(NOTION_CALENDARIO_DB_ID)
        notion.pages.create(parent={"data_source_id": data_source_id}, properties=properties)
        print(f"    data sugerida: {data_sugerida}")
        resultado["notion_criada"] = True
    except APIResponseError as e:
        print(f"  ✖ Erro ao criar página para '{titulo}': {e}")
        resultado["notion_criada"] = False

    return resultado


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


# ── 7. Salvar sugestões (com fontes) no insights.json ────────────────────────

def salvar_sugestoes_no_insights_json(sugestoes: list):
    """Grava as sugestões desta rodada (com fontes) pra alimentar a nova seção
    de sugestões da aba Calendário do dashboard — independente do resultado da
    criação da página no Notion, pra nunca esconder uma sugestão da criadora."""
    path = Path(INSIGHTS_JSON_PATH)
    if not path.exists():
        data = {}
    else:
        data = json.loads(path.read_text(encoding="utf-8"))

    existentes = data.setdefault("sugestoes_conteudo", [])
    existentes_por_titulo = {e["titulo"].strip().lower(): e for e in existentes if e.get("titulo")}

    for s in sugestoes:
        entrada = {
            "id": f"{HOJE_STR}-{s['titulo'][:40]}",
            "titulo": s["titulo"],
            "formato": s["formato"],
            "pilar": s["pilar"],
            "publico": s["publico"],
            "promessa": s["promessa"],
            "justificativa": s["justificativa"],
            "fontes": s["fontes"],
            "data_sugerida": s["data_sugerida"],
            "notion_criada": s["notion_criada"],
            "gerado_em": HOJE_STR,
        }
        # Upsert por título (case-insensitive) — reprocessar a mesma ideia
        # atualiza a entrada em vez de duplicar; senão, some no final do array.
        chave = s["titulo"].strip().lower()
        if chave in existentes_por_titulo:
            existentes[existentes.index(existentes_por_titulo[chave])] = entrada
        else:
            existentes.append(entrada)
            existentes_por_titulo[chave] = entrada

    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  ✓ sugestoes_conteudo atualizado: {len(existentes)} sugestão(ões) no total.")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"\n=== Sugestões de Conteúdo — {HOJE_STR} ===\n")

    print("Lendo bilans de audiência recentes...")
    bilans = buscar_bilans_audiencia_recentes()
    print(f"  {len(bilans)} bilan(s) encontrado(s).")

    print("Lendo ideias de conteúdo por entrada (propriedade bruta)...")
    ideias_brutas = buscar_ideias_brutas_audiencia()
    print(f"  {len(ideias_brutas)} ideia(s) encontrada(s).")

    print("Lendo benchmark de concorrência mais recente...")
    benchmark = buscar_benchmark_concorrencia_recente()
    print("  encontrado." if benchmark else "  nenhum ainda — seguindo sem esse insumo.")

    print("Lendo calendário atual (dedup + datas ocupadas)...")
    agendados, ideas, ocupadas = buscar_titulos_calendario()
    print(f"  {len(agendados)} agendado(s)/em produção · {len(ideas)} já em Idea/Backlog · {len(ocupadas)} data(s) ocupada(s).")

    if not bilans and not ideias_brutas and not benchmark:
        print("Sem insumos suficientes (nem audiência nem concorrência) — pulando geração desta rodada.")
    else:
        print("Gerando sugestões com Claude (com busca na web pra fatos verificáveis)...")
        propostas = gerar_sugestoes(bilans, ideias_brutas, benchmark, agendados, ideas)
        print(f"  {len(propostas)} sugestão(ões) proposta(s).")

        criadas = 0
        sugestoes_geradas = []
        for s in propostas:
            print(f"  → {s.get('titulo')}")
            resultado = criar_pagina_sugestao(s, ocupadas)
            if resultado is None:
                continue
            sugestoes_geradas.append(resultado)
            if resultado["notion_criada"]:
                criadas += 1
                print(f"    ✓ página criada.")
        print(f"{criadas} página(s) nova(s) criada(s) em INSTA TO POR DENTRO, cada uma já com data sugerida.")

        if sugestoes_geradas:
            print("Salvando sugestões (com fontes) no insights.json...")
            salvar_sugestoes_no_insights_json(sugestoes_geradas)

    print("Atualizando calendario_posts no insights.json...")
    refrescar_calendario_posts()

    print("\n=== Sugestões de conteúdo concluído ===")


if __name__ == "__main__":
    main()
