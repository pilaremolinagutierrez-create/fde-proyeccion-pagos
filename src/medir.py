"""
Corre las 5 consultas de datos/consultas.json, arma la tabla de medición y la compara
con la meta del KPI técnico de la Pre-Entrega 1.

Uso:  python src/medir.py
Guarda: resultados/mediciones.md  y  resultados/detalle.json
"""
import json
from datetime import datetime
from pathlib import Path

from contrato import Entrada
from motor import procesar, MODELO, MODO_SIMULADO, PRECIO_ENTRADA, PRECIO_SALIDA, RAIZ

META_KPI_TECNICO = 90.0   # PE1: cotizaciones procesadas completas y correctas al primer intento >= 90%


def main():
    consultas = json.loads((RAIZ / "datos" / "consultas.json").read_text(encoding="utf-8"))
    filas, detalle = [], []
    for i, c in enumerate(consultas, 1):
        r = procesar(Entrada.model_validate(c))
        detalle.append({"descripcion": c["descripcion"], **json.loads(r.a_json())})
        filas.append((i, c["descripcion"], r))

    n = len(filas)
    prom = lambda attr: sum(getattr(r, attr) for _, _, r in filas) / n
    validas = sum(r.ok for _, _, r in filas)
    primer = sum(r.valido_primer_intento for _, _, r in filas)
    pct_primer = primer / n * 100

    lineas = [
        f"# Medición del motor - {datetime.now():%d/%m/%Y %H:%M}",
        f"Modelo: `{MODELO}` · Precios: USD {PRECIO_ENTRADA} / MTok entrada, USD {PRECIO_SALIDA} / MTok salida"
        + (" · **MODO SIMULADO: NO ES EVIDENCIA**" if MODO_SIMULADO else ""),
        "",
        "| # | Consulta | ¿Validó? | ¿Al 1er intento? | Tokens (entrada / salida) | Costo USD | Latencia ms |",
        "|---|---|---|---|---|---|---|",
    ]
    for i, desc, r in filas:
        lineas.append(f"| {i} | {desc} | {'Sí' if r.ok else 'No'} | {'Sí' if r.valido_primer_intento else 'No'} "
                      f"| {r.tokens_entrada} / {r.tokens_salida} | {r.costo_usd:.6f} | {r.latencia_ms:,.0f} |")
    lineas += [
        f"| | **Promedio** | {validas}/{n} | {primer}/{n} | {prom('tokens_entrada'):.0f} / {prom('tokens_salida'):.0f} "
        f"| {prom('costo_usd'):.6f} | {prom('latencia_ms'):,.0f} |",
        "",
        f"**KPI técnico (PE1):** cotizaciones procesadas completas y correctas al primer intento = "
        f"**{pct_primer:.0f}%** · Meta: {META_KPI_TECNICO:.0f}% · "
        + ("**Cumple**" if pct_primer >= META_KPI_TECNICO else "**No cumple todavía**"),
        f"Costo total de las {n} consultas: USD {sum(r.costo_usd for _, _, r in filas):.6f}",
    ]
    texto = "\n".join(lineas)
    print("\n" + texto + "\n")

    (RAIZ / "resultados").mkdir(exist_ok=True)
    (RAIZ / "resultados" / "mediciones.md").write_text(texto + "\n", encoding="utf-8")
    (RAIZ / "resultados" / "detalle.json").write_text(json.dumps(detalle, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Guardado en resultados/mediciones.md y resultados/detalle.json")


if __name__ == "__main__":
    main()
