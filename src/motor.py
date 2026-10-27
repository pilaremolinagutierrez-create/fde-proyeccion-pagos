"""
Motor LLM base - Proyección de pagos entre áreas.

Recibe una cotización (Entrada), se la pasa a Claude, valida la respuesta contra Salida
y devuelve el resultado junto con tokens, costo en USD y latencia en ms.

Uso:
    python src/motor.py --texto "Taller X: 1000 remeras ... 50% adelanto ..."
    python src/motor.py --archivo datos/consultas.json --id COT-2026-001
    python src/motor.py --archivo datos/consultas.json --id COT-2026-001 --forzar-error
"""
import argparse
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import date
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from pydantic import ValidationError

from contrato import Entrada, Salida

RAIZ = Path(__file__).resolve().parent.parent
load_dotenv(RAIZ / ".env")

MODELO = os.getenv("MODELO", "claude-haiku-5-5")
PRECIO_ENTRADA = float(os.getenv("PRECIO_ENTRADA_USD_MTOK", "0.10"))   # USD por millón de tokens
PRECIO_SALIDA = float(os.getenv("PRECIO_SALIDA_USD_MTOK", "0.50"))
MODO_SIMULADO = os.getenv("MODO_SIMULADO", "0") == "1"
MAX_TOKENS = 2000
MAX_TOKENS_FORZAR_ERROR = 40       # respuesta cortada a propósito para mostrar el manejo de errores

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("motor")


# ---------- Plantillas de prompt (archivos separados, no dentro de la función) ----------

def cargar_prompts(entrada: Entrada) -> tuple[str, str]:
    carpeta = Path(__file__).parent / "prompts"
    esquema = json.dumps(Salida.model_json_schema(), ensure_ascii=False, indent=1)
    sistema = (carpeta / "sistema.txt").read_text(encoding="utf-8").format(esquema=esquema)
    usuario = (carpeta / "usuario.txt").read_text(encoding="utf-8").format(
        fecha_carga=entrada.fecha_carga.isoformat(), area_origen=entrada.area_origen.value,
        id_carga=entrada.id_carga, texto=entrada.texto)
    return sistema, usuario


# ---------- Resultado ----------

@dataclass
class Resultado:
    ok: bool
    id_carga: str
    modelo: str
    simulado: bool
    intentos: int = 0
    valido_primer_intento: bool = False
    tokens_entrada: int = 0
    tokens_salida: int = 0
    costo_usd: float = 0.0
    latencia_ms: float = 0.0
    salida: Optional[dict] = None
    error: Optional[dict] = None        # error estructurado: tipo, campos_fallidos, mensaje

    def a_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2, default=str)


# ---------- Cliente del modelo ----------

class ErrorAPI(Exception):
    def __init__(self, tipo: str, mensaje: str):
        super().__init__(mensaje)
        self.tipo, self.mensaje = tipo, mensaje


def llamar_modelo(sistema: str, mensajes: list[dict], max_tokens: int) -> tuple[str, int, int]:
    """Devuelve (texto, tokens_entrada, tokens_salida). Traduce los errores de la API a ErrorAPI."""
    if MODO_SIMULADO:
        return _respuesta_simulada(mensajes, max_tokens)

    import anthropic
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise ErrorAPI("api_autenticacion", "Falta ANTHROPIC_API_KEY en el archivo .env")
    cliente = anthropic.Anthropic()
    try:
        resp = cliente.messages.create(model=MODELO, max_tokens=max_tokens, system=sistema, messages=mensajes)
    except anthropic.RateLimitError as e:
        raise ErrorAPI("api_limite_de_uso", f"Límite de uso alcanzado: {e}")
    except anthropic.APIConnectionError as e:
        raise ErrorAPI("api_sin_conexion", f"No se pudo conectar con la API: {e}")
    except anthropic.AuthenticationError as e:
        raise ErrorAPI("api_autenticacion", f"Clave inválida: {e}")
    except anthropic.APIStatusError as e:
        raise ErrorAPI("api_error", f"Error {e.status_code} de la API: {e.message}")

    texto = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    return texto, resp.usage.input_tokens, resp.usage.output_tokens


def _respuesta_simulada(mensajes, max_tokens):
    """Solo para probar sin clave (MODO_SIMULADO=1). No sirve como evidencia de la entrega."""
    pedido = mensajes[0]["content"]
    m = re.search(r"Id de carga: (\S+)", pedido)
    simuladas = json.loads((RAIZ / "datos" / "respuestas_simuladas.json").read_text(encoding="utf-8"))
    texto = json.dumps(simuladas.get(m.group(1) if m else "", simuladas["_fuera_de_tema"]), ensure_ascii=False)
    if max_tokens < 100:
        texto = texto[:120]
    time.sleep(0.05)
    return texto, len(pedido) // 3 + 900, len(texto) // 3


# ---------- Validación ----------

def extraer_json(texto: str) -> dict:
    limpio = re.sub(r"^```(?:json)?|```$", "", texto.strip(), flags=re.M).strip()
    return json.loads(limpio)


def describir_error(e: Exception) -> tuple[str, list[str], str]:
    """Devuelve (tipo, campos que fallaron, mensaje legible)."""
    if isinstance(e, ValidationError):
        campos, detalles = [], []
        for err in e.errors():
            msg = err["msg"].replace("Value error, ", "")
            campo = ".".join(str(x) for x in err["loc"]) or msg.split(":")[0]
            campos.append(campo)
            detalles.append(msg if msg.startswith(campo + ":") else f"{campo}: {msg}")
        return "validacion", campos, " | ".join(detalles)
    return "json_invalido", ["(respuesta completa)"], f"La respuesta no es JSON válido: {e}"


# ---------- Motor ----------

def procesar(entrada: Entrada, forzar_error: bool = False) -> Resultado:
    res = Resultado(ok=False, id_carga=entrada.id_carga, modelo=MODELO, simulado=MODO_SIMULADO)
    sistema, usuario = cargar_prompts(entrada)
    mensajes = [{"role": "user", "content": usuario}]
    max_tokens = MAX_TOKENS_FORZAR_ERROR if forzar_error else MAX_TOKENS
    inicio = time.perf_counter()

    for intento in (1, 2):                     # un intento + un reintento
        res.intentos = intento
        try:
            texto, t_in, t_out = llamar_modelo(sistema, mensajes, max_tokens)
        except ErrorAPI as e:
            log.error("[%s] Error de API (%s): %s", entrada.id_carga, e.tipo, e.mensaje)
            res.error = {"tipo": e.tipo, "campos_fallidos": [], "mensaje": e.mensaje}
            break
        res.tokens_entrada += t_in
        res.tokens_salida += t_out

        try:
            salida = Salida.model_validate(extraer_json(texto))
            res.ok, res.salida, res.error = True, salida.model_dump(mode="json"), None
            res.valido_primer_intento = intento == 1
            log.info("[%s] Validó en el intento %d", entrada.id_carga, intento)
            break
        except (ValidationError, json.JSONDecodeError) as e:
            tipo, campos, detalle = describir_error(e)
            log.warning("[%s] Intento %d inválido (%s). Campos que fallaron: %s",
                        entrada.id_carga, intento, tipo, ", ".join(campos))
            res.error = {"tipo": tipo, "campos_fallidos": campos, "mensaje": detalle}
            # Reintento: le devolvemos al modelo su respuesta y el error concreto
            mensajes += [{"role": "assistant", "content": texto or "(vacío)"},
                         {"role": "user", "content": f"Tu respuesta no pasó la validación: {detalle}. "
                                                     "Devolvé solo el JSON corregido."}]

    res.latencia_ms = round((time.perf_counter() - inicio) * 1000, 1)
    res.costo_usd = round(res.tokens_entrada * PRECIO_ENTRADA / 1e6 + res.tokens_salida * PRECIO_SALIDA / 1e6, 6)
    if not res.ok:
        log.error("[%s] No se pudo validar después de %d intento(s). Se devuelve error estructurado.",
                  entrada.id_carga, res.intentos)
    return res


def main():
    p = argparse.ArgumentParser(description="Motor LLM - extracción de cotizaciones")
    p.add_argument("--texto", help="Texto de la cotización")
    p.add_argument("--archivo", help="JSON con consultas (datos/consultas.json)")
    p.add_argument("--id", help="id_carga a ejecutar del archivo")
    p.add_argument("--area", default="produccion")
    p.add_argument("--forzar-error", action="store_true", help="Corta la respuesta para mostrar el manejo de errores")
    a = p.parse_args()

    try:
        if a.archivo:
            datos = json.loads(Path(a.archivo).read_text(encoding="utf-8"))
            crudo = next(c for c in datos if c["id_carga"] == a.id)
        else:
            crudo = {"id_carga": "COT-2026-999", "area_origen": a.area, "texto": a.texto or ""}
        entrada = Entrada.model_validate(crudo)
    except ValidationError as e:
        # La Entrada también se valida: no se llama al modelo con datos inválidos
        _, campos, detalle = describir_error(e)
        print(json.dumps({"ok": False, "error": {"tipo": "entrada_invalida", "campos_fallidos": campos,
                                                 "mensaje": detalle}}, ensure_ascii=False, indent=2))
        sys.exit(1)
    except StopIteration:
        sys.exit(f"No existe la consulta {a.id} en {a.archivo}")

    if MODO_SIMULADO:
        log.warning("MODO_SIMULADO=1: respuesta de prueba, NO usar como evidencia")
    res = procesar(entrada, forzar_error=a.forzar_error)
    print(res.a_json())
    sys.exit(0 if res.ok else 2)


if __name__ == "__main__":
    main()
