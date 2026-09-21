import re


def limpar_cpf(cpf: str) -> str:
    """Remove formatacao e preenche com zero a esquerda ate 11 digitos.

    Necessario porque planilhas/APIs frequentemente entregam CPF como numero,
    descartando o zero a esquerda (ex.: '02654235114' vira '2654235114'), o
    que quebraria a validacao do digito verificador.
    """
    digitos = re.sub(r"\D", "", str(cpf or ""))
    if digitos and len(digitos) <= 11:
        digitos = digitos.zfill(11)
    return digitos


def cpf_valido(cpf: str) -> bool:
    """Valida CPF pelo digito verificador (modulo 11)."""
    cpf = limpar_cpf(cpf)
    if len(cpf) != 11 or cpf == cpf[0] * 11:
        return False

    for pos in (9, 10):
        soma = sum(int(cpf[i]) * (pos + 1 - i) for i in range(pos))
        digito = (soma * 10 % 11) % 10
        if digito != int(cpf[pos]):
            return False
    return True
