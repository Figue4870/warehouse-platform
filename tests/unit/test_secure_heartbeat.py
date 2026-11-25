"""Heartbeat reto-respuesta cifrado (platform_common/secure_heartbeat.py).

Las pruebas de "man in the middle" simulan un ataque y esperan que el mensaje se rechace.
"""

import copy
import json
from datetime import datetime, timedelta, timezone

import pytest
from platform_common import secure_heartbeat as hb

SECRET_WH1 = "clave-de-prueba-wh001-0123456789"
SECRET_WH2 = "clave-de-prueba-wh002-9876543210"
KEY_WH1 = hb.derive_key(SECRET_WH1)
KEY_WH2 = hb.derive_key(SECRET_WH2)
STATUS = {"last_sequence": 41, "outbox_pending": 0, "instance": "WH-001-api-2"}


def central_asks(key=KEY_WH1, warehouse_id="WH-001", now=None):
    challenge = hb.new_challenge(warehouse_id, now)
    return challenge, hb.seal(key, hb.CHALLENGE, warehouse_id, challenge)


def warehouse_answers(envelope, key=KEY_WH1, warehouse_id="WH-001", guard=None, now=None):
    return hb.answer_challenge(key, warehouse_id, envelope, STATUS, guard or hb.ReplayGuard(), now)


def flip_one_character(text):
    return text[:10] + ("A" if text[10] != "A" else "B") + text[11:]


# el camino normal
def test_la_central_pregunta_la_bodega_responde_y_la_central_verifica():
    challenge, envelope = central_asks()
    response = warehouse_answers(envelope)
    assert hb.verify_response(KEY_WH1, "WH-001", challenge, response) == STATUS


def test_los_mensajes_viajan_cifrados():
    challenge, envelope = central_asks()
    response = warehouse_answers(envelope)
    on_the_wire = json.dumps(envelope) + json.dumps(response)
    assert challenge["challenge"] not in on_the_wire
    assert "last_sequence" not in on_the_wire and "WH-001-api-2" not in on_the_wire


def test_cada_mensaje_usa_un_nonce_distinto():
    first = hb.seal(KEY_WH1, hb.CHALLENGE, "WH-001", {"a": 1})
    second = hb.seal(KEY_WH1, hb.CHALLENGE, "WH-001", {"a": 1})
    assert first["nonce"] != second["nonce"] and first["ciphertext"] != second["ciphertext"]


# man in the middle
def test_una_central_falsa_sin_la_clave_no_logra_que_la_bodega_responda():
    _, envelope = central_asks(key=hb.derive_key("clave-equivocada-0000000000"))
    with pytest.raises(hb.AuthenticationError):
        warehouse_answers(envelope)


def test_una_bodega_falsa_sin_la_clave_no_engana_a_la_central():
    challenge, envelope = central_asks()
    impostor = hb.seal(hb.derive_key("clave-del-impostor-000000000"), hb.RESPONSE, "WH-001",
                       {"challenge": challenge["challenge"], "warehouse_id": "WH-001",
                        "responded_at": datetime.now(timezone.utc).isoformat(), "status": STATUS})
    with pytest.raises(hb.AuthenticationError):
        hb.verify_response(KEY_WH1, "WH-001", challenge, impostor)


def test_si_alguien_altera_la_respuesta_en_el_camino_se_detecta():
    challenge, envelope = central_asks()
    response = warehouse_answers(envelope)
    tampered = dict(response, ciphertext=flip_one_character(response["ciphertext"]))
    with pytest.raises(hb.AuthenticationError):
        hb.verify_response(KEY_WH1, "WH-001", challenge, tampered)


def test_si_alguien_altera_el_reto_en_el_camino_la_bodega_lo_rechaza():
    _, envelope = central_asks()
    tampered = dict(envelope, ciphertext=flip_one_character(envelope["ciphertext"]))
    with pytest.raises(hb.AuthenticationError):
        warehouse_answers(tampered)


def test_una_respuesta_vieja_grabada_no_sirve_para_un_reto_nuevo():
    old_challenge, old_envelope = central_asks()
    recorded = warehouse_answers(old_envelope)  # respuesta grabada por un tercero
    new_challenge, _ = central_asks()
    with pytest.raises(hb.ReplayError):
        hb.verify_response(KEY_WH1, "WH-001", new_challenge, recorded)


def test_la_bodega_no_responde_dos_veces_el_mismo_reto():
    _, envelope = central_asks()
    guard = hb.ReplayGuard()
    warehouse_answers(envelope, guard=guard)
    with pytest.raises(hb.ReplayError):
        warehouse_answers(copy.deepcopy(envelope), guard=guard)


def test_un_reto_no_se_puede_hacer_pasar_por_respuesta():
    challenge, envelope = central_asks()
    with pytest.raises(hb.AuthenticationError):
        hb.verify_response(KEY_WH1, "WH-001", challenge, dict(envelope, kind=hb.RESPONSE))


def test_un_mensaje_de_una_bodega_no_vale_para_otra():
    challenge, envelope = central_asks(key=KEY_WH2, warehouse_id="WH-002")
    response = warehouse_answers(envelope, key=KEY_WH2, warehouse_id="WH-002")
    relabeled = dict(response, warehouse_id="WH-001")
    with pytest.raises(hb.AuthenticationError):
        hb.verify_response(KEY_WH1, "WH-001", challenge, relabeled)
    with pytest.raises(hb.AuthenticationError):
        hb.verify_response(KEY_WH2, "WH-002", challenge, relabeled)


def test_un_reto_viejo_se_rechaza():
    two_minutes_ago = datetime.now(timezone.utc) - timedelta(minutes=2)
    _, envelope = central_asks(now=two_minutes_ago)
    with pytest.raises(hb.StaleMessageError):
        warehouse_answers(envelope)


def test_mensajes_sin_formato_se_rechazan():
    challenge, _ = central_asks()
    for garbage in (None, "hola", {}, {"v": 1, "kind": "response", "warehouse_id": "WH-001", "nonce": "x", "ciphertext": "y"}):
        with pytest.raises(hb.AuthenticationError):
            hb.verify_response(KEY_WH1, "WH-001", challenge, garbage)


# configuración
def test_la_clave_debe_ser_larga():
    with pytest.raises(ValueError, match="al menos"):
        hb.derive_key("corta")


def test_cada_bodega_tiene_su_propia_llave():
    assert hb.derive_key(SECRET_WH1) == KEY_WH1 and KEY_WH1 != KEY_WH2 and len(KEY_WH1) == 32


def test_lista_de_claves_de_la_central():
    assert hb.parse_secrets(" WH-001=abc==,WH-002=def ") == {"WH-001": "abc==", "WH-002": "def"}
    assert hb.parse_secrets("") == {}
    with pytest.raises(ValueError):
        hb.parse_secrets("WH-001")
