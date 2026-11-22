from decimal import Decimal, InvalidOperation
from typing import Literal, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator


def _validar_decimal_finito(valor) -> Decimal:
    """Convierte a Decimal rechazando booleanos, NaN e infinitos."""
    if isinstance(valor, bool):
        raise ValueError("No se permiten valores booleanos como montos o tasas.")
    try:
        dec = valor if isinstance(valor, Decimal) else Decimal(str(valor).strip())
    except (InvalidOperation, AttributeError, ValueError) as exc:
        raise ValueError("Debe ser un número decimal válido.") from exc
    if not dec.is_finite():
        raise ValueError("El valor numérico debe ser finito.")
    return dec


def _rechazar_booleano_en_entero(valor):
    if isinstance(valor, bool):
        raise ValueError("No se permiten valores booleanos en campos enteros.")
    return valor


class ClausulaExtraida(BaseModel):
    """
    Esquema rígido de los atributos que el LLM debe extraer de una cláusula
    de solución de controversias junto con su cita textual exacta.
    """
    model_config = ConfigDict(extra="forbid")

    mecanismo_principal: Literal[
        "arbitraje",
        "mediacion",
        "conciliacion",
        "negociacion",
        "escalonada",
        "jurisdiccion_estatal",
        "ambiguo",
    ]
    institucion_administradora: Optional[str] = Field(
        default=None,
        description="Ej. 'CAM', 'CANACO', 'ICC', 'ad_hoc', o nombre literal en el contrato.",
    )
    sede_arbitraje: Optional[str] = Field(
        default=None,
        description="Ciudad o país designado como sede jurídica del arbitraje.",
    )
    idioma: Optional[str] = Field(default=None)
    ley_aplicable_fondo: Optional[str] = Field(default=None)
    numero_arbitros: Optional[int] = Field(default=None, ge=1, le=9)
    tiene_etapa_previa_obligatoria: bool = Field(
        default=False,
        description="True si exige negociación/mediación previa antes de arbitraje o juicio.",
    )
    plazo_etapa_previa_dias: Optional[int] = Field(
        default=None,
        ge=1,
        description="Plazo máximo expreso en días para agotar la etapa previa.",
    )
    somete_tambien_a_tribunales_locales: bool = Field(
        default=False,
        description="True si el contrato también somete el fondo a jueces estatales de forma contradictoria.",
    )
    cita_textual_clausula: str = Field(
        ...,
        min_length=15,
        description="Fragmento literal copiado del contrato para verificación determinista.",
    )

    @field_validator("numero_arbitros", "plazo_etapa_previa_dias", mode="before")
    @classmethod
    def _sin_booleanos_enteros(cls, v):
        if v is None:
            return None
        return _rechazar_booleano_en_entero(v)


class ParametrosLitigioEMV(BaseModel):
    """
    Insumos financieros estrictos para comparar el Valor Presente Esperado (EMV/VPN)
    de litigar un juicio mercantil frente a convenir en MASC.
    """
    model_config = ConfigDict(extra="forbid")

    monto_reclamado_mxn: Decimal = Field(..., gt=0)
    probabilidad_exito_actor: Decimal = Field(..., ge=0, le=1)
    costos_litigio_actor_mxn: Decimal = Field(..., ge=0)
    costos_litigio_demandado_mxn: Decimal = Field(..., ge=0)
    duracion_juicio_meses: int = Field(..., ge=1, le=120)
    tasa_descuento_anual: Decimal = Field(
        ...,
        ge=0,
        le=1,
        description="Costo de oportunidad anual del dinero en decimal (ej. 0.10 para 10%).",
    )
    costos_masc_actor_mxn: Decimal = Field(..., ge=0)
    costos_masc_demandado_mxn: Decimal = Field(..., ge=0)
    duracion_masc_meses: int = Field(default=1, ge=0, le=24)
    riesgo_costas_perdedor_mxn: Decimal = Field(
        default=Decimal("0.00"),
        ge=0,
        description="Condena estimada en costas procesales si se pierde el juicio (art. 1084 CCom).",
    )

    @field_validator(
        "monto_reclamado_mxn",
        "probabilidad_exito_actor",
        "costos_litigio_actor_mxn",
        "costos_litigio_demandado_mxn",
        "tasa_descuento_anual",
        "costos_masc_actor_mxn",
        "costos_masc_demandado_mxn",
        "riesgo_costas_perdedor_mxn",
        mode="before",
    )
    @classmethod
    def _parsear_decimales(cls, v):
        return _validar_decimal_finito(v)

    @field_validator("duracion_juicio_meses", "duracion_masc_meses", mode="before")
    @classmethod
    def _parsear_enteros(cls, v):
        return _rechazar_booleano_en_entero(v)