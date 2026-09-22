import pytest

from guardrails.pii import mask_pii
from guardrails.profanity import sanitize_profanity


def test_mask_cpf():
    assert "[CPF]" in mask_pii("Meu CPF é 123.456.789-00")


def test_profanity():
    assert "***" in sanitize_profanity("Esse atendimento é uma merda")


@pytest.mark.parametrize(
    "texto,placeholder",
    [
        ("Meu CPF é 123.456.789-00", "[CPF]"),
        ("cpf sem pontuação 12345678901", "[CPF/ID]"),
        ("cartão 4111 1111 1111 1111", "[DADO_CARTAO]"),
        ("email joao.silva@email.com", "[EMAIL]"),
        ("chave pix joao@banco.com.br", "[EMAIL]"),
        ("CNPJ 12.345.678/0001-90", "[CNPJ]"),
        ("ligue (11) 98765-4321", "[TELEFONE]"),
        ("whatsapp 11 98765-4321", "[TELEFONE]"),
        ("celular 98765-4321", "[TELEFONE]"),
        ("internacional +55 11 98765-4321", "[TELEFONE]"),
        ("CEP 01310-100", "[CEP]"),
        ("pix 3f2504e0-4f89-41d3-9a0c-0305e82c3301", "[CHAVE_PIX]"),
        ("conta 45678-9", "[DADO_BANCARIO]"),
        ("agência 0001", "[DADO_BANCARIO]"),
    ],
)
def test_mascara_cada_tipo_de_pii(texto, placeholder):
    assert placeholder in mask_pii(texto)


@pytest.mark.parametrize(
    "texto",
    [
        "cobrado R$ 2.000 em 2025 2026",
        "protocolo 2024-1234 aberto",
        "contrato 1234-5678 vencido",
        "taxa de 1,99% ao mês",
        "prazo de 48 horas",
    ],
)
def test_nao_mascara_dado_inofensivo(texto):
    """Mascarar valor, data ou protocolo destruiria a informação útil da reclamação."""
    assert mask_pii(texto) == texto


def test_mascara_pii_composta_em_um_unico_texto():
    texto = (
        "CPF 123.456.789-00, cartão 4111 1111 1111 1111, "
        "tel (11) 98765-4321 e email joao@x.com"
    )
    masked = mask_pii(texto)
    for placeholder in ("[CPF]", "[DADO_CARTAO]", "[TELEFONE]", "[EMAIL]"):
        assert placeholder in masked
    for bruto in ("123.456.789-00", "4111 1111 1111 1111", "98765-4321", "joao@x.com"):
        assert bruto not in masked


def test_mask_pii_aceita_none():
    assert mask_pii(None) is None
