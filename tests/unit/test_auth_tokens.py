"""Tokens Ed25519: la central firma y cualquier servicio verifica con la clave pública."""

from datetime import datetime, timedelta, timezone

import jwt
import pytest
from platform_common import auth


@pytest.fixture
def signer():
    return auth.TokenSigner(auth.generate_private_key())


def test_un_token_firmado_por_la_central_se_verifica_con_su_clave_publica(signer):
    principal = auth.Principal("registrador.wh001", auth.REGISTRADOR, "Registrador", "WH-001")
    token = signer.issue(principal, timedelta(minutes=5))
    verifier = auth.TokenVerifier({signer.kid: signer.public_b64})
    assert verifier.verify(token) == principal


def test_token_vencido(signer):
    past = datetime.now(timezone.utc) - timedelta(hours=2)
    token = signer.issue(auth.Principal("lider", auth.LIDER), timedelta(minutes=5), now=past)
    with pytest.raises(auth.AuthError, match="venció"):
        auth.TokenVerifier({signer.kid: signer.public_b64}).verify(token)


def test_token_de_otra_clave_no_se_acepta(signer):
    other = auth.TokenSigner(auth.generate_private_key())
    token = other.issue(auth.Principal("intruso", auth.ADMIN_GLOBAL), timedelta(minutes=5))
    with pytest.raises(auth.AuthError, match="desconocida"):
        auth.TokenVerifier({signer.kid: signer.public_b64}).verify(token)


def test_token_alterado_no_se_acepta(signer):
    token = signer.issue(auth.Principal("registrador.wh001", auth.REGISTRADOR, warehouse_id="WH-001"), timedelta(minutes=5))
    header, payload, signature = token.split(".")
    forged = jwt.utils.base64url_encode(jwt.utils.base64url_decode(payload).replace(b"REGISTRADOR", b"ADMIN_GLOBAL")).decode()
    with pytest.raises(auth.AuthError):
        auth.TokenVerifier({signer.kid: signer.public_b64}).verify(f"{header}.{forged}.{signature}")


def test_roles_de_bodega_solo_actuan_en_su_bodega():
    registrador = auth.Principal("r", auth.REGISTRADOR, warehouse_id="WH-001")
    assert registrador.can_act_on("WH-001") and not registrador.can_act_on("WH-002")
    assert auth.Principal("g", auth.ADMIN_GLOBAL).can_act_on("WH-002")


def test_lista_de_claves_publicas(signer):
    assert auth.parse_public_keys(f" {signer.public_b64} ,") == {signer.kid: signer.public_b64}
