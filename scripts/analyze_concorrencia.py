"""
MÓDULO 3 — Análise de Concorrência
Roda na primeira segunda-feira do mês via GitHub Actions (guard mensal — ver
analise-concorrencia.yml, que dispara toda segunda e pula quando não é a
primeira).

Fluxo (mesma lógica de analyze_audiencia.py — só troca a tag AUDIÊNCIA por
CONCORRÊNCIA, e a cadência de semanal pra mensal):
1. Busca prints de concorrentes já transcritos pela Claude, em "Inputs
   Benchmark Instagram" (CATEGORIA=CONCORRÊNCIA + STATUS=Analisado — já
   enriquecidos por enriquecer_conversas_audiencia.py)
2. Envia tudo para Claude, que gera o BENCHMARK do mês em seções fixas (##) —
   incluindo insights por concorrente (não só agregado do nicho) e sugestões
   concretas de conteúdo pro Por Dentro derivadas das lacunas identificadas
3. Salva/atualiza UMA LINHA na base "🎯 Benchmarks de Concorrência (Mensal)"
   (chave "ID Mês" — reprocessar o mesmo mês atualiza a linha em vez de duplicar)
4. Salva o mesmo benchmark em insights.json — bloco estruturado em "concorrencia",
   para o dashboard (index.html) exibir na aba Concorrência
5. Marca as entradas usadas como PROCESSADO

Variáveis de ambiente esperadas:
  NOTION_TOKEN, ANTHROPIC_API_KEY, NOTION_DB_IG, NOTION_BENCHMARKS_DB_ID
  INSIGHTS_JSON_PATH (opcional — caminho do insights.json no repo, default "insights.json")

Nota de simplificação (31/07/2026): antes desta versão, este script juntava
dados de TRÊS fontes — COLETAS YOUTUBE (canais concorrentes coletados por
collect.py), notas manuais em FICHIERS INSTAGRAM (STATUS=NOVO) e prints em
Inputs Benchmark Instagram (STATUS=Analisado) — usando 3 secrets adicionais
(NOTION_DB_ID, NOTION_COLETAS_DB_ID). Isso quebrou em produção com "Could not
find property with name or id: CATEGORIA" (uma dessas secrets apontava pro
database ID errado) e era mais complexo do que o uso real no dia a dia: a
única fonte de fato alimentada é "Inputs Benchmark Instagram" — a MESMA base
que analyze_audiencia.py já lê, só filtrando CATEGORIA=CONCORRÊNCIA em vez de
AUDIÊNCIA. Simplificado para espelhar exatamente essa lógica: uma fonte, um
secret (NOTION_DB_IG). "Total Canais YouTube Analisados" fica fixo em 0 nesta
versão — se a coleta de canais do YouTube (COLETAS YOUTUBE) voltar a ser
necessária no benchmark, reintroduzir como fonte adicional aqui.
"""

import os
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv
from notion_client import Client as NotionClient
from notion_client.errors import APIResponseError
import anthropic

load_dotenv()

# ── Configuração ──────────────────────────────────────────────────────────────
NOTION_TOKEN            = os.environ["NOTION_TOKEN"]
ANTHROPIC_API_KEY       = os.environ["ANTHROPIC_API_KEY"]
NOTION_DB_IG            = os.environ["NOTION_DB_IG"]              # Inputs Benchmark Instagram
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
MES_ATUAL_PT = MESES_PT[AGORA.strftime("%B")]              # ex: "Julho" — usado só pro rótulo
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
    propriedades reais dessa base nos logs antes de relançar o erro — assim dá
    pra ver na hora, sem entrar no Notion, se a secret aponta pro database
    errado ou se a propriedade mudou de nome.
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

def _texto_rt(props: dict, campo: str) -> str:
    return "".join(rt.get("text", {}).get("content", "") for rt in props.get(campo, {}).get("rich_text", []))


def buscar_prints_concorrencia_instagram() -> list:
    """
    Busca prints de concorrentes já transcritos pela Claude em "Inputs
    Benchmark Instagram" (CATEGORIA=CONCORRÊNCIA + STATUS=Analisado) — mesma
    base e mesmo padrão de analyze_audiencia.buscar_entradas_audiencia, que
    usa CATEGORIA=AUDIÊNCIA.
    """
    data_source_id = resolver_data_source_id(NOTION_DB_IG)
    resp = _query_com_diagnostico(
        data_source_id,
        {
            "and": [
                {"property": "CATEGORIA", "select": {"equals": "CONCORRÊNCIA"}},
                {"property": "STATUS",    "select": {"equals": "Analisado"}}
            ]
        },
        "Inputs Benchmark Instagram (NOTION_DB_IG)"
    )

    entradas = []
    for page in resp.get("results", []):
        props = page["properties"]
        nome_prop = props.get("Name", {}).get("title", [])
        nome = nome_prop[0]["text"]["content"] if nome_prop else "sem título"

        plataforma_prop = props.get("PLATAFORMA", {}).get("select")
        plataforma = plataforma_prop["name"] if plataforma_prop else "DESCONHECIDA"

        perfil  = _texto_rt(props, "Perfil Concorrente")
        formato = (props.get("Formato do Post", {}).get("select") or {}).get("name", "")
        tema    = _texto_rt(props, "Tema do Concorrente")
        gancho  = _texto_rt(props, "Gancho")
        adaptar = _texto_rt(props, "O Que Dá Pra Adaptar")
        texte   = _texto_rt(props, "Texte")

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

        entradas.append({
            "id": page["id"], "nome": nome, "texto": linha, "plataforma": plataforma,
            "perfil": perfil, "tema": tema, "formato": formato, "gancho": gancho, "adaptar": adaptar,
        })

    return entradas


def formatar_entradas(entradas: list) -> str:
    """Organiza entradas por plataforma para o prompt (mesmo padrão de analyze_audiencia.py)."""
    instagram = [e for e in entradas if e["plataforma"] == "INSTAGRAM"]
    youtube   = [e for e in entradas if e["plataforma"] == "YOUTUBE"]
    outro     = [e for e in entradas if e["plataforma"] not in ("INSTAGRAM", "YOUTUBE")]

    partes = []
    if instagram:
        partes.append("### Instagram\n" + "\n\n".join(e["texto"] for e in instagram))
    if youtube:
        partes.append("### YouTube\n" + "\n\n".join(e["texto"] for e in youtube))
    if outro:
        partes.append("### Outros\n" + "\n\n".join(f"{e['texto']} [{e['plataforma']}]" for e in outro))

    return "\n\n---\n\n".join(partes) if partes else "Nenhum print de concorrente analisado ainda."


# ── Análise Claude ────────────────────────────────────────────────────────────

# Mapeia o título de cada seção "## " do output da Claude para a propriedade
# correspondente na base "🎯 Benchmarks de Concorrência (Mensal)" e para a chave
# correspondente no bloco salvo em insights.json.
SECOES = [
    ("O QUE ESTÁ FUNCIONANDO NO NICHO",      "O Que Está Funcionando",     "o_que_funciona"),
    ("INSIGHTS POR CONCORRENTE",             "Insights por Concorrente",   "insights_por_concorrente"),
    ("SINAIS DE ALGORITMO DO PERÍODO",       "Sinais de Algoritmo",        "sinais_algoritmo"),
    ("LACUNAS QUE O POR DENTRO PODE OCUPAR", "Lacunas",                    "lacunas_oportunidades"),
    ("SUGESTÕES DE CONTEÚDO",                "Sugestões de Conteúdo",      "sugestoes_conteudo"),
    ("O QUE NÃO FAZER",                      "O Que Não Fazer",            "o_que_nao_fazer"),
    ("RECOMENDAÇÃO EDITORIAL DO MÊS",        "Recomendação Editorial",     "recomendacao_editorial"),
]

# Teto de segurança no texto agregado enviado à Claude — mesmo padrão de
# analyze_audiencia.py (ali evita um 413 "Request Too Large" num backlog grande).
LIMITE_CARACTERES_DADOS = 100_000


def analisar_com_claude(dados_concorrencia: str) -> Optional[str]:
    if len(dados_concorrencia) > LIMITE_CARACTERES_DADOS:
        print(f"  ⚠ Dados de concorrência ({len(dados_concorrencia)} caracteres) excedem o teto de "
              f"{LIMITE_CARACTERES_DADOS} — truncando para caber num único request à Claude.")
        dados_concorrencia = dados_concorrencia[:LIMITE_CARACTERES_DADOS] + "\n\n[...truncado — excedeu o limite de tamanho...]"

    prompt = f"""Você é o sistema editorial do canal Por Dentro — canal de uma imigrante brasileira na França que explica como a França realmente funciona: trabalho, saúde, burocracia, moradia, cultura.

Posicionamento: observador, lúcido, educativo. Nunca romantiza nem catastrofiza. Nunca clickbait no corpo do conteúdo.

Analise os dados de concorrência abaixo (prints de posts/reels de concorrentes, já transcritos e comentados) e gere o BENCHMARK CONCORRÊNCIA de {MES_LABEL}.

## DADOS DE CONCORRÊNCIA
{dados_concorrencia}

---

Gere o output na estrutura abaixo, usando EXATAMENTE esses títulos de seção (com "## "), nesta ordem — eles são usados para preencher colunas de uma base estruturada, então não mude o texto dos títulos. Seja específico, use dados concretos, posicione tudo para o Por Dentro.

## O QUE ESTÁ FUNCIONANDO NO NICHO
3 tendências com exemplos concretos dos dados acima.

## INSIGHTS POR CONCORRENTE
Para cada concorrente citado nos dados (pelo nome/perfil), 1-2 frases sobre o que ele especificamente faz bem ou diferente — não repita o "O que está funcionando" geral, seja específico por perfil. Se só houver dados de um concorrente, foque nele.

## SINAIS DE ALGORITMO DO PERÍODO
Padrões de títulos, thumbnails, frequência e formato que aparecem nos dados.

## LACUNAS QUE O POR DENTRO PODE OCUPAR
3 oportunidades específicas que nenhum concorrente está cobrindo bem agora.

## SUGESTÕES DE CONTEÚDO
3 ideias concretas de conteúdo pro Por Dentro, cada uma decorrente de uma lacuna ou insight acima. Para cada uma, no formato "[PLATAFORMA/formato] Título ou ângulo — por que isso faz sentido agora".

## O QUE NÃO FAZER
Temas saturados ou formatos que não fazem sentido para o posicionamento do Por Dentro.

## RECOMENDAÇÃO EDITORIAL DO MÊS
Uma decisão clara e acionável para o próximo calendário editorial."""

    try:
        resp = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=3000,
            messages=[{"role": "user", "content": prompt}]
        )
    except anthropic.APIStatusError as e:
        # Ex.: rate limit, API fora do ar. Não derruba o job — main() evita
        # marcar as entradas como PROCESSADO nesse caso, então elas voltam a
        # ser tentadas na próxima rodada (mesmo padrão de analyze_audiencia.py).
        print(f"  ✖ Erro ao chamar Claude: {e}")
        return None

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


def _montar_properties(secoes: dict, total_entradas: int) -> dict:
    properties = {
        "Mês":                              _title(MES_LABEL),
        "ID Mês":                           _rt(MES_ID),
        "Data da Rodada":                   {"date": {"start": AGORA.strftime("%Y-%m-%d")}},
        "Status":                           {"select": {"name": "Novo"}},
        # Fixo em 0: a coleta de canais do YouTube (COLETAS YOUTUBE) foi
        # removida desta versão — ver nota de simplificação no topo do arquivo.
        "Total Canais YouTube Analisados":  {"number": 0},
        "Total Observações Instagram":      {"number": total_entradas},
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


def salvar_analise_na_base(secoes: dict, total_entradas: int):
    """
    Salva o benchmark como uma LINHA em "🎯 Benchmarks de Concorrência (Mensal)".
    Reprocessar o mesmo mês ATUALIZA a linha existente (chave "ID Mês"), em vez de
    criar uma nova.
    """
    properties = _montar_properties(secoes, total_entradas)

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

def salvar_no_insights_json(secoes: dict, entradas: list):
    """
    Salva o mesmo benchmark em insights.json, bloco "concorrencia" — mesmo padrão
    de bootstrap defensivo e upsert por id usado em
    analyze_audiencia.salvar_bilan_no_insights_json.

    Também grava/atualiza "concorrentes_observados": uma lista PERSISTENTE
    (cresce a cada rodada, upsert por id de página — ao contrário de
    "concorrencia", que guarda uma linha por MÊS) com um registro por print
    de concorrente já analisado. É o que alimenta os chips de "Instagram" no
    dashboard, no mesmo padrão visual dos chips de canais do YouTube
    (canais.json) — adicionado em 31/07/2026.
    """
    path = Path(INSIGHTS_JSON_PATH)
    total_entradas = len(entradas)

    if not path.exists():
        print(f"  ⚠ {INSIGHTS_JSON_PATH} não existe no repositório — criando esqueleto mínimo.")
        data = {"concorrencia": [], "concorrentes_observados": []}
    else:
        data = json.loads(path.read_text(encoding="utf-8"))
        data.setdefault("concorrencia", [])
        data.setdefault("concorrentes_observados", [])

    entrada = {
        "id": MES_ID,
        "periodo": MES_LABEL,
        "gerado_em": AGORA.strftime("%Y-%m-%d"),
        "canais_analisados": {"youtube": 0, "instagram": total_entradas},
    }
    for titulo_prompt, _propriedade, chave_json in SECOES:
        entrada[chave_json] = secoes.get(titulo_prompt, "")

    # Reprocessar o mesmo mês substitui a entrada anterior em vez de duplicar.
    data["concorrencia"] = [c for c in data["concorrencia"] if c.get("id") != MES_ID]
    data["concorrencia"].append(entrada)

    ids_desta_rodada = {e["id"] for e in entradas}
    data["concorrentes_observados"] = [
        c for c in data["concorrentes_observados"] if c.get("id") not in ids_desta_rodada
    ]
    for e in entradas:
        data["concorrentes_observados"].append({
            "id": e["id"],
            "nome": e["nome"],
            "perfil": e.get("perfil", ""),
            "tema": e.get("tema", ""),
            "formato": e.get("formato", ""),
            "gancho": e.get("gancho", ""),
            "adaptar": e.get("adaptar", ""),
            "plataforma": e["plataforma"],
            "mes_id": MES_ID,
        })

    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  ✓ Atualizado {INSIGHTS_JSON_PATH} (concorrencia: {len(data['concorrencia'])} rodada(s), "
          f"concorrentes_observados: {len(data['concorrentes_observados'])} no total)")


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

    print("Buscando prints de concorrentes analisados (base Inputs Benchmark Instagram)...")
    entradas = buscar_prints_concorrencia_instagram()
    print(f"  {len(entradas)} print(s) encontrado(s).")

    if not entradas:
        print("Nenhum dado de concorrência disponível este mês — pulando geração de benchmark.")
        return

    dados_formatados = formatar_entradas(entradas)

    print("Enviando para Claude...")
    analise = analisar_com_claude(dados_formatados)

    if analise is None:
        print("  ⚠ Falha ao gerar análise (ver erro acima) — entradas mantidas como 'Analisado' "
              "para nova tentativa na próxima rodada, em vez de marcar PROCESSADO.")
        return

    secoes = parsear_secoes(analise)

    print("Salvando em 🎯 Benchmarks de Concorrência (Mensal)...")
    salvar_analise_na_base(secoes, len(entradas))

    print("Atualizando insights.json (dashboard)...")
    salvar_no_insights_json(secoes, entradas)

    print("Marcando entradas usadas como processadas...")
    for e in entradas:
        marcar_processado(e["id"])

    print("\n=== Análise de concorrência concluída ===")


if __name__ == "__main__":
    main()
