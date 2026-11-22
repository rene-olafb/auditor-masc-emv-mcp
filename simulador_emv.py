from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, localcontext
from pydantic import ValidationError
from esquemas import ParametrosLitigioEMV, ParametrosSensibilidadEMV

CENTAVO = Decimal("0.01")


def _fmt_mxn(valor: Decimal, modo: str = ROUND_HALF_UP) -> str:
    """Formatea un Decimal a dos posiciones decimales con el modo de redondeo especificado."""
    return str(valor.quantize(CENTAVO, rounding=modo))


def _factor_compuesto(tasa_anual: Decimal, delta_meses: int) -> Decimal:
    """
    Calcula (1 + r)^(delta_meses / 12) con precisión intermedia de 50 dígitos.
    Garantiza identidad exacta Decimal('1') cuando delta_meses == 0 o tasa_anual == 0.
    """
    if delta_meses == 0 or tasa_anual == Decimal("0"):
        return Decimal("1")
    with localcontext() as ctx:
        ctx.prec = 50
        exponente = Decimal(delta_meses) / Decimal("12")
        return (Decimal("1") + tasa_anual) ** exponente


def simular_escenario_batna_emv(parametros: dict) -> dict:
    """
    Modela determinísticamente en Decimal el Valor Presente Neto (VPN) de litigar
    frente a convenir en MASC, contabilizando transferencia simétrica de costas
    reembolsables, identidad algebraica cuando J == T, redondeo conservador al
    centavo y consistencia estratégica ante retiro del actor.
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
        factor_juicio = _factor_compuesto(p.tasa_descuento_anual, p.duracion_juicio_meses)
        factor_masc = _factor_compuesto(p.tasa_descuento_anual, p.duracion_masc_meses)
        # Factor relativo (1 + r)^((T - J) / 12): evita dividir y multiplicar cuando T == J
        factor_juicio_a_masc = _factor_compuesto(
            p.tasa_descuento_anual, p.duracion_masc_meses - p.duracion_juicio_meses
        )

        k_reemb = p.costas_reembolsables_entre_partes_mxn
        k_ext = p.costos_externos_perdedor_mxn

        # 1. Flujos esperados del fallo en t = duracion_juicio_meses
        flujo_esperado_juicio_actor = (
            prob_exito * (p.monto_reclamado_mxn + k_reemb)
            - prob_derrota * (k_reemb + k_ext)
        )
        costo_esperado_juicio_demandado = (
            prob_exito * (p.monto_reclamado_mxn + k_reemb + k_ext)
            - prob_derrota * k_reemb
        )

        # Métricas netas de costos propios (costos propios imputados en t=0)
        emv_neto_sin_descuento_fallo_actor = flujo_esperado_juicio_actor - p.costos_litigio_actor_mxn
        emv_costo_neto_sin_descuento_fallo_demandado = (
            costo_esperado_juicio_demandado + p.costos_litigio_demandado_mxn
        )

        # 2. Valor Presente Neto (VPN) de la rama de litigio
        vpn_litigio_actor = (flujo_esperado_juicio_actor / factor_juicio) - p.costos_litigio_actor_mxn
        vpn_costo_litigio_demandado = (
            costo_esperado_juicio_demandado / factor_juicio
        ) + p.costos_litigio_demandado_mxn

        # 3. Alternativas de referencia (BATNA) de actor y demandado
        actor_elige_retiro = bool(p.actor_puede_desistir_sin_costo and vpn_litigio_actor < Decimal("0"))
        if actor_elige_retiro:
            batna_efectivo_actor = Decimal("0.00")
            alternativa_actor = "desistir_o_no_demandar"
            piso_matematico_actor = p.costos_masc_actor_mxn * factor_masc
        else:
            batna_efectivo_actor = vpn_litigio_actor
            alternativa_actor = "litigar"
            piso_matematico_actor = (
                flujo_esperado_juicio_actor * factor_juicio_a_masc
                + (p.costos_masc_actor_mxn - p.costos_litigio_actor_mxn) * factor_masc
            )

        if actor_elige_retiro and p.demandado_conoce_retiro_actor:
            vpn_costo_sin_convenio_demandado = Decimal("0.00")
            alternativa_demandado = "actor_se_retira_sin_costo"
            techo_matematico_demandado = -p.costos_masc_demandado_mxn * factor_masc
        else:
            vpn_costo_sin_convenio_demandado = vpn_costo_litigio_demandado
            alternativa_demandado = "litigar"
            techo_matematico_demandado = (
                costo_esperado_juicio_demandado * factor_juicio_a_masc
                + (p.costos_litigio_demandado_mxn - p.costos_masc_demandado_mxn) * factor_masc
            )

        # 4. Redondeo conservador al centavo pagable:
        # Piso redondea hacia arriba (CEILING); techo hacia abajo (FLOOR).
        piso_no_negativo_mat = max(Decimal("0.00"), piso_matematico_actor)
        piso_redondeado_centavos = piso_no_negativo_mat.quantize(CENTAVO, rounding=ROUND_CEILING)
        piso_operativo_centavos = (
            piso_redondeado_centavos
            if p.permitir_convenio_cero
            else max(CENTAVO, piso_redondeado_centavos)
        )
        techo_pagable_centavos = techo_matematico_demandado.quantize(CENTAVO, rounding=ROUND_FLOOR)

        # 5. Evaluación de viabilidad de ZOPA sobre importes pagables
        ningun_pago_no_negativo_demandado = techo_pagable_centavos < Decimal("0.00")
        causa_sin_zopa = None

        if ningun_pago_no_negativo_demandado:
            existe_zopa = False
            estado_negociacion = "SIN_ZOPA"
            causa_sin_zopa = "ningun_pago_no_negativo_satisface_al_demandado"
        elif techo_pagable_centavos < piso_operativo_centavos:
            existe_zopa = False
            estado_negociacion = "SIN_ZOPA"
            if (
                not p.permitir_convenio_cero
                and techo_pagable_centavos == Decimal("0.00")
                and piso_redondeado_centavos == Decimal("0.00")
            ):
                causa_sin_zopa = "acuerdo_solo_en_cero_sin_pago"
            elif techo_matematico_demandado >= piso_no_negativo_mat:
                causa_sin_zopa = "sin_monto_pagable_en_centavos"
            elif actor_elige_retiro:
                causa_sin_zopa = "alternativa_desistir_supera_oferta_demandado"
            else:
                causa_sin_zopa = "costos_masc_superan_ahorro_litigio"
        else:
            existe_zopa = True
            estado_negociacion = "ZOPA_VIABLE"

        if existe_zopa:
            amplitud_zopa = techo_pagable_centavos - piso_operativo_centavos
            punto_medio_zopa = (
                (piso_operativo_centavos + techo_pagable_centavos) / Decimal("2")
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
            "Aritmética Decimal (precisión intermedia de 50 dígitos). Flujos del fallo y costas "
            "se capitalizan/descuentan con identidad exacta (1+r)^((T-J)/12) (=1 cuando J==T); "
            "costos propios se imputan en t=0. Costas reembolsables se transfieren entre partes. "
            "Viabilidad decidida tras redondeo conservador al centavo (piso CEILING, techo FLOOR)."
        ),
        "supuestos_activos": {
            "probabilidad_compartida_actor_demandado": str(prob_exito),
            "cobro_integro_al_ganar": True,
            "neutralidad_al_riesgo": True,
            "alternativa_actor_modelada": alternativa_actor,
            "alternativa_demandado_modelada": alternativa_demandado,
            "permitir_convenio_cero": p.permitir_convenio_cero,
            "piso_minimo_operativo_mxn": "0.00" if p.permitir_convenio_cero else "0.01",
        },
        "actor": {
            "emv_neto_sin_descuento_fallo_mxn": _fmt_mxn(emv_neto_sin_descuento_fallo_actor),
            "vpn_litigio_mxn": _fmt_mxn(vpn_litigio_actor),
            "batna_efectivo_vpn_mxn": _fmt_mxn(batna_efectivo_actor),
            "punto_indiferencia_matematico_mxn": _fmt_mxn(piso_matematico_actor),
            "minimo_pagable_aceptable_masc_mxn": _fmt_mxn(piso_operativo_centavos, ROUND_CEILING),
            "litigio_tiene_valor_esperado_negativo": vpn_litigio_actor < Decimal("0"),
        },
        "demandado": {
            "emv_costo_neto_sin_descuento_fallo_mxn": _fmt_mxn(emv_costo_neto_sin_descuento_fallo_demandado),
            "vpn_costo_litigio_mxn": _fmt_mxn(vpn_costo_litigio_demandado),
            "batna_efectivo_vpn_costo_mxn": _fmt_mxn(vpn_costo_sin_convenio_demandado),
            "limite_matematico_pago_masc_mxn": _fmt_mxn(techo_matematico_demandado),
            "maximo_pagable_ofertable_masc_mxn": maximo_ofertable_str,
            "ningun_pago_no_negativo_satisface": ningun_pago_no_negativo_demandado,
        },
        "negociacion_masc": {
            "existe_zopa": existe_zopa,
            "rango_acuerdo_pagable_mxn": {
                "piso_actor": _fmt_mxn(piso_operativo_centavos, ROUND_CEILING),
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
    éxito (por defecto 0.35, 0.50, 0.65 y 0.80) propagando cualquier error controlado.
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
        if res["estado"] not in {"ZOPA_VIABLE", "SIN_ZOPA"}:
            return {
                "estado": res["estado"],
                "mensaje": f"Error al evaluar probabilidad {prob}: {res.get('mensaje', 'error desconocido')}",
                "probabilidad_fallida": str(prob),
            }
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
            "Cada fila aplica aritmética Decimal (50 dígitos intermedios), identidad exacta "
            "cuando J==T, redondeo conservador al centavo y transferencia de costas reembolsables."
        ),
    }