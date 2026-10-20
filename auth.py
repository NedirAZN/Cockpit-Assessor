"""Login por cookie assinado.

Uma senha só, em variável de ambiente. É proporcional ao caso: um usuário, dados
de clientes, nada de cadastro. O cookie é assinado com HMAC, então ninguém forja
sessão sem a SECRET_KEY.
"""
import hmac
import os
import time

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

SECRET_KEY = os.environ.get("SECRET_KEY", "")
USUARIO = os.environ.get("COCKPIT_USUARIO", "")
SENHA = os.environ.get("COCKPIT_SENHA", "")
DIAS_SESSAO = int(os.environ.get("DIAS_SESSAO", "30"))
COOKIE = "bfr_sessao"

if not SECRET_KEY:
    raise RuntimeError("SECRET_KEY não definida. Gere uma com: openssl rand -hex 32")
if not USUARIO or not SENHA:
    raise RuntimeError("COCKPIT_USUARIO e COCKPIT_SENHA precisam estar definidas.")

_serializer = URLSafeTimedSerializer(SECRET_KEY, salt="bfr-cockpit")


def conferir_credenciais(usuario: str, senha: str) -> bool:
    """Compara em tempo constante, para não vazar a senha pelo tempo de resposta."""
    ok_usuario = hmac.compare_digest(usuario.strip(), USUARIO)
    ok_senha = hmac.compare_digest(senha, SENHA)
    time.sleep(0.3)  # freia tentativa em força bruta
    return ok_usuario and ok_senha


def criar_token() -> str:
    return _serializer.dumps({"u": USUARIO})


def sessao_valida(token: str | None) -> bool:
    if not token:
        return False
    try:
        _serializer.loads(token, max_age=DIAS_SESSAO * 86400)
        return True
    except (BadSignature, SignatureExpired):
        return False
