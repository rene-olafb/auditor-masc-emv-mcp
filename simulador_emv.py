from decimal import Decimal, ROUND_HALF_UP
from pydantic import ValidationError
from esquemas import ParametrosLitigioEMV

CENTAVO = Decimal("0.01")


def _fmt_mxn(valor: Decimal) -> str:
    """Formatea un Decimal a exactamente dos posiciones decimales con ROUND_HALF_UP."""
    return str(valor.quantize(CENTAVO, rounding=ROUND_HALF_UP))


def _factor_descuento(tasa_anual: Decimal, meses: int) -> Decimal:
    """Calcula (1 + r)^(meses / 12) usando tasa efectiva anual en Decimal."""
    if meses == 0 or tasa_anual == Decimal("0"):
        return Decimal("1")
    exponente = Decimal(meses) / Decimal("12")
    return (Decimal("1") + tasa_anual) ** exponente


def simular_escenario_batna_emv(parametros: dict) -> dict:
    """
    Modela determinísticamente en Decimal el Valor Monetario Esperado (EMV) y el
    Valor Presente Neto (VPN) de litigar frente a convenir en MASC, determinando
    el BATNA de actor y demandado y la Zona de Posible Acuerdo (ZOPA).
    """
    try:
        p = ParametrosLitigioEMV(**parametros)
    except ValidationError as exc:
        return {
            "estado": "ERROR_VALIDACION",
            "mensaje": "Los parámetros financieros no cumplen las restricciones de validación.",
            "errores": exc.errors(include_url=False),
        }

    prob_derrota_actor = Decimal("1") - p.probabilidad_exito_actor
    factor_juicio = _factor_descuento(p.tasa_descuento_anual, p.duracion_juicio_meses)
    factor_masc = _factor_descuento(p.tasa_descuento_anual, p.duracion_masc_meses)

    # 1. Métricas nominales sin descuento temporal (EMV bruto)
    emv_nominal_actor = (
        (p.probabilidad_exito_actor * p.monto_reclamado_mxn)
        - (prob_derrota_actor * p.riesgo_costas_perdedor_mxn)
        - p.costos_litigio_actor_mxn
    )
    emv_nominal_costo_demandado = (
        p.probabilidad_exito_actor * (p.monto_reclamado_mxn + p.riesgo_costas_perdedor_mxn)
        + p.costos_litigio_demandado_mxn
    )

    # 2. Métricas descontadas a Valor Presente Neto (BATNA real en VPN)
    vp_cobro_actor = (p.probabilidad_exito_actor * p.monto_reclamado_mxn) / factor_juicio
    vp_riesgo_costas_actor = (prob_derrota_actor * p.riesgo_costas_perdedor_mxn) / factor_juicio
    batna_vpn_actor = vp_cobro_actor - vp_riesgo_costas_actor - p.costos_litigio_actor_mxn

    vp_condena_demandado = (
        p.probabilidad_exito_actor * (p.monto_reclamado_mxn + p.riesgo_costas_perdedor_mxn)
    ) / factor_juicio
    batna_vpn_costo_demandado = vp_condena_demandado + p.costos_litigio_demandado_mxn

    # 3. Puntos de indiferencia para convenio en MASC (en el momento duracion_masc_meses)
    # Actor: S / factor_masc - costos_masc_actor >= batna_vpn_actor
    punto_indiferencia_actor = (batna_vpn_actor + p.costos_masc_actor_mxn) * factor_masc
    minimo_aceptable_actor = max(Decimal("0.00"), punto_indiferencia_actor)

    # Demandado: S / factor_masc + costos_masc_demandado <= batna_vpn_costo_demandado
    maximo_pagable_demandado = (batna_vpn_costo_demandado - p.costos_masc_demandado_mxn) * factor_masc

    # 4. Cálculo de ZOPA (Zona de Posible Acuerdo)
    existe_zopa = maximo_pagable_demandado >= minimo_aceptable_actor and maximo_pagable_demandado > Decimal("0")
    if existe_zopa:
        amplitud_zopa = maximo_pagable_demandado - minimo_aceptable_actor
        punto_medio_zopa = (minimo_aceptable_actor + maximo_pagable_demandado) / Decimal("2")
        estado_negociacion = "ZOPA_VIABLE"
    else:
        amplitud_zopa = Decimal("0.00")
        punto_medio_zopa = None
        estado_negociacion = "SIN_ZOPA_BRECHA_EXPECTATIVAS"

    return {
        "estado": estado_negociacion,
        "convencion_financiera": (
            "Aritmética Decimal (ROUND_HALF_UP). Cobros y condenas futuras se descuentan "
            "a VPN desde duracion_juicio_meses con tasa efectiva anual (1+r)^(t/12); "
            "costos propios de litigio y MASC se imputan a valor presente."
        ),
        "actor": {
            "emv_nominal_sin_descuento_mxn": _fmt_mxn(emv_nominal_actor),
            "batna_vpn_litigio_mxn": _fmt_mxn(batna_vpn_actor),
            "punto_indiferencia_matematico_mxn": _fmt_mxn(punto_indiferencia_actor),
            "minimo_racional_aceptable_masc_mxn": _fmt_mxn(minimo_aceptable_actor),
            "litigio_tiene_valor_esperado_negativo": batna_vpn_actor < Decimal("0"),
        },
        "demandado": {
            "emv_costo_nominal_sin_descuento_mxn": _fmt_mxn(emv_nominal_costo_demandado),
            "batna_vpn_costo_litigio_mxn": _fmt_mxn(batna_vpn_costo_demandado),
            "maximo_racional_ofertable_masc_mxn": _fmt_mxn(maximo_pagable_demandado),
        },
        "negociacion_masc": {
            "existe_zopa": existe_zopa,
            "rango_acuerdo_mxn": {
                "piso_actor": _fmt_mxn(minimo_aceptable_actor),
                "techo_demandado": _fmt_mxn(maximo_pagable_demandado),
            } if existe_zopa else None,
            "amplitud_zopa_mxn": _fmt_mxn(amplitud_zopa),
            "punto_medio_referencia_mxn": _fmt_mxn(punto_medio_zopa) if punto_medio_zopa is not None else None,
        },
        "advertencia_metodologica": (
            "Las probabilidades de éxito, duración y tasas de descuento son supuestos "
            "subjetivos aportados por el usuario; el motor garantiza consistencia aritmética, "
            "no predicción estadística del resultado judicial."
        ),
    }