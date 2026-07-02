#!/usr/bin/env python3
"""
Adiciona um novo check-in semanal ao insights.json do Por Dentro.

Uso:
    python3 atualizar_semana.py

Roda na mesma pasta do insights.json. Faz perguntas simples no terminal,
calcula o engajamento automaticamente e grava o novo bloco dentro do
array "semanas" — sem risco de quebrar o JSON à mão.
"""
import json
from pathlib import Path
from datetime import date

CAMINHO = Path(__file__).parent / "insights.json"


def perguntar_numero(pergunta, permitir_vazio=True):
    while True:
        bruto = input(pergunta).strip().replace(".", "").replace(",", ".")
        if bruto == "" and permitir_vazio:
            return 0
        try:
            return float(bruto) if "." in bruto else int(bruto)
        except ValueError:
            print("  → não entendi como número, tenta de novo.")


def main():
    if not CAMINHO.exists():
        print(f"Não encontrei {CAMINHO}. Rode este script na mesma pasta do insights.json.")
        return

    data = json.loads(CAMINHO.read_text(encoding="utf-8"))
    semanas = data.setdefault("semanas", [])

    print("== Novo check-in semanal — Por Dentro ==\n")
    periodo = input("Período (ex: 6–12 Jul 2026): ").strip()
    mes_ref = input(f"Mês de referência (ex: {date.today().strftime('%Y-%m')}): ").strip() or date.today().strftime("%Y-%m")
    alcance = perguntar_numero("Alcance da semana: ")
    interacoes = perguntar_numero("Interações da semana (curtidas + comentários + saves + compart.): ")
    seguidores_ganhos = perguntar_numero("Seguidores ganhos na semana: ")
    engajamento_pct = round(interacoes / alcance * 100, 1) if alcance else 0.0
    nota = input("Nota rápida — o que aconteceu, o que replicar (opcional): ").strip()

    contador = sum(1 for s in semanas if s.get("mes_ref") == mes_ref) + 1
    novo_id = f"{mes_ref}-S{contador}"

    entrada = {
        "id": novo_id,
        "periodo": periodo,
        "mes_ref": mes_ref,
        "alcance": alcance,
        "interacoes": interacoes,
        "seguidores_ganhos": seguidores_ganhos,
        "engajamento_pct": engajamento_pct,
        "conta_para_meta": True,
        "origem": "check_in_semanal",
        "nota": nota,
    }

    semanas.append(entrada)
    CAMINHO.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n✓ Semana '{novo_id}' adicionada. Engajamento calculado: {engajamento_pct}%")
    print("Abra o index.html (via servidor local, ex: python3 -m http.server) para ver o dashboard atualizado.")


if __name__ == "__main__":
    main()
