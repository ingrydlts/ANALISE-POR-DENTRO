"""
MÓDULO 3 — Coleta Semanal YouTube
Roda toda sexta à noite via GitHub Actions.

Fluxo:
1. Lê entradas CONCORRÊNCIA + YOUTUBE + NOVO do Notion (FICHIERS INSTAGRAM)
2. Para cada entrada, busca dados do canal no YouTube Data API
3. Salva os dados coletados como nova linha na base COLETAS YOUTUBE
4. Marca a entrada original como PROCESSADO

Variáveis de ambiente esperadas:
  NOTION_TOKEN, YOUTUBE_API_KEY, NOTION_DB_ID, NOTION_COLETAS_DB_ID

Nota de correção (05/07/2026): este script antes também exigia NOTION_INTELIGENCIA_PAGE_ID
só para uma checagem de sanidade (a variável nunca era usada para gravar nada) — e essa página
estava deletada no Notion, então a checagem não protegia nada de verdade. Trocamos a checagem
para validar diretamente NOTION_COLETAS_DB_ID, que é o destino real dos dados. Também migramos
a consulta em FICHIERS INSTAGRAM para a API de data sources (mesma usada pelos scripts de
audiência), unificando a versão da API Notion usada em todo o repositório.
"""

import os
import re
import time
from datetime import datetime, timezone
from dotenv import load_dotenv
from notion_client import Client as NotionClient
from notion_client.errors import APIResponseError
from googleapiclient.discovery import build

load_dotenv()

# ── Configuração ──────────────────────────────────────────────────────────────
NOTION_TOKEN         = os.environ.get("NOTION_TOKEN")
YOUTUBE_API_KEY      = os.environ.get("YOUTUBE_API_KEY")
NOTION_DB_ID         = os.environ.get("NOTION_DB_ID")           # FICHIERS INSTAGRAM
NOTION_COLETAS_DB_ID = os.environ.get("NOTION_COLETAS_DB_ID")   # COLETAS YOUTUBE

if not all([NOTION_TOKEN, YOUTUBE_API_KEY, NOTION_DB_ID, NOTION_COLETAS_DB_ID]):
    raise SystemExit("❌ Variável de ambiente ausente. Verifique: NOTION_TOKEN, YOUTUBE_API_KEY, NOTION_DB_ID, NOTION_COLETAS_DB_ID")

notion  = NotionClient(auth=NOTION_TOKEN)
youtube = build("youtube", "v3", developerKey=YOUTUBE_API_KEY)

_data_source_cache = {}


def resolver_data_source_id(database_id: str) -> str:
    """
    Resolve o data_source_id atual de um database (API Notion 2025-09-03+).
    Ver mesma função em analyze_audiencia.py — bancos passaram a ter uma camada
    intermediária de "data sources"; resolvemos dinamicamente a cada execução.
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


# ── Validação do alvo Notion ──────────────────────────────────────────────────
# Confere que a base de destino real (COLETAS YOUTUBE) existe e está acessível
# antes de gastar cota da YouTube API — falha cedo e com mensagem clara.
try:
    resolver_data_source_id(NOTION_COLETAS_DB_ID)
except (APIResponseError, RuntimeError) as e:
    raise SystemExit(
        f"❌ Não foi possível acessar NOTION_COLETAS_DB_ID ({NOTION_COLETAS_DB_ID}): {e}\n"
        "Verifique se a base existe e foi compartilhada com a integração Notion."
    )

MESES_PT = {
    "January": "Janeiro", "February": "Fevereiro", "March": "Março",
    "April": "Abril", "May": "Maio", "June": "Junho",
    "July": "Julho", "August": "Agosto", "September": "Setembro",
    "October": "Outubro", "November": "Novembro", "December": "Dezembro"
}

_now          = datetime.now(timezone.utc)
MES_ATUAL     = _now.strftime("%B %Y")
MES_ATUAL_PT  = MESES_PT[_now.strftime("%B")]


# ── Helpers YouTube ───────────────────────────────────────────────────────────

def extrair_channel_id(url: str) -> str | None:
    """Extrai o channel ID de URLs do YouTube (formatos /channel/, @handle e /c/ legado)."""
    match = re.search(r'youtube\.com/channel/(UC[\w-]+)', url)
    if match:
        return match.group(1)

    match = re.search(r'youtube\.com/@([\w.-]+)', url)
    if match:
        handle = match.group(1)
        resp = youtube.channels().list(part="id", forHandle=handle).execute()
        items = resp.get("items", [])
        return items[0]["id"] if items else None

    match = re.search(r'youtube\.com/(?:c|user)/([\w.-]+)', url)
    if match:
        nome = match.group(1)
        resp = youtube.search().list(
            part="snippet",
            q=nome,
            type="channel",
            maxResults=1
        ).execute()
        items = resp.get("items", [])
        return items[0]["snippet"]["channelId"] if items else None

    return None


def buscar_dados_canal(channel_id: str) -> dict | None:
    """Busca informações, thumbnail e últimos 10 vídeos do canal."""
    canal_resp = youtube.channels().list(
        part="snippet,contentDetails,statistics",
        id=channel_id
    ).execute()

    items = canal_resp.get("items", [])
    if not items:
        return None

    canal      = items[0]
    uploads_id = canal["contentDetails"]["relatedPlaylists"]["uploads"]
    stats      = canal.get("statistics", {})
    thumbnails = canal["snippet"].get("thumbnails", {})

    thumbnail_url = (
        thumbnails.get("high", {}).get("url") or
        thumbnails.get("medium", {}).get("url") or
        thumbnails.get("default", {}).get("url") or ""
    )

    playlist_resp = youtube.playlistItems().list(
        part="contentDetails",
        playlistId=uploads_id,
        maxResults=10
    ).execute()

    video_ids = [i["contentDetails"]["videoId"] for i in playlist_resp.get("items", [])]

    videos = []
    if video_ids:
        videos_resp = youtube.videos().list(
            part="snippet,statistics",
            id=",".join(video_ids)
        ).execute()

        for v in videos_resp.get("items", []):
            videos.append({
                "titulo":      v["snippet"]["title"],
                "publicado":   v["snippet"]["publishedAt"][:10],
                "views":       int(v["statistics"].get("viewCount", 0)),
                "likes":       int(v["statistics"].get("likeCount", 0)),
                "comentarios": int(v["statistics"].get("commentCount", 0)),
                "url":         f"https://youtube.com/watch?v={v['id']}",
                "thumbnail":   v["snippet"].get("thumbnails", {}).get("high", {}).get("url", "")
            })

    top_video = max(videos, key=lambda v: v["views"]) if videos else None

    return {
        "nome":          canal["snippet"]["title"],
        "inscritos":     int(stats.get("subscriberCount", 0)),
        "total_videos":  int(stats.get("videoCount", 0)),
        "thumbnail_url": thumbnail_url,
        "top_video":     top_video,
        "videos":        videos
    }


# ── Helpers Notion ────────────────────────────────────────────────────────────

def buscar_entradas_novas() -> list:
    """Retorna entradas CONCORRÊNCIA + YOUTUBE + NOVO do banco FICHIERS INSTAGRAM."""
    data_source_id = resolver_data_source_id(NOTION_DB_ID)
    resp = notion.data_sources.query(
        data_source_id=data_source_id,
        filter={
            "and": [
                {"property": "CATEGORIA",  "select": {"equals": "CONCORRÊNCIA"}},
                {"property": "PLATAFORMA", "select": {"equals": "YOUTUBE"}},
                {"property": "STATUS",     "select": {"equals": "NOVO"}}
            ]
        }
    )
    return resp.get("results", [])


def marcar_processado(page_id: str):
    try:
        notion.pages.update(
            page_id=page_id,
            properties={"STATUS": {"select": {"name": "PROCESSADO"}}}
        )
    except APIResponseError as e:
        if "archived" in str(e).lower():
            print(f"  ⚠ Página {page_id} está arquivada no Notion — pulando marcação de PROCESSADO.")
        else:
            raise


def montar_blocos_videos(videos: list) -> list:
    """Monta blocos visuais para os vídeos recentes."""
    blocos = [
        {
            "object": "block", "type": "heading_3",
            "heading_3": {"rich_text": [{"text": {"content": "Últimos vídeos"}}]}
        },
        {
            "object": "block", "type": "divider",
            "divider": {}
        }
    ]

    for v in videos:
        if v.get("thumbnail"):
            blocos.append({
                "object": "block", "type": "image",
                "image": {"type": "external", "external": {"url": v["thumbnail"]}}
            })

        blocos.append({
            "object": "block", "type": "callout",
            "callout": {
                "rich_text": [{"text": {"content":
                    f"{v['titulo']}\n"
                    f"📅 {v['publicado']}  •  👁 {v['views']:,} views  •  👍 {v['likes']:,}  •  💬 {v['comentarios']:,}\n"
                    f"🔗 {v['url']}"
                }}],
                "icon": {"emoji": "▶️"},
                "color": "gray_background"
            }
        })

    return blocos


def salvar_coleta_no_notion(dados: dict, url_origem: str, max_retries: int = 3):
    """Cria entrada na database COLETAS YOUTUBE com thumbnail como capa."""
    top = dados.get("top_video") or {}

    capa_url = top.get("thumbnail") or dados.get("thumbnail_url") or ""

    properties = {
        "Canal":           {"title": [{"text": {"content": dados["nome"]}}]},
        "Mês":             {"select": {"name": MES_ATUAL_PT}},
        "Inscritos":       {"number": dados["inscritos"]},
        "Total Vídeos":    {"number": dados["total_videos"]},
        "Canal URL":       {"url": url_origem},
        "CATEGORIA":       {"select": {"name": "CONCORRÊNCIA"}},
    }

    if top:
        properties["Top Vídeo"]       = {"rich_text": [{"text": {"content": top["titulo"]}}]}
        properties["Top Vídeo Views"] = {"number": top["views"]}

    page_body = {
        "parent":     {"database_id": NOTION_COLETAS_DB_ID},
        "properties": properties,
        "children":   montar_blocos_videos(dados["videos"])
    }

    if capa_url:
        page_body["cover"] = {"type": "external", "external": {"url": capa_url}}

    for tentativa in range(1, max_retries + 1):
        try:
            notion.pages.create(**page_body)
            print(f"  ✓ Salvo com thumbnail: {dados['nome']} — {MES_ATUAL_PT}")
            return
        except APIResponseError as e:
            print(f"  ⚠ Erro Notion (tentativa {tentativa}/{max_retries}): {e}")
            if tentativa == max_retries:
                print(f"  ✗ Falha ao salvar {dados['nome']}. Continuando.")
                return
            time.sleep(2 ** tentativa)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"\n=== Coleta YouTube — {MES_ATUAL} ===\n")

    entradas = buscar_entradas_novas()

    if not entradas:
        print("Nenhuma entrada nova para coletar.")
        return

    print(f"{len(entradas)} entrada(s) encontrada(s).\n")

    for entrada in entradas:
        page_id = entrada["id"]

        url_prop = entrada["properties"].get("URL", {}).get("url") or ""
        nome_prop = entrada["properties"].get("Name", {}).get("title", [])
        nome = nome_prop[0]["text"]["content"] if nome_prop else page_id

        print(f"Processando: {nome} ({url_prop})")

        if not url_prop:
            print("  ⚠ Sem URL, pulando.")
            marcar_processado(page_id)
            continue

        channel_id = extrair_channel_id(url_prop)
        if not channel_id:
            print(f"  ⚠ Não foi possível extrair channel ID de: {url_prop}")
            marcar_processado(page_id)
            continue

        dados = buscar_dados_canal(channel_id)
        if not dados:
            print(f"  ⚠ Canal não encontrado para ID: {channel_id}")
            marcar_processado(page_id)
            continue

        salvar_coleta_no_notion(dados, url_origem=url_prop)
        marcar_processado(page_id)

    print("\n=== Coleta concluída ===")


if __name__ == "__main__":
    main()
