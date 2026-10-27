# fde-proyeccion-pagos

Motor LLM base del caso **"Proyección de pagos entre áreas asistida por IA"** (Forward Deployed Engineer · Pre-Entrega 2).

Producción pega una cotización tal como le llegó (mail, Excel, WhatsApp, proforma en inglés). El motor se la pasa a
Claude, valida la respuesta con Pydantic y devuelve el **compromiso de pago estandarizado** que Finanzas necesita para
proyectar la caja: proveedor, tipo de gasto, moneda, monto total, pagos con fecha, y los **datos faltantes** que
Producción tiene que completar. También informa **tokens, costo en USD y latencia en ms**.

> Empresa, marcas y proveedores anonimizados. Las cotizaciones de `datos/consultas.json` son ficticias.

## Estructura

```
fde-proyeccion-pagos/
├── README.md
├── requirements.txt
├── .env.example          # nombres de variables, sin valores
├── .gitignore            # incluye .env
├── datos/
│   ├── consultas.json            # las 5 consultas de prueba
│   └── respuestas_simuladas.json # solo para MODO_SIMULADO
├── resultados/                   # acá se guardan las mediciones
└── src/
    ├── motor.py          # motor: llamada al modelo, validación, reintento, medición
    ├── contrato.py       # contrato de datos: clases Entrada y Salida (Pydantic)
    ├── medir.py          # corre las 5 consultas y arma la tabla
    └── prompts/
        ├── sistema.txt   # plantilla del prompt de sistema
        └── usuario.txt   # plantilla del mensaje de usuario
```

## Cómo correrlo desde un clon limpio

Requisitos: Python 3.10 o superior y una clave de API de Anthropic.

```bash
git clone <url-del-repo>
cd fde-proyeccion-pagos
python3 -m venv .venv
source .venv/bin/activate          # en Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # y completar ANTHROPIC_API_KEY dentro de .env
```

Probar sin clave (respuestas de prueba, no sirven como evidencia):

```bash
MODO_SIMULADO=1 python src/medir.py
```

Ejecuciones reales:

```bash
# Las 5 consultas + tabla de medición (se guarda en resultados/mediciones.md)
python src/medir.py

# Una consulta puntual
python src/motor.py --archivo datos/consultas.json --id COT-2026-001

# Ejecución con error (respuesta cortada a propósito) para ver el manejo de errores
python src/motor.py --archivo datos/consultas.json --id COT-2026-004 --forzar-error

# Texto libre
python src/motor.py --texto "Taller Sur: 500 buzos a $ 9.000 c/u, 50% adelanto el 15/11/2026 y 50% contra entrega"
```

## Contrato de datos

**Entrada** (lo que carga Producción)

| Campo | Regla |
|---|---|
| `id_carga` | Patrón `COT-AAAA-NNN` |
| `area_origen` | Enum: `produccion`, `producto`, `comercio_exterior` |
| `texto` | Largo mínimo 20, máximo 8000 caracteres |
| `fecha_carga` | Fecha (por defecto, hoy) |

**Salida** (lo que recibe Finanzas)

| Campo | Regla |
|---|---|
| `tipo_gasto` | Enum: `taller`, `telas`, `avios`, `importacion`, `otro` |
| `moneda` | Enum: `ARS`, `USD`, `EUR` |
| `monto_total`, `pagos[].monto` | Mayor que 0 |
| `pagos[].porcentaje` | Rango 0 a 100 |
| `proveedor` | Largo mínimo 3 o null |
| `confianza` | Rango 0 a 1 |
| Regla de negocio | La suma de los pagos tiene que coincidir con `monto_total` (±1%) |
| Regla de negocio | Si un pago no tiene fecha o falta el proveedor, tiene que figurar en `datos_faltantes` (el modelo no puede inventarlo) |
| Regla de negocio | Si `es_cotizacion` es false: `motivo_rechazo` obligatorio y sin pagos |

## Qué pasa cuando falla

1. **Respuesta inválida** (no es JSON o no cumple el contrato): se registra en el log **qué campo falló**, se le
   devuelve al modelo el error concreto y se **reintenta una vez**. Si vuelve a fallar, se devuelve un **error
   estructurado** (`tipo`, `campos_fallidos`, `mensaje`) y no se registra ningún compromiso de pago.
2. **Errores de la API**: límite de uso, sin conexión, clave inválida u otro error se devuelven como error
   estructurado, sin cortar el programa.
3. **Entrada inválida**: si la carga no cumple el contrato de `Entrada`, no se llama al modelo.

## Modelo y costos

`claude-haiku-5-5` (Anthropic): USD 0,10 por millón de tokens de entrada y USD 0,50 por millón de tokens de salida
(precios publicados para prompts de hasta 100.000 tokens). El costo se calcula con los tokens del campo `usage`
de cada respuesta, sumando el reintento si lo hubo.
