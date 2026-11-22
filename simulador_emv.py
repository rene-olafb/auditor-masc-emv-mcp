from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP
from pydantic import ValidationError
from esquemas import ParametrosLitigioEMV, ParametrosSensibilidadEMV

CENTAVO = Decimal("0.01")


def _fmt_mxn(valor: Decimal, modo: str = ROUND_HALF_UP) -> str:
    """Formatea un Decimal a dos posiciones decimales con el modo de redondeo especificado."""
    return str(valor.quantize(CENTAVO, rounding=modo))


def _factor_descuento(tasa_anual: Decimal, meses: int) -> Decimal:
    """Calcula (1 + r)^(meses / 12) usando tasa efectiva anual en Decimal de precisión finita."""
    if meses == 0 or tasa_anual == Decimal("0"):
        return Decimal("1")
    exponente = Decimal(meses) / Decimal("12")
    return (Decimal("1") + tasa_anual) ** exponente


def simular_escenario_batna_emv(parametros: dict) -> dict:
    """
    Modela determinísticamente en Decimal el Valor Presente Neto (VPN) de litigar
    frente a convenir en MASC, contabilizando transferencias de costas entre partes,
    redondeo conservador al centavo y causas explícitas de ausencia de ZOPA.
    """
    try:
        p = ParametrosLitigioEMV(**parametros)
    except ValidationError as exc:
        return {
            "estado": "ERROR_VALIDACION",
            "mensaje": "Los parámetros financieros no cumplen las restricciones de validación.",
            "errores": exc.errors(include_url=False),
        }

    try:
        prob_exito = p.probabilidad_exito_actor
        prob_derrota = Decimal("1") - prob_exito
        factor_juicio = _factor_descuento(p.tasa_descuento_anual, p.duracion_juicio_meses)
        factor_masc = _factor_descuento(p.tasa_descuento_anual, p.duracion_masc_meses)

        k_reemb = p.costas_reembolsables_entre_partes_mxn
        k_ext = p.costos_externos_perdedor_mxn

        # 1. Flujos esperados en t = duracion_juicio_meses
        # Transferencia de costas reembolsables: quien pierde paga k_reemb y quien gana lo recibe.
        flujo_esperado_juicio_actor = (
            prob_exito * (p.monto_reclamado_mxn + k_reemb)
            - prob_derrota * (k_reemb + k_ext)
        )
        costo_esperado_juicio_demandado = (
            prob_exito * (p.monto_reclamado_mxn + k_reemb + k_ext)
            - prob_derrota * k_reemb
        )

        # Métricas brutas (flujo del fallo sin descontar, menos/más costos propios ya en t=0)
        emv_bruto_actor = flujo_esperado_juicio_actor - p.costos_litigio_actor_mxn
        emv_bruto_costo_demandado = costo_esperado_juicio_demandado + p.costos_litigio_demandado_mxn

        # 2. Valor Presente Neto (VPN) del litigio
        vpn_litigio_actor = (flujo_esperado_juicio_actor / factor_juicio) - p.costos_litigio_actor_mxn
        vpn_costo_litigio_demandado = (
            costo_esperado_juicio_demandado / factor_juicio
        ) + p.costos_litigio_demandado_mxn

        # 3. Alternativa de referencia (BATNA) del actor
        if p.actor_puede_desistir_sin_costo and vpn_litigio_actor < Decimal("0"):
            batna_efectivo_actor = Decimal("0.00")
            alternativa_actor = "desistir_o_no_demandar"
        else:
            batna_efectivo_actor = vpn_litigio_actor
            alternativa_actor = "litigar"

        # 4. Límites matemáticos de indiferencia en t = duracion_masc_meses
        piso_matematico_actor = (batna_efectivo_actor + p.costos_masc_actor_mxn) * factor_masc
        techo_matematico_demandado = (
            vpn_costo_litigio_demandado - p.costos_masc_demandado_mxn
        ) * factor_masc

        # 5. Redondeo conservador a centavos efectivamente pagables:
        # El piso redondea hacia arriba (CEILING) y el techo hacia abajo (FLOOR).
        piso_no_negativo_matematico = max(Decimal("0.00"), piso_matematico_actor)
        piso_pagable_centavos = piso_no_negativo_matematico.quantize(CENTAVO, rounding=ROUND_CEILING)
        techo_pagable_centavos = techo_matematico_demandado.quantize(CENTAVO, rounding=ROUND_FLOOR)

        # 6. Determinación de ZOPA sobre importes realmente pagables en centavos
        ningun_pago_no_negativo_demandado = techo_pagable_centavos < Decimal("0.00")
        causa_sin_zopa = None

        if ningun_pago_no_negativo_demandado:
            existe_zopa = False
            estado_negociacion = "SIN_ZOPA"
            causa_sin_zopa = "ningun_pago_no_negativo_satisface_al_demandado"
        elif techo_pagable_centavos < piso_pagable_centavos:
            existe_zopa = False
            estado_negociacion = "SIN_ZOPA"
            if techo_matematico_demandado >= piso_no_negativo_matematico:
                causa_sin_zopa = "sin_monto_pagable_en_centavos"
            else:
                causa_sin_zopa = "costos_masc_superan_ahorro_litigio"
        elif piso_pagable_centavos == Decimal("0.00") and techo_pagable_centavos == Decimal("0.00"):
            if p.permitir_convenio_cero:
                existe_zopa = True
                estado_negociacion = "ZOPA_VIABLE"
            else:
                existe_zopa = False
                estado_negociacion = "SIN_ZOPA"
                causa_sin_zopa = "acuerdo_solo_en_cero_sin_pago"
        else:
            existe_zopa = True
            estado_negociacion = "ZOPA_VIABLE"

        if existe_zopa:
            amplitud_zopa = techo_pagable_centavos - piso_pagable_centavos
            punto_medio_zopa = (
                (piso_pagable_centavos + techo_pagable_centavos) / Decimal("2")
            ).quantize(CENTAVO, rounding=ROUND_HALF_UP)
        else:
            amplitud_zopa = Decimal("0.00")
            punto_medio_zopa = None

        maximo_ofertable_str = (
            None if ningun_pago_no_negativo_demandado else _fmt_mxn(techo_pagable_centavos, ROUND_FLOOR)
        )
    except (InvalidOperation, ArithmeticError) as exc:
        return {
            "estado": "ERROR_ARITMETICO",
            "mensaje": f"No fue posible completar el cálculo financiero dentro de la precisión permitida: {exc}",
        }

    return {
        "estado": estado_negociacion,
        "causa_sin_zopa": causa_sin_zopa,
        "convencion_financiera": (
            "Aritmética Decimal con precisión finita para (1+r)^(t/12). Flujos del fallo y "
            "costas se descuentan desde duracion_juicio_meses; costos propios de litigio y "
            "MASC se imputan a valor presente (t=0). Las costas reembolsables se transfieren "
            "entre partes (se cargan al perdedor y se acreditan al vencedor). La viabilidad "
            "se decide tras redondeo conservador al centavo (piso CEILING, techo FLOOR)."
        ),
        "supuestos_activos": {
            "probabilidad_compartida_actor_demandado": str(prob_exito),
            "cobro_integro_al_ganar": True,
            "neutralidad_al_riesgo": True,
            "alternativa_actor_modelada": alternativa_actor,
            "permitir_convenio_cero": p.permitir_convenio_cero,
        },
        "actor": {
            "emv_flujo_bruto_sin_descuento_mxn": _fmt_mxn(emv_bruto_actor),
            "vpn_litigio_mxn": _fmt_mxn(vpn_litigio_actor),
            "batna_efectivo_vpn_mxn": _fmt_mxn(batna_efectivo_actor),
            "punto_indiferencia_matematico_mxn": _fmt_mxn(piso_matematico_actor),
            "minimo_pagable_aceptable_masc_mxn": _fmt_mxn(piso_pagable_centavos, ROUND_CEILING),
            "litigio_tiene_valor_esperado_negativo": vpn_litigio_actor < Decimal("0"),
        },
        "demandado": {
            "emv_costo_bruto_sin_descuento_mxn": _fmt_mxn(emv_bruto_costo_demandado),
            "batna_vpn_costo_litigio_mxn": _fmt_mxn(vpn_costo_litigio_demandado),
            "limite_matematico_pago_masc_mxn": _fmt_mxn(techo_matematico_demandado),
            "maximo_pagable_ofertable_masc_mxn": maximo_ofertable_str,
            "ningun_pago_no_negativo_satisface": ningun_pago_no_negativo_demandado,
        },
        "negociacion_masc": {
            "existe_zopa": existe_zopa,
            "rango_acuerdo_pagable_mxn": {
                "piso_actor": _fmt_mxn(piso_pagable_centavos, ROUND_CEILING),
                "techo_demandado": _fmt_mxn(techo_pagable_centavos, ROUND_FLOOR),
            } if existe_zopa else None,
            "amplitud_zopa_mxn": _fmt_mxn(amplitud_zopa),
            "punto_medio_referencia_mxn": _fmt_mxn(punto_medio_zopa) if punto_medio_zopa is not None else None,
        },
        "advertencia_metodologica": (
            "Comparación condicionada a supuestos de probabilidad compartida, cobro íntegro, "
            "costos propios en valor presente y neutralidad al riesgo. El motor garantiza "
            "consistencia aritmética, no predicción estadística del fallo judicial."
        ),
    }


def simular_matriz_sensibilidad_emv(parametros: dict) -> dict:
    """
    Ejecuta el motor determinista BATNA/EMV sobre una lista de probabilidades de
    éxito (por defecto 0.35, 0.50, 0.65 y 0.80) para observar el desplazamiento de la ZOPA.
    """
    try:
        p = ParametrosSensibilidadEMV(**parametros)
    except ValidationError as exc:
        return {
            "estado": "ERROR_VALIDACION",
            "mensaje": "Los parámetros de la matriz de sensibilidad no cumplen la validación.",
            "errores": exc.errors(include_url=False),
        }

    base = p.model_dump(exclude={"probabilidades_exito_actor"})
    escenarios = []
    con_zopa = 0

    for prob in p.probabilidades_exito_actor:
        res = simular_escenario_batna_emv({**base, "probabilidad_exito_actor": prob})
        if res["estado"] == "ZOPA_VIABLE":
            con_zopa += 1
        escenarios.append({
            "probabilidad_exito_actor": str(prob),
            "estado": res["estado"],
            "causa_sin_zopa": res.get("causa_sin_zopa"),
            "batna_efectivo_actor_mxn": res["actor"]["batna_efectivo_vpn_mxn"],
            "piso_actor_masc_mxn": res["actor"]["minimo_pagable_aceptable_masc_mxn"],
            "techo_demandado_masc_mxn": res["demandado"]["maximo_pagable_ofertable_masc_mxn"],
            "amplitud_zopa_mxn": res["negociacion_masc"]["amplitud_zopa_mxn"],
            "punto_medio_referencia_mxn": res["negociacion_masc"]["punto_medio_referencia_mxn"],
            "litigio_tiene_valor_esperado_negativo": res["actor"]["litigio_tiene_valor_esperado_negativo"],
        })

    return {
        "estado": "OK",
        "resumen": {
            "total_escenarios": len(escenarios),
            "escenarios_con_zopa": con_zopa,
            "escenarios_sin_zopa": len(escenarios) - con_zopa,
        },
        "matriz_escenarios": escenarios,
        "convencion_financiera": (
            "Cada fila aplica aritmética Decimal con redondeo conservador al centavo "
            "(piso CEILING, techo FLOOR) y transferencia simétrica de costas reembolsables."
        ),
    }