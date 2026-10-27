"""
Contrato de datos del motor (Pydantic).

Sale de la HU2 de la Pre-Entrega 1:
  "Como responsable de Producción, quiero subir cada cotización en el formato que ya tengo,
   para informar los pagos sin cargar los datos dos veces."
  Criterios: el asistente extrae proveedor, monto, moneda, condiciones y fechas de pago;
             si falta la fecha de pago, el sistema la pide antes de permitir el envío.

Entrada = lo que carga Producción.   Salida = el compromiso de pago que ve Finanzas.
"""
from datetime import date
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, model_validator


# ---------- Valores permitidos (Enum) ----------

class AreaOrigen(str, Enum):
    produccion = "produccion"
    producto = "producto"
    comercio_exterior = "comercio_exterior"


class Moneda(str, Enum):
    ARS = "ARS"
    USD = "USD"
    EUR = "EUR"


class TipoGasto(str, Enum):
    taller = "taller"                  # confección
    telas = "telas"
    avios = "avios"                    # botones, etiquetas, cierres
    importacion = "importacion"
    otro = "otro"


class ConceptoPago(str, Enum):
    adelanto = "adelanto"
    saldo = "saldo"
    cuota = "cuota"
    pago_unico = "pago_unico"
    nacionalizacion = "nacionalizacion"


# ---------- ENTRADA ----------

class Entrada(BaseModel):
    # Regla 1 - formato con patrón: COT-AAAA-NNN
    id_carga: str = Field(pattern=r"^COT-\d{4}-\d{3}$",
                          description="Identificador de la carga, ej. COT-2026-001")
    # Regla 2 - valor permitido (Enum)
    area_origen: AreaOrigen
    # Regla 3 - largo mínimo y máximo: una cotización real no entra en 20 caracteres
    texto: str = Field(min_length=20, max_length=8000,
                       description="Cotización tal como llegó: mail, Excel pegado, WhatsApp, proforma")
    fecha_carga: date = Field(default_factory=date.today,
                              description="Sirve para convertir fechas relativas ('a 30 días')")


# ---------- SALIDA ----------

class Pago(BaseModel):
    concepto: ConceptoPago
    porcentaje: Optional[float] = Field(default=None, ge=0, le=100)      # rango 0-100
    monto: float = Field(gt=0)                                            # rango > 0
    fecha_pago: Optional[date] = None                                     # None = no se sabe: NO se inventa
    fecha_es_estimada: bool = Field(description="True si la fecha se dedujo o es aproximada")


class Salida(BaseModel):
    es_cotizacion: bool
    motivo_rechazo: Optional[str] = Field(default=None, min_length=10)
    proveedor: Optional[str] = Field(default=None, min_length=3)
    tipo_gasto: Optional[TipoGasto] = None
    moneda: Optional[Moneda] = None
    monto_total: Optional[float] = Field(default=None, gt=0)
    pagos: list[Pago] = Field(default_factory=list)
    datos_faltantes: list[str] = Field(default_factory=list,
                                       description="Lo que Producción tiene que completar antes de enviar")
    confianza: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def reglas_de_negocio(self):
        if not self.es_cotizacion:
            # Fuera de tema: tiene que explicar por qué y no traer montos
            if not self.motivo_rechazo:
                raise ValueError("motivo_rechazo: obligatorio cuando es_cotizacion es false")
            if self.pagos or self.monto_total:
                raise ValueError("pagos: no puede haber pagos si no es una cotización")
            return self

        # Es cotización: campos obligatorios para Finanzas
        for campo in ("tipo_gasto", "moneda", "monto_total"):
            if getattr(self, campo) is None:
                raise ValueError(f"{campo}: obligatorio en una cotización")
        if not self.pagos:
            raise ValueError("pagos: tiene que haber al menos un pago")

        # La suma de los pagos tiene que dar el total (tolerancia 1%)
        suma = sum(p.monto for p in self.pagos)
        if abs(suma - self.monto_total) > self.monto_total * 0.01:
            raise ValueError(f"pagos: la suma ({suma:,.2f}) no coincide con monto_total ({self.monto_total:,.2f})")

        # No inventar: si falta un dato, tiene que figurar en datos_faltantes (criterio HU2)
        faltantes = " ".join(self.datos_faltantes).lower()
        if any(p.fecha_pago is None for p in self.pagos) and "fecha" not in faltantes:
            raise ValueError("datos_faltantes: hay un pago sin fecha y no se pide la fecha")
        if self.proveedor is None and "proveedor" not in faltantes:
            raise ValueError("datos_faltantes: falta el proveedor y no se pide")
        return self
