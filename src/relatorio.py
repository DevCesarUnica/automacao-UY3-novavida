"""Resumo legivel por humano de cada ciclo da automacao.

logs/automacao.log ja tem tudo, mas e um log tecnico continuo, dificil de
ler rapido pra saber "o ciclo das 10h deu certo ou nao?". Este modulo
escreve um SEGUNDO arquivo, logs/resumo_ciclos.txt, com um bloco por ciclo,
cada etapa marcada com OK/FALHOU/PULADO e um resultado geral no topo -
pensado pra abrir e entender em 5 segundos.

Uso: orchestrator.py cria um ResumoCiclo() no inicio de run_ciclo(), chama
.ok()/.pulado()/.falhou() em cada etapa, e .salvar() num finally (assim o
resumo sai mesmo se o ciclo quebrar no meio).
"""
from __future__ import annotations

from datetime import datetime

from config import settings

CAMINHO_RESUMO = settings.LOGS_DIR / "resumo_ciclos.txt"


class ResumoCiclo:
    def __init__(self) -> None:
        self.inicio = datetime.now()
        self.etapas: list[str] = []
        self.erro_geral: str | None = None

    def ok(self, texto: str) -> None:
        self.etapas.append(f"[OK]      {texto}")

    def pulado(self, texto: str) -> None:
        self.etapas.append(f"[PULADO]  {texto}")

    def falhou(self, texto: str) -> None:
        self.etapas.append(f"[FALHOU]  {texto}")

    def erro(self, excecao: BaseException) -> None:
        self.erro_geral = f"{type(excecao).__name__}: {excecao}"

    def salvar(self) -> None:
        fim = datetime.now()
        duracao = (fim - self.inicio).total_seconds()
        teve_falha = self.erro_geral is not None or any(
            e.startswith("[FALHOU]") for e in self.etapas
        )
        status_geral = "COM FALHAS" if teve_falha else "SUCESSO"

        linhas = [
            "=" * 80,
            f"CICLO {self.inicio:%d/%m/%Y %H:%M:%S} -> {fim:%H:%M:%S} "
            f"({duracao:.0f}s) - {status_geral}",
            "=" * 80,
        ]
        linhas.extend(self.etapas or ["(nenhuma etapa chegou a rodar)"])
        if self.erro_geral:
            linhas.append(f"[ERRO GERAL] {self.erro_geral}")
        linhas.append("")

        with open(CAMINHO_RESUMO, "a", encoding="utf-8") as f:
            f.write("\n".join(linhas) + "\n")
