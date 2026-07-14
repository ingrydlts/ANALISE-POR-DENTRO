"""
Performance Instagram — processa os CSVs diários exportados do Instagram
(Interactions, Visites, Vues, Clics sur un lien, Couverture, Followers en
plus) e faz upsert por data em insights.json["instagram_diario"].

Disparo: workflow "Performance Instagram" roda quando qualquer arquivo é
adicionado em dados/instagram/ (gatilho por path, não por agenda) — a
criadora sobe os CSVs pelo painel-atualizacao.html toda semana/quinzena,
cobrindo o período desde o último upload (os ranges podem se sobrepor;
upsert por data evita duplicar ou perder dado).

Formato dos arquivos (export nativo do Meta/Instagram — Insights → exportar):
  - Codificação UTF-16 LE com BOM (não é um CSV comum)
  - Linha 1: "sep=,"
  - Linha 2: título da métrica entre aspas (varia por arquivo, ignorado aqui
    — o nome do ARQUIVO é que decide a que métrica corresponde)
  - Linha 3: cabeçalho "Date","Primary"
  - Linhas seguintes: "AAAA-MM-DDT00:00:00","valor"

Variáveis de ambiente esperadas:
  INSIGHTS_JSON_PATH (opcional — default "insights.json")
  DADOS_INSTAGRAM_DIR (opcional — default "dados/instagram")
"""

from __future__ import annotations

import csv
import io
import json
import os
from datetime import datetime, timezone
from pathlib import Path

INSIGHTS_JSON_PATH = os.environ.get("INSIGHTS_JSON_PATH", "insights.json")
DADOS_INSTAGRAM_DIR = os.environ.get("DADOS_INSTAGRAM_DIR", "dados/instagram")

# Nome do arquivo (como a Meta exporta, em francês) -> chave da métrica em instagram_diario.
# Comparação é por "contém", case-insensitive, pra tolerar variações de acentuação/maiúsculas.
MAPA_ARQUIVOS = {
    "interactions": "interacoes",
    "visites": "visitas_perfil",
    "vues": "vues",
    "clics": "cliques_link",
    "couverture": "alcance",
    "followers": "seguidores_ganhos",
}


def _decodificar(caminho: Path) -> str:
    """Os exports da Meta vêm em UTF-16 LE com BOM — não é um .decode() padrão."""
    dados = caminho.read_bytes()
    if dados[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return dados.decode("utf-16")
    # fallback defensivo, caso algum export venha em UTF-8 normal
    return dados.decode("utf-8-sig")


def _metrica_do_nome_arquivo(nome_arquivo: str) -> str | None:
    nome = nome_arquivo.lower()
    for pista, chave in MAPA_ARQUIVOS.items():
        if pista in nome:
            return chave
    return None


def _parsear_csv_meta(texto: str) -> dict:
    """Retorna {data 'AAAA-MM-DD': valor int}. Pula a linha 'sep=,', o título
    e o cabeçalho — identifica a primeira linha de dado real pelo formato da
    data (começa com dígito após remover aspas)."""
    linhas = texto.splitlines()
    valores = {}
    leitor = csv.reader(linhas)
    for linha in leitor:
        if len(linha) != 2:
            continue
        data_bruta, valor_bruto = linha[0].strip(), linha[1].strip()
        if not data_bruta or not data_bruta[0].isdigit():
            continue  # pula "sep=,", título, cabeçalho "Date","Primary"
        data = data_bruta[:10]  # "2026-05-01T00:00:00" -> "2026-05-01"
        try:
            valores[data] = int(float(valor_bruto.replace(",", "")))
        except ValueError:
            continue
    return valores


def processar_arquivos_csv() -> dict:
    """Varre DADOS_INSTAGRAM_DIR (recursivo — cada upload pode ir numa subpasta
    datada) e agrega {data: {metrica: valor}} de todos os CSVs reconhecidos."""
    base = Path(DADOS_INSTAGRAM_DIR)
    if not base.exists():
        print(f"  ⚠ Pasta {DADOS_INSTAGRAM_DIR} não existe neste checkout.")
        return {}

    agregado: dict[str, dict[str, int]] = {}
    arquivos_csv = list(base.rglob("*.csv"))
    print(f"  {len(arquivos_csv)} arquivo(s) CSV encontrado(s) em {DADOS_INSTAGRAM_DIR}.")

    for caminho in arquivos_csv:
        metrica = _metrica_do_nome_arquivo(caminho.name)
        if not metrica:
            print(f"    ⚠ Não reconheci a métrica do arquivo '{caminho.name}' — pulando.")
            continue
        try:
            texto = _decodificar(caminho)
        except Exception as e:
            print(f"    ✖ Falha ao ler '{caminho.name}': {e}")
            continue
        valores = _parsear_csv_meta(texto)
        print(f"    ✓ {caminho.name} -> {metrica} ({len(valores)} dia(s))")
        for data, valor in valores.items():
            agregado.setdefault(data, {})[metrica] = valor

    return agregado


def atualizar_instagram_diario(agregado: dict, data: dict) -> int:
    """Upsert por data em data['instagram_diario'] (dict já carregado do
    insights.json). Um novo upload substitui só os campos/dias que ele traz
    — não apaga métricas de dias que só vieram num upload anterior."""
    diario = {d["data"]: d for d in data.get("instagram_diario", [])}
    atualizados = 0
    for data_str, metricas in agregado.items():
        entrada = diario.get(data_str, {"data": data_str})
        entrada.update(metricas)
        diario[data_str] = entrada
        atualizados += 1

    data["instagram_diario"] = sorted(diario.values(), key=lambda e: e["data"])
    data["instagram_diario_atualizado_em"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return atualizados


# ── Gera check-ins semanais automáticos a partir do diário ──────────────────
# A tabela "semanas" (aba Semanal do dashboard) era só preenchida à mão
# (atualizar_semana.py). Agora que há dado diário real, geramos as semanas
# que ainda não existem — SEM tocar nas que já existem (as de junho têm
# anotação escrita à mão, cruzada com o Notion; nunca sobrescrever).
# Mesmo esquema de "semana" já usado nos ciclos migrados: blocos de 7 dias
# dentro do mês (1–7, 8–14, 15–21, 22–28, 29–fim), id "AAAA-MM-Sn".
MES_ABREV_PT = ["Jan","Fev","Mar","Abr","Mai","Jun","Jul","Ago","Set","Out","Nov","Dez"]


def gerar_semanas_automaticas(data: dict) -> int:
    diario = sorted(data.get("instagram_diario", []), key=lambda e: e["data"])
    if not diario:
        return 0

    por_mes: dict[str, list[dict]] = {}
    for e in diario:
        por_mes.setdefault(e["data"][:7], []).append(e)

    semanas_existentes = {s["id"]: s for s in data.get("semanas", [])}
    novas = 0

    for mes_ref, dias in por_mes.items():
        ano, mes = int(mes_ref[:4]), int(mes_ref[5:7])
        blocos = [(1, 7), (8, 14), (15, 21), (22, 28), (29, 31)]
        for i, (ini, fim) in enumerate(blocos, start=1):
            bloco_dias = [d for d in dias if ini <= int(d["data"][8:10]) <= fim]
            if not bloco_dias:
                continue
            id_semana = f"{mes_ref}-S{i}"
            if id_semana in semanas_existentes:
                continue  # já existe (manual ou gerado antes) — não sobrescreve

            alcance = sum(d.get("alcance") or 0 for d in bloco_dias)
            interacoes = sum(d.get("interacoes") or 0 for d in bloco_dias)
            seguidores = sum(d.get("seguidores_ganhos") or 0 for d in bloco_dias)
            engajamento_pct = round(interacoes / alcance * 100, 1) if alcance else 0.0

            dia_fim_real = int(bloco_dias[-1]["data"][8:10])
            periodo = f"{ini}–{dia_fim_real} {MES_ABREV_PT[mes-1]} {ano}"

            data.setdefault("semanas", []).append({
                "id": id_semana,
                "periodo": periodo,
                "mes_ref": mes_ref,
                "alcance": alcance,
                "interacoes": interacoes,
                "seguidores_ganhos": seguidores,
                "engajamento_pct": engajamento_pct,
                "conta_para_meta": True,
                "origem": "instagram_diario_auto",
                "nota": f"Gerado automaticamente a partir de {len(bloco_dias)} dia(s) de dados reais (instagram_diario)."
            })
            semanas_existentes[id_semana] = True
            novas += 1

    if novas:
        data["semanas"] = sorted(data["semanas"], key=lambda s: s["id"])
    return novas


def main():
    print("\n=== Performance Instagram — processando CSVs ===\n")
    agregado = processar_arquivos_csv()
    if not agregado:
        print("Nenhum dado novo pra processar.")
        return

    path = Path(INSIGHTS_JSON_PATH)
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    atualizados = atualizar_instagram_diario(agregado, data)
    print(f"✓ {atualizados} dia(s) atualizado(s) em instagram_diario ({len(agregado)} dia(s) no total dos arquivos lidos).")

    novas_semanas = gerar_semanas_automaticas(data)
    print(f"✓ {novas_semanas} semana(s) nova(s) gerada(s) automaticamente em 'semanas' (as já existentes não foram tocadas).")

    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n=== Concluído ===")


if __name__ == "__main__":
    main()
