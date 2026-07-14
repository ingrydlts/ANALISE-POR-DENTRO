"""
MÓDULO 3 — Enriquecimento de Prints (Audiência + Concorrência)
Roda ANTES do analyze_audiencia.py, na mesma janela de sexta 07:00 (Paris).

Por que este script existe:
  As entradas de "Inputs Benchmark Instagram" (ex-"🗨️ CONVERSAS AUDIÊNCIA (Instagram)")
  chegam como PRINTS (propriedade "Screenshot"), sem texto transcrito. Este script lê
  cada print com visão da Claude e preenche os campos estruturados da linha.

  A partir de 05/07/2026 a base também recebe prints de CONCORRÊNCIA (posts/reels de
  concorrentes no Instagram, pro benchmark mensal), não só de AUDIÊNCIA (DMs/comentários
  da sua audiência) — por isso este script agora ramifica o prompt e os campos
  preenchidos conforme a CATEGORIA de cada entrada.

Fluxo deste script:
1. Busca entradas STATUS=NOVO + "Enviar para Claude"=✓, com CATEGORIA=AUDIÊNCIA ou
   CATEGORIA=CONCORRÊNCIA
2. Para cada entrada, baixa o(s) screenshot(s) (via URL assinada da API do Notion) e
   manda para a Claude com visão:
   - AUDIÊNCIA: transcrição + dor/necessidade, insight, ideia de conteúdo, pilar,
     prioridade, palavras-chave, persona
   - CONCORRÊNCIA: transcrição + perfil do concorrente, formato do post, tema, gancho,
     ideia de adaptação, palavras-chave, prioridade
3. Preenche essas propriedades diretamente na linha e marca STATUS="Analisado"
4. Depois, cada categoria é agregada por um script diferente:
   - AUDIÊNCIA  → analyze_audiencia.py (bilan semanal)
   - CONCORRÊNCIA → analyze_concorrencia.py (benchmark mensal, junto com FICHIERS INSTAGRAM)
   Ambos leem STATUS="Analisado" e marcam STATUS="PROCESSADO" ao final.

Se o parse do JSON falhar ou o download da imagem falhar para uma entrada,
essa entrada específica é pulada (mantém STATUS=NOVO para nova tentativa na
próxima rodada) — não derruba o job inteiro.

Variáveis de ambiente esperadas: NOTION_TOKEN, ANTHROPIC_API_KEY, NOTION_DB_IG
"""

import os
import json
import base64
from datetime import datetime, timezone
import httpx
from dotenv import load_dotenv
from notion_client import Client as NotionClient
from notion_client.errors import APIResponseError
import anthropic

load_dotenv()

NOTION_TOKEN      = os.environ["NOTION_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
NOTION_DB_ID      = os.environ["NOTION_DB_IG"]  # Inputs Benchmark Instagram (audiência + concorrência)

notion = NotionClient(auth=NOTION_TOKEN)
claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

PILARES_VALIDOS     = {"Sistema", "Trajetória", "Identidade", "Sociedade", "viral"}
PRIORIDADES_VALIDAS = {"Alta", "Média", "Baixa"}
PERSONAS_VALIDAS    = {"P01 - Sonhadora", "P02 - Recém-chegada", "P03 - Adaptada", "P04 - Potencial"}
FORMATOS_VALIDOS    = {"Reels", "Carrossel", "Stories", "Post Estático"}

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


def buscar_entradas_para_enriquecer() -> list:
    """Busca entradas prontas para análise: NOVO + (AUDIÊNCIA ou CONCORRÊNCIA) + 'Enviar para Claude' marcado."""
    data_source_id = resolver_data_source_id(NOTION_DB_ID)
    resp = notion.data_sources.query(
        data_source_id=data_source_id,
        filter={
            "and": [
                {"or": [
                    {"property": "CATEGORIA", "select": {"equals": "AUDIÊNCIA"}},
                    {"property": "CATEGORIA", "select": {"equals": "CONCORRÊNCIA"}},
                ]},
                {"property": "STATUS", "select": {"equals": "NOVO"}},
                {"property": "Enviar para Claude", "checkbox": {"equals": True}},
            ]
        }
    )
    return resp.get("results", [])


def _extrair_urls_screenshot(page: dict) -> list:
    arquivos = page["properties"].get("Screenshot", {}).get("files", [])
    urls = []
    for f in arquivos:
        if f.get("type") == "file" and f.get("file", {}).get("url"):
            urls.append(f["file"]["url"])
        elif f.get("type") == "external" and f.get("external", {}).get("url"):
            urls.append(f["external"]["url"])
    return urls


def _baixar_imagem(url: str):
    resp = httpx.get(url, timeout=30, follow_redirects=True)
    resp.raise_for_status()
    media_type = resp.headers.get("content-type", "image/png").split(";")[0].strip()
    if not media_type.startswith("image/"):
        media_type = "image/png"
    return resp.content, media_type


def _montar_blocos_imagem(urls: list) -> list:
    blocos = []
    for url in urls:
        try:
            conteudo, media_type = _baixar_imagem(url)
        except Exception as e:
            print(f"    ⚠ Falha ao baixar um screenshot: {e}")
            continue
        blocos.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": base64.b64encode(conteudo).decode("utf-8")
            }
        })
    return blocos


# ── Schemas e prompts ─────────────────────────────────────────────────────────

ENTRY_SCHEMA_AUDIENCIA_JSON = """{
  "nome_curto": "string (até 60 caracteres)",
  "texte": "string (frase-chave ou resumo fiel da troca, até 300 caracteres)",
  "dor_necessidade": "string",
  "insight_audiencia": "string",
  "ideia_conteudo": "string",
  "pilar_sugerido": "Sistema|Trajetória|Identidade|Sociedade|viral",
  "prioridade": "Alta|Média|Baixa",
  "palavras_chave": "string (3 a 6 palavras separadas por vírgula)",
  "persona": ["P01 - Sonhadora|P02 - Recém-chegada|P03 - Adaptada|P04 - Potencial", "..."]
}"""

ENTRY_SCHEMA_CONCORRENCIA_JSON = """{
  "nome_curto": "string (até 60 caracteres)",
  "texte": "string (resumo fiel do que o concorrente publicou, até 300 caracteres)",
  "perfil_concorrente": "string (@handle ou nome do perfil, se visível — vazio se não der pra saber)",
  "formato_post": "Reels|Carrossel|Stories|Post Estático",
  "tema_concorrente": "string",
  "gancho": "string (a frase ou imagem de abertura usada pelo concorrente)",
  "o_que_da_pra_adaptar": "string (ideia concreta de conteúdo pro Por Dentro inspirada nisso, sem copiar)",
  "palavras_chave": "string (3 a 6 palavras separadas por vírgula)",
  "prioridade": "Alta|Média|Baixa"
}"""


def analisar_print_audiencia_com_claude(urls_screenshot: list, tipo: str, plataforma: str) -> dict:
    blocos_imagem = _montar_blocos_imagem(urls_screenshot)
    if not blocos_imagem:
        return {"erro_parse": True, "texto_bruto": "Nenhuma imagem pôde ser baixada para esta entrada."}

    prompt_texto = f"""Você é o sistema editorial do canal Por Dentro — imigrante brasileira na França, conteúdo sobre trabalho, saúde, burocracia, moradia, cultura.

As imagens acima são print(s) de uma conversa real com a audiência ({tipo}, plataforma {plataforma}). Leia a conversa e responda APENAS com um JSON válido (sem markdown, sem cercas de código, sem texto fora do JSON) no formato exato abaixo. Baseie-se apenas no que está nas imagens — nunca invente.

{ENTRY_SCHEMA_AUDIENCIA_JSON}

Onde:
- nome_curto: título curto do que se trata (vira o nome da linha no Notion)
- texte: a frase-chave ou resumo fiel da troca, citando as palavras da pessoa quando possível
- dor_necessidade: a dor/necessidade real que essa pessoa expressou
- insight_audiencia: o que isso revela sobre a audiência de forma mais ampla
- ideia_conteudo: um reel/carrossel/story concreto que responde a essa dor
- pilar_sugerido: exatamente um entre Sistema, Trajetória, Identidade, Sociedade, viral
- prioridade: Alta se é uma dor recorrente/urgente, Média ou Baixa caso contrário
- palavras_chave: 3 a 6 palavras-chave separadas por vírgula
- persona: uma ou mais entre "P01 - Sonhadora", "P02 - Recém-chegada", "P03 - Adaptada", "P04 - Potencial", conforme o perfil de quem está falando"""

    return _chamar_claude_com_imagens(blocos_imagem, prompt_texto)


def analisar_print_concorrencia_com_claude(urls_screenshot: list, plataforma: str) -> dict:
    blocos_imagem = _montar_blocos_imagem(urls_screenshot)
    if not blocos_imagem:
        return {"erro_parse": True, "texto_bruto": "Nenhuma imagem pôde ser baixada para esta entrada."}

    prompt_texto = f"""Você é o sistema editorial do canal Por Dentro — imigrante brasileira na França, conteúdo sobre trabalho, saúde, burocracia, moradia, cultura.

As imagens acima são print(s) de um post/reel/story de um CONCORRENTE na plataforma {plataforma}. Leia o print e responda APENAS com um JSON válido (sem markdown, sem cercas de código, sem texto fora do JSON) no formato exato abaixo. Baseie-se apenas no que está nas imagens — nunca invente.

{ENTRY_SCHEMA_CONCORRENCIA_JSON}

Onde:
- nome_curto: título curto do que se trata (vira o nome da linha no Notion)
- texte: resumo fiel do post/reel, citando texto visível quando possível
- perfil_concorrente: @handle ou nome do perfil, se visível no print
- formato_post: exatamente um entre Reels, Carrossel, Stories, Post Estático
- tema_concorrente: assunto/tema coberto pelo concorrente
- gancho: a frase ou imagem de abertura usada pelo concorrente pra prender atenção
- o_que_da_pra_adaptar: uma ideia concreta de conteúdo pro Por Dentro inspirada neste post — adaptando pro nosso posicionamento, sem copiar
- palavras_chave: 3 a 6 palavras-chave separadas por vírgula
- prioridade: Alta se é um padrão forte/recorrente que vale testar, Média ou Baixa caso contrário"""

    return _chamar_claude_com_imagens(blocos_imagem, prompt_texto)


def _chamar_claude_com_imagens(blocos_imagem: list, prompt_texto: str) -> dict:
    content = blocos_imagem + [{"type": "text", "text": prompt_texto}]

    resp = claude.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1200,
        messages=[{"role": "user", "content": content}]
    )
    texto = resp.content[0].text.strip()

    if texto.startswith("```"):
        texto = texto.strip("`")
        if texto.lower().startswith("json"):
            texto = texto[4:]
        texto = texto.strip()

    try:
        return json.loads(texto)
    except json.JSONDecodeError:
        return {"erro_parse": True, "texto_bruto": texto}


def _rt(texto: str) -> dict:
    return {"rich_text": [{"text": {"content": (texto or "")[:2000]}}]}


def montar_properties_audiencia(analise: dict, page: dict):
    if analise.get("erro_parse"):
        return None

    props = {
        "Name": {"title": [{"text": {"content": (analise.get("nome_curto") or "Conversa de audiência")[:200]}}]},
        "Texte": _rt(analise.get("texte", "")),
        "Dor/Necessidade Identificada": _rt(analise.get("dor_necessidade", "")),
        "Insight de Audiência": _rt(analise.get("insight_audiencia", "")),
        "Ideia de Conteúdo Gerada": _rt(analise.get("ideia_conteudo", "")),
        "Palavras-chave": _rt(analise.get("palavras_chave", "")),
        "STATUS": {"select": {"name": "Analisado"}},
    }

    pilar = analise.get("pilar_sugerido")
    if pilar in PILARES_VALIDOS:
        props["Pilar Sugerido"] = {"select": {"name": pilar}}

    prioridade = analise.get("prioridade")
    if prioridade in PRIORIDADES_VALIDAS:
        props["Prioridade"] = {"select": {"name": prioridade}}

    personas = [p for p in (analise.get("persona") or []) if p in PERSONAS_VALIDAS]
    if personas:
        props["Persona"] = {"multi_select": [{"name": p} for p in personas]}

    data_atual = page["properties"].get("Data da Coleta", {}).get("date")
    if not data_atual:
        props["Data da Coleta"] = {"date": {"start": datetime.now(timezone.utc).strftime("%Y-%m-%d")}}

    return props


def montar_properties_concorrencia(analise: dict, page: dict):
    if analise.get("erro_parse"):
        return None

    props = {
        "Name": {"title": [{"text": {"content": (analise.get("nome_curto") or "Post de concorrente")[:200]}}]},
        "Texte": _rt(analise.get("texte", "")),
        "Perfil Concorrente": _rt(analise.get("perfil_concorrente", "")),
        "Tema do Concorrente": _rt(analise.get("tema_concorrente", "")),
        "Gancho": _rt(analise.get("gancho", "")),
        "O Que Dá Pra Adaptar": _rt(analise.get("o_que_da_pra_adaptar", "")),
        "Palavras-chave": _rt(analise.get("palavras_chave", "")),
        "STATUS": {"select": {"name": "Analisado"}},
    }

    formato = analise.get("formato_post")
    if formato in FORMATOS_VALIDOS:
        props["Formato do Post"] = {"select": {"name": formato}}

    prioridade = analise.get("prioridade")
    if prioridade in PRIORIDADES_VALIDAS:
        props["Prioridade"] = {"select": {"name": prioridade}}

    data_atual = page["properties"].get("Data da Coleta", {}).get("date")
    if not data_atual:
        props["Data da Coleta"] = {"date": {"start": datetime.now(timezone.utc).strftime("%Y-%m-%d")}}

    return props


def main():
    print("\n=== Enriquecimento de Prints (Audiência + Concorrência) ===\n")

    entradas = buscar_entradas_para_enriquecer()
    if not entradas:
        print("Nenhuma entrada NOVO com 'Enviar para Claude' marcado. Nada para enriquecer.")
        return

    print(f"{len(entradas)} entrada(s) para enriquecer.")

    for page in entradas:
        page_id = page["id"]
        categoria = (page["properties"].get("CATEGORIA", {}).get("select") or {}).get("name", "AUDIÊNCIA")
        tipo = (page["properties"].get("Tipo", {}).get("select") or {}).get("name", "DM")
        plataforma = (page["properties"].get("PLATAFORMA", {}).get("select") or {}).get("name", "INSTAGRAM")
        urls = _extrair_urls_screenshot(page)

        print(f"  → {page_id} [{categoria}] — {len(urls)} imagem(ns) encontrada(s)...")
        if not urls:
            print("    ⚠ Sem screenshot anexado — pulando (mantém NOVO).")
            continue

        if categoria == "CONCORRÊNCIA":
            analise = analisar_print_concorrencia_com_claude(urls, plataforma)
            props = montar_properties_concorrencia(analise, page)
        else:
            analise = analisar_print_audiencia_com_claude(urls, tipo, plataforma)
            props = montar_properties_audiencia(analise, page)

        if props is None:
            print(f"    ⚠ Claude não retornou JSON válido — mantendo NOVO para nova tentativa. "
                  f"Bruto: {str(analise.get('texto_bruto', ''))[:200]}")
            continue

        try:
            notion.pages.update(page_id=page_id, properties=props)
            print("    ✓ Enriquecida e marcada como Analisado.")
        except APIResponseError as e:
            print(f"    ✖ Erro ao atualizar {page_id}: {e}")

    print("\n=== Enriquecimento concluído ===")


if __name__ == "__main__":
    main()
