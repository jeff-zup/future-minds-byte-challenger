from guardrails.pii import mask_pii
from guardrails.profanity import sanitize_profanity


def test_mask_cpf():
    assert "[CPF]" in mask_pii("Meu CPF é 123.456.789-00")


def test_profanity():
    assert "***" in sanitize_profanity("Esse atendimento é uma merda")
