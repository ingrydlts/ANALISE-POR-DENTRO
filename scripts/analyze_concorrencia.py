"""
MÓDULO 3 — Análise de Concorrência
Roda na primeira segunda-feira do mês via GitHub Actions (guard semanal — ver
Analise_concorrencia.yml, que dispara toda segunda e pula quando não é a primeira).

Fluxo:
1. Busca linhas da base COLETAS YOUTUBE (CATEGORIA=CONCORRÊNCIA) do mês atual
2. Busca observações de concorrência no Instagram, de DUAS fontes:
   a) FICHIERS INSTAGRAM — notas manuais em texto (CATEGORIA=CONCORRÊNCIA + PLATAFORMA=
      INSTAGRAM + STATUS=NOVO)
   b) Inputs Benchmark Instagram — prints de posts/reels de concorrentes já transcritos
      pela Claude em enriquecer_conversas_audiencia.py (CATEGORIA=CONCORRÊNCIA +
      PLATAFORMA=INSTAGRAM + STATUS=Analisado)
3. Envia tudo para Claude, que gera o BENCHMARK do mês em seções fixas (##)
4. Salva/atualiza UMA LINHA na base "🎯 Benchmarks de Concorrência (Mensal)"
   (chave "ID Mês" — reprocessar o mesmo mês atualiza a linha em vez de duplicar)
5. Salva o mesmo benchmark em insights.json — bloco estruturado em "concorrencia",
   para o dashboard (index.html) exibir na aba Concorrência. Antes desta versão,
   este script só escrevia na Notion — a análise de concorrência nunca chegava ao
   dashboard visual. Mesmo padrão de upsert por id usado em analyze_audiencia.py.
6. Marca as entradas usadas (das duas fontes de Instagram) como PROCESSADO

Variáveis de ambiente esperadas:
  NOTION_TOKEN, ANTHROPIC_API_KEY, NOTION_DB_ID, NOTION_DB_IG, NOTION_COLETAS_DB_ID,
  NOTION_BENCHMARKS_DB_ID
  INSIGHTS_JSON_PATH (opcional — caminho do insights.json no repo, default "insights.json")

Nota de correção (05/07/2026): antes desta versão, este script tentava ler "páginas COLETA YT"
filhas de uma página Notion (NOTION_INTELIGENCIA_PAGE_ID) que estava deletada/em branco — nunca
encontrava dados reais, porque o collect.py sempre gravou os canais como LINHAS na base COLETAS
YOUTUBE, não como páginas-filhas. O resultado também era salvo como página solta em vez de linha
estruturada, e o cron do workflow (`0 9 1-7 * 1`) disparava o job em todo dia 1–7 do mês MAIS toda
segunda-feira — sem proteção contra duplicar. As três coisas foram corrigidas: leitura direta de
COLETAS YOUTUBE, upsert por "ID Mês" na nova base estruturada, e cron consertado no workflow.

Nota de atualização (05/07/2026): "🗨️ CONVERSAS AUDIÊNCIA (Instagram)" virou "Inputs Benchmark
Instagram" e passou a aceitar CATEGORIA=CONCORRÊNCIA (prints de concorrentes), além de AUDIÊNCIA.
Este script foi atualizado para também consultar essa base (NOTION_DB_IG) como segunda fonte de
observações de Instagram, somando-se às notas manuais de FICHIERS INSTAGRAM.

Nota de unificação (14/07/2026): migrado do repositório modulo3-Benchmark-Concorrencia-Audiencia
para cá (junto com collect.py, analyze_audiencia.py e enriquecer_conversas_audiencia.py), para que
INSIGHTS_JSON_PATH aponte para o MESMO insights.json que o dashboard lê — antes, cada repositório
tinha sua própria cópia dessincronizada do arquivo.
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
NOTION_TOKEN            = os.environ["NOTION_TOKEN"]
ANTHROPIC_API_KEY       = os.environ["ANTHROPIC_API_KEY"]
NOTION_DB_ID            = os.environ["NOTION_DB_ID"]              # FICHIERS INSTAGRAM
NOTION_DB_IG            = os.environ["NOTION_DB_IG"]              # Inputs Benchmark Instagram
NOTION_COLETAS_DB_ID    = os.environ["NOTION_COLETAS_DB_ID"]      # COLETAS YOUTUBE
NOTION_BENCHMARKS_DB_ID = os.environ["NOTION_BENCHMARKS_DB_ID"]   # 🎯 Benchmarks de Concorrência (Mensal)
INSIGHTS_JSON_PATH      = os.environ.get("INSIGHTS_JSON_PATH", "insights.json")

notion = NotionClient(auth=NOTION_TOKEN)
claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

MESES_PT = {
    "January": "Janeiro", "February": "Fevereiro", "March": "Março",
    "April": "Abril", "May": "Maio", "June": "Junho",
    "July": "Julho", "August": "Agosto", "September": "Setembro",
    "October": "Outubro", "November": "Novembro", "December": "Dezembro"
}

AGORA        = datetime.now(timezone.utc)
MES_ATUAL_PT = MESES_PT[AGORA.strftime("%B")]              # ex: "Julho" — bate com o select "Mês" de COLETAS YOUTUBE
MES_ID       = AGORA.strftime("%Y-%m")                     # ex: "2026-07" — chave estável para upsert
MES_LABEL    = f"{MES_ATUAL_PT} {AGORA.strftime('%Y')}"    # ex: "Julho 2026" — título da linha

_data_source_cache = {}


def resolver_data_source_id(database_id: str) -> str:
    """Resolve o data_source_id atual de um database (API Notion 2025-09-03+). Ver analyze_audiencia.py."""
    if database_id in _data_source_cache:
        return _data_source_cache[database_id]
    db = notion.databases.retrieve(database_id=database_id)
    data_sources = db.get("data_sources", [])
    if not data_sources:
        raise RuntimeError(f"O database {database_id} não retornou nenhum data_source.")
    data_source_id = data_sources[0]["id"]
    _data_source_cache[database_id] = data_source_id
    return data_source_id


def _query_com_diagnostico(data_source_id: str, filtro: dict, contexto: str):
    """
    Wrapper de notion.data_sources.query(): se a API rejeitar uma propriedade do
    filtro (erro 400 "Could not find property with name or id: X"), lista as
    propriedades reais dessa base nos logs antes de relançar o erro.

    Isso já aconteceu em produção (31/07/2026) com "CATEGORIA" — a base em si
    tinha a propriedade certa, o problema era uma secret do GitHub
    (NOTION_DB_ID / NOTION_COLETAS_DB_ID / NOTION_DB_IG) apontando para o
    database ID errado. Sem esse diagnóstico, o log só mostra o 400 cru e não dá
    pra saber, sem entrar no Notion, se a base é a errada ou se a propriedade
    mudou de nome.
    """
    try:
        return notion.data_sources.query(data_source_id=data_source_id, filter=filtro)
    except APIResponseError as e:
        if "Could not find property" in str(e):
            try:
                ds = notion.data_sources.retrieve(data_source_id=data_source_id)
                propriedades = ", ".join(sorted(ds.get("properties", {}).keys())) or "(nenhuma)"
            except APIResponseError:
                propriedades = "(não foi possível listar as propriedades — ver erro original acima)"
            print(
                f"  ✖ Erro ao consultar '{contexto}' (data_source {data_source_id}): {e}\n"
                f"    Propriedades reais desta base: {propriedades}\n"
                f"    Se a propriedade esperada não aparecer na lista acima, a secret do GitHub "
                f"para '{contexto}' provavelmente aponta para o database ID errado — confira em "
                f"Settings → Secrets and variables → Actions."
            )
        raise


# ── Busca de dados ────────────────────────────────────────────────────────────

def buscar_coletas_youtube() -> tuple[list, str]:
    """Lê as linhas da base COLETAS YOUTUBE (CATEGORIA=CONCORRÊNCIA) do mês atual."""
    data_source_id = resolver_data_source_id(NOTION_COLETAS_DB_ID)
    resp = _query_com_diagnostico(
        data_source_id,
        {
            "and": [
                {"property": "CATEGORIA", "select": {"equals": "CONCORRÊNCIA"}},
                {"property": "Mês",       "select": {"equals": MES_ATUAL_PT}},
            ]
        },
        "COLETAS YOUTUBE (NOTION_COLETAS_DB_ID)"
    )
    linhas = resp.get("results", [])
    textos = []
    for page in linhas:
        props = page["properties"]
        canal_prop = props.get("Canal", {}).get("title", [])
        nome = canal_prop[0]["text"]["content"] if canal_prop else "canal sem nome"

        inscritos     = props.get("Inscritos", {}).get("number")
        total_videos  = props.get("Total Vídeos", {}).get("number")
        top_video_txt = "".join(
            rt.get("text", {}).get("content", "")
            for rt in props.get("Top Vídeo", {}).get("rich_text", [])
        )
        top_video_views = props.get("Top Vídeo Views", {}).get("number")

        linha = f"**{nome}** — {inscritos if inscritos is not None else '?'} inscritos, {total_videos if total_videos is not None else '?'} vídeos totais"
        if top_video_txt:
            linha += f"\n  Top vídeo do mês: \"{top_video_txt}\" ({top_video_views if top_video_views is not None else '?'} views)"
        textos.append(linha)

    conteudo = "\n\n".join(textos) if textos else "Nenhuma coleta YouTube disponível este mês."
    return linhas, conteudo


def buscar_observacoes_instagram() -> tuple[list, str]:
    """Notas manuais em texto sobre concorrência no Instagram, em FICHIERS INSTAGRAM."""
    data_source_id = resolver_data_source_id(NOTION_DB_ID)
    resp = _query_com_diagnostico(
        data_source_id,
        {
            "and": [
                {"property": "CATEGORIA",  "select": {"equals": "CONCORRÊNCIA"}},
                {"property": "PLATAFORMA", "select": {"equals": "INSTAGRAM"}},
                {"property": "STATUS",     "select": {"equals": "NOVO"}}
            ]
        },
        "FICHIERS INSTAGRAM (NOTION_DB_ID)"
    )

    entradas = resp.get("results", [])
    textos = []

    for e in entradas:
        nome_prop = e["properties"].get("Name", {}).get("title", [])
        nome = nome_prop[0]["text"]["content"] if nome_prop else "sem título"

        texto_prop = e["properties"].get("Texte", {}).get("rich_text", [])
        texto = "".join(rt.get("text", {}).get("content", "") for rt in texto_prop)

        textos.append(f"**{nome}**\n{texto}")

    conteudo = "\n\n---\n\n".join(textos) if textos else "Nenhuma observação manual depositada."
    return entradas, conteudo


def _texto_rt(props: dict, campo: str) -> str:
    return "".join(rt.get("text", {}).get("content", "") for rt in props.get(campo, {}).get("rich_text", []))


def buscar_prints_concorrencia_instagram() -> tuple[list, str]:
    """Prints de concorrentes já transcritos pela Claude, em Inputs Benchmark Instagram."""
    data_source_id = resolver_data_source_id(NOTION_DB_IG)
    resp = _query_com_diagnostico(
        data_source_id,
        {
            "and": [
                {"property": "CATEGORIA",  "select": {"equals": "CONCORRÊNCIA"}},
                {"property": "PLATAFORMA", "select": {"equals": "INSTAGRAM"}},
                {"property": "STATUS",     "select": {"equals": "Analisado"}}
            ]
        },
        "Inputs Benchmark Instagram (NOTION_DB_IG)"
    )

    entradas = resp.get("results", [])
    textos = []

    for e in entradas:
        props = e["properties"]
        nome_prop = props.get("Name", {}).get("title", [])
        nome = nome_prop[0]["text"]["content"] if nome_prop else "sem título"

        perfil = _texto_rt(props, "Perfil Concorrente")
        formato = (props.get("Formato do Post", {}).get("select") or {}).get("name", "")
        tema = _texto_rt(props, "Tema do Concorrente")
        gancho = _texto_rt(props, "Gancho")
        adaptar = _texto_rt(props, "O Que Dá Pra Adaptar")
        texte = _texto_rt(props, "Texte")

        linha = f"**{nome}**"
        if perfil:
            linha += f" ({perfil})"
        if formato:
            linha += f" — formato: {formato}"
        if tema:
            linha += f"\n  Tema: {tema}"
        if gancho:
            linha += f"\n  Gancho: {gancho}"
        if texte:
            linha += f"\n  Resumo: {texte}"
        if adaptar:
            linha += f"\n  O que dá pra adaptar: {adaptar}"
        textos.append(linha)

    conteudo = "\n\n---\n\n".join(textos) if textos else "Nenhum print de concorrente analisado ainda."
    return entradas, conteudo


# ── Análise Claude ────────────────────────────────────────────────────────────

# Mapeia o título de cada seção "## " do output da Claude para a propriedade
# correspondente na base "🎯 Benchmarks de Concorrência (Mensal)" e para a chave
# correspondente no bloco salvo em insights.json.
SECOES = [
    ("O QUE ESTÁ FUNCIONANDO NO NICHO",      "O Que Está Funcionando",  "o_que_funciona"),
    ("SINAIS DE ALGORITMO DO PERÍODO",       "Sinais de Algoritmo",     "sinais_algoritmo"),
    ("LACUNAS QUE O POR DENTRO PODE OCUPAR", "Lacunas",                 "lacunas_oportunidades"),
    ("O QUE NÃO FAZER",                      "O Que Não Fazer",         "o_que_nao_fazer"),
    ("RECOMENDAÇÃO EDITORIAL DO MÊS",        "Recomendação Editorial",  "recomendacao_editorial"),
]


def analisar_com_claude(youtube_data: str, instagram_data: str) -> str:
    prompt = f"""Você é o sistema editorial do canal Por Dentro — canal de uma imigrante brasileira na França que explica como a França realmente funciona: trabalho, saúde, burocracia, moradia, cultura.

Posicionamento: observador, lúcido, educativo. Nunca romantiza nem catastrofiza. Nunca clickbait no corpo do conteúdo.

Analise os dados de concorrência abaixo e gere o BENCHMARK CONCORRÊNCIA de {MES_LABEL}.

## DADOS YOUTUBE (coletados automaticamente das últimas semanas)
{youtube_data}

## DADOS INSTAGRAM (notas manuais + prints de concorrentes analisados)
{instagram_data}

---

Gere o output na estrutura abaixo, usando EXATAMENTE esses títulos de seção (com "## "), nesta ordem — eles são usados para preencher colunas de uma base estruturada, então não mude o texto dos títulos. Seja específico, use dados concretos, posicione tudo para o Por Dentro.

## O QUE ESTÁ FUNCIONANDO NO NICHO
3 tendências com exemplos concretos dos dados acima.

## SINAIS DE ALGORITMO DO PERÍODO
Padrões de títulos, thumbnails, frequência e formato que aparecem nos dados.

## LACUNAS QUE O POR DENTRO PODE OCUPAR
3 oportunidades específicas que nenhum concorrente está cobrindo bem agora.

## O QUE NÃO FAZER
Temas saturados ou formatos que não fazem sentido para o posicionamento do Por Dentro.

## RECOMENDAÇÃO EDITORIAL DO MÊS
Uma decisão clara e acionável para o próximo calendário editorial."""

    resp = claude.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}]
    )
    return resp.content[0].text


def parsear_secoes(texto: str) -> dict:
    """Quebra a resposta da Claude em {título_da_seção: conteúdo}, usando os headings '## '."""
    blocos = {}
    atual = None
    linhas_atual = []
    for linha in texto.split("\n"):
        if linha.strip().startswith("## "):
            if atual is not None:
                blocos[atual] = "\n".join(linhas_atual).strip()
            atual = linha.strip()[3:].strip()
            linhas_atual = []
        else:
            linhas_atual.append(linha)
    if atual is not None:
        blocos[atual] = "\n".join(linhas_atual).strip()
    return blocos


# ── Salvar em "🎯 Benchmarks de Concorrência (Mensal)" ───────────────────────

def _rt(texto: str) -> dict:
    return {"rich_text": [{"text": {"content": (texto or "")[:2000]}}]}


def _title(texto: str) -> dict:
    return {"title": [{"text": {"content": texto}}]}


def _montar_properties(secoes: dict, total_youtube: int, total_instagram: int) -> dict:
    properties = {
        "Mês":                              _title(MES_LABEL),
        "ID Mês":                           _rt(MES_ID),
        "Data da Rodada":                   {"date": {"start": AGORA.strftime("%Y-%m-%d")}},
        "Status":                           {"select": {"name": "Novo"}},
        "Total Canais YouTube Analisados":  {"number": total_youtube},
        "Total Observações Instagram":      {"number": total_instagram},
    }
    for titulo_prompt, propriedade, _chave_json in SECOES:
        properties[propriedade] = _rt(secoes.get(titulo_prompt, ""))
    return properties


def _buscar_linha_mes_existente(data_source_id: str):
    """Procura uma linha já existente para MES_ID, para atualizar em vez de duplicar."""
    resp = notion.data_sources.query(
        data_source_id=data_source_id,
        filter={"property": "ID Mês", "rich_text": {"equals": MES_ID}}
    )
    resultados = resp.get("results", [])
    return resultados[0]["id"] if resultados else None


def salvar_analise_na_base(secoes: dict, total_youtube: int, total_instagram: int):
    """
    Salva o benchmark como uma LINHA em "🎯 Benchmarks de Concorrência (Mensal)".
    Reprocessar o mesmo mês ATUALIZA a linha existente (chave "ID Mês"), em vez de
    criar uma nova — protege contra duplicatas mesmo se o cron disparar mais de uma
    vez no mesmo mês (ex.: reexecução manual, ou falha de agendamento).
    """
    properties = _montar_properties(secoes, total_youtube, total_instagram)

    try:
        data_source_id = resolver_data_source_id(NOTION_BENCHMARKS_DB_ID)
        existente = _buscar_linha_mes_existente(data_source_id)

        if existente:
            notion.pages.update(page_id=existente, properties=properties)
            print(f"  ✓ Linha atualizada em Benchmarks de Concorrência: {MES_LABEL}")
        else:
            notion.pages.create(parent={"data_source_id": data_source_id}, properties=properties)
            print(f"  ✓ Linha criada em Benchmarks de Concorrência: {MES_LABEL}")
    except APIResponseError as e:
        if "archived" in str(e).lower():
            print(
                "  ⚠ A base NOTION_BENCHMARKS_DB_ID (ou a linha do mês) está arquivada no Notion. "
                "Abra '🎯 Benchmarks de Concorrência (Mensal)' e restaure — pulando salvar nesta rodada."
            )
            return
        raise


# ── Salvar no insights.json (alimenta o dashboard) ───────────────────────────

def salvar_no_insights_json(secoes: dict, total_youtube: int, total_instagram: int):
    """
    Salva o mesmo benchmark em insights.json, bloco "concorrencia" — mesmo padrão
    de bootstrap defensivo e upsert por id usado em
    analyze_audiencia.salvar_bilan_no_insights_json.
    """
    path = Path(INSIGHTS_JSON_PATH)

    if not path.exists():
        print(f"  ⚠ {INSIGHTS_JSON_PATH} não existe no repositório — criando esqueleto mínimo.")
        data = {"concorrencia": []}
    else:
        data = json.loads(path.read_text(encoding="utf-8"))
        data.setdefault("concorrencia", [])

    entrada = {
        "id": MES_ID,
        "periodo": MES_LABEL,
        "gerado_em": AGORA.strftime("%Y-%m-%d"),
        "canais_analisados": {"youtube": total_youtube, "instagram": total_instagram},
    }
    for titulo_prompt, _propriedade, chave_json in SECOES:
        entrada[chave_json] = secoes.get(titulo_prompt, "")

    # Reprocessar o mesmo mês substitui a entrada anterior em vez de duplicar.
    data["concorrencia"] = [c for c in data["concorrencia"] if c.get("id") != MES_ID]
    data["concorrencia"].append(entrada)

    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  ✓ Atualizado {INSIGHTS_JSON_PATH} (concorrencia: {len(data['concorrencia'])} rodada(s))")


def marcar_processado(page_id: str):
    try:
        notion.pages.update(
            page_id=page_id,
            properties={"STATUS": {"select": {"name": "PROCESSADO"}}}
        )
    except APIResponseError as e:
        if "archived" in str(e).lower():
            print(f"  ⚠ Página {page_id} está arquivada (lixeira) no Notion — pulando.")
        else:
            raise


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"\n=== Análise de Concorrência — {MES_LABEL} ===\n")

    print("Buscando coletas YouTube do mês (base COLETAS YOUTUBE)...")
    linhas_youtube, youtube_data = buscar_coletas_youtube()
    print(f"  {len(linhas_youtube)} canal(is) encontrado(s).")

    print("Buscando observações manuais de Instagram (base FICHIERS INSTAGRAM)...")
    entradas_ig, instagram_texto = buscar_observacoes_instagram()
    print(f"  {len(entradas_ig)} observação(ões) manual(is) encontrada(s).")

    print("Buscando prints de concorrentes analisados (base Inputs Benchmark Instagram)...")
    entradas_prints, prints_texto = buscar_prints_concorrencia_instagram()
    print(f"  {len(entradas_prints)} print(s) encontrado(s).")

    total_instagram = len(entradas_ig) + len(entradas_prints)
    instagram_data = (
        f"### Notas manuais (FICHIERS INSTAGRAM)\n{instagram_texto}\n\n"
        f"### Prints de concorrentes analisados (Inputs Benchmark Instagram)\n{prints_texto}"
    )

    if not linhas_youtube and total_instagram == 0:
        print("Nenhum dado de concorrência disponível este mês — pulando geração de benchmark.")
        return

    print("Enviando para Claude...")
    analise = analisar_com_claude(youtube_data, instagram_data)
    secoes = parsear_secoes(analise)

    print("Salvando em 🎯 Benchmarks de Concorrência (Mensal)...")
    salvar_analise_na_base(secoes, len(linhas_youtube), total_instagram)

    print("Atualizando insights.json (dashboard)...")
    salvar_no_insights_json(secoes, len(linhas_youtube), total_instagram)

    print("Marcando entradas usadas como processadas...")
    for e in entradas_ig:
        marcar_processado(e["id"])
    for e in entradas_prints:
        marcar_processado(e["id"])

    print("\n=== Análise de concorrência concluída ===")


if __name__ == "__main__":
    main()
