"""
sirius_tarefas.py — Stub de compatibilidade (S.I.R.I.U.S. v5.2)
================================================================
Este arquivo foi absorvido por sirius_paralelo.py, que contém
toda a lógica real. Este stub garante compatibilidade retroativa.

NÃO edite este arquivo — edite sirius_paralelo.py.
"""

from sirius_paralelo import (   # noqa: F401
    GerenciadorTarefas,
    DetectorParalelo,
    SiriusParalelo,
    Tarefa,
    EstadoTarefa,
    PrioridadeTarefa,
    GrupoParalelo,
    Pipeline,
    detectar_tipo_execucao,
    extrair_sub_comandos,
)

__all__ = [
    "GerenciadorTarefas",
    "DetectorParalelo",
    "SiriusParalelo",
    "Tarefa",
    "EstadoTarefa",
    "PrioridadeTarefa",
    "GrupoParalelo",
    "Pipeline",
    "detectar_tipo_execucao",
    "extrair_sub_comandos",
]