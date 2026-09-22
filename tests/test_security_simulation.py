"""
Roda a simulação de ataques como teste automatizado.

Mantém os vetores de segurança sob regressão: qualquer alteração futura que reabra
CSV injection, XSS ou o guardrail de entrada quebra o build.
"""

import asyncio

from security.simulate_attacks import main as simulate


def test_todos_os_vetores_mitigados():
    exit_code = asyncio.run(simulate())
    assert exit_code == 0, "algum vetor de ataque voltou a passar — ver output/security/simulacao.json"
