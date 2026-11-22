from decimal import Decimal, InvalidOperation
from typing import Literal, Optional
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    field_validator,
    model_validator,
)

MAX_MONTO_MXN = Decimal("100000000000000")  # 100 billones MXN (cota anti-overflow Decimal)
MAX_DECIMALES_MONTO = 4
MAX_DECIMALES_TASA = 6

EstadoExtraccion = Literal[
    "presente",
    "ausente_tras_revision",
    "no_determinado",
    "no_aplicable",
]


def _validar_decimal_finito(
    valor,
    max_valor: Optional[Decimal] = None,
    max_decimales: Optional[int] = None,
) -> Decimal:
    """Convierte a Decimal rechazando booleanos, NaN, infinitos, overflow y escala excesiva."""
    if isinstance(valor, bool):
        raise ValueError("No se permiten valores booleanos como montos o tasas.")
    try:
        dec = valor if isinstance(valor, Decimal) else Decimal(str(valor).strip())
    except (InvalidOperation, AttributeError, ValueError) as exc:
        raise ValueError("Debe ser un número decimal válido.") from exc
    if not dec.is_finite():
        raise ValueError("El valor numérico debe ser finito.")
    if max_valor is not None and abs(dec) > max_valor:
        raise ValueError(f"El valor excede el límite operativo permitido ({max_valor}).")
    if max_decimales is not None:
        exp = dec.normalize().as_tuple().exponent
        if isinstance(exp, int) and exp < -max_decimales:
            raise ValueError(
                f"El valor excede el máximo de {max_decimales} posiciones decimales permitidas."
            )
    return dec


def _validar_cadena_no_vacia(valor: Optional[str]) -> Optional[str]:
    if valor is None:
        return None
    if not isinstance(valor, str):
        raise ValueError("El valor debe ser una cadena de texto.")
    limpio = valor.strip()
    if not limpio:
        raise ValueError("No se permiten cadenas vacías o compuestas solo por espacios.")
    return limpio


class DatoTexto(BaseModel):
    """Atributo textual acompañado de su estado de extracción y cita literal específica."""
    model_config = ConfigDict(extra="forbid")

    estado: EstadoExtraccion = "no_determinado"
    valor: Optional[str] = None
    cita_literal: Optional[str] = None

    @field_validator("valor", "cita_literal", mode="before")
    @classmethod
    def _limpiar_textos(cls, v):
        return _validar_cadena_no_vacia(v)

    @model_validator(mode="after")
    def _validar_consistencia(self):
        if self.estado == "presente" and self.valor is None:
            raise ValueError("Cuando estado='presente', 'valor' es obligatorio.")
        if self.estado != "presente" and self.valor is not None:
            raise ValueError("Solo se permite asignar 'valor' cuando estado='presente'.")
        return self


class DatoPlazoDias(BaseModel):
    """Plazo en días (1 a 3650) acompañado de estado de extracción y cita literal."""
    model_config = ConfigDict(extra="forbid")

    estado: EstadoExtraccion = "no_determinado"
    valor: Optional[StrictInt] = Field(default=None, ge=1, le=3650)
    cita_literal: Optional[str] = None

    @field_validator("cita_literal", mode="before")
    @classmethod
    def _limpiar_cita(cls, v):
        return _validar_cadena_no_vacia(v)

    @model_validator(mode="after")
    def _validar_consistencia(self):
        if self.estado == "presente" and self.valor is None:
            raise ValueError("Cuando estado='presente', 'valor' es obligatorio.")
        if self.estado != "presente" and self.valor is not None:
            raise ValueError("Solo se permite asignar 'valor' cuando estado='presente'.")
        return self


class DatoNumeroArbitros(BaseModel):
    """Número de árbitros (cota operativa de software: 1 a 99) con estado y cita literal."""
    model_config = ConfigDict(extra="forbid")

    estado: EstadoExtraccion = "no_determinado"
    valor: Optional[StrictInt] = Field(default=None, ge=1, le=99)
    cita_literal: Optional[str] = None

    @field_validator("cita_literal", mode="before")
    @classmethod
    def _limpiar_cita(cls, v):
        return _validar_cadena_no_vacia(v)

    @model_validator(mode="after")
    def _validar_consistencia(self):
        if self.estado == "presente" and self.valor is None:
            raise ValueError("Cuando estado='presente', 'valor' es obligatorio.")
        if self.estado != "presente" and self.valor is not None:
            raise ValueError("Solo se permite asignar 'valor' cuando estado='presente'.")
        return self


class DatoBooleano(BaseModel):
    """Atributo booleano estricto acompañado de su estado de extracción y cita literal."""
    model_config = ConfigDict(extra="forbid")

    estado: EstadoExtraccion = "no_determinado"
    valor: Optional[StrictBool] = None
    cita_literal: Optional[str] = None

    @field_validator("cita_literal", mode="before")
    @classmethod
    def _limpiar_cita(cls, v):
        return _validar_cadena_no_vacia(v)

    @model_validator(mode="after")
    def _validar_consistencia(self):
        if self.estado == "presente" and self.valor is None:
            raise ValueError("Cuando estado='presente', 'valor' es obligatorio.")
        if self.estado != "presente" and self.valor is not None:
            raise ValueError("Solo se permite asignar 'valor' cuando estado='presente'.")
        return self


class EtapaPrevia(BaseModel):
    """Describe una etapa MASC previa independiente del foro final."""
    model_config = ConfigDict(extra="forbid")

    tipo: Literal["negociacion", "mediacion", "conciliacion", "otro"]
    cita_tipo: Optional[str] = None
    obligatoria: DatoBooleano = Field(default_factory=DatoBooleano)
    plazo_dias: DatoPlazoDias = Field(default_factory=DatoPlazoDias)
    condicion_cierre_objetiva: DatoTexto = Field(default_factory=DatoTexto)

    @field_validator("cita_tipo", mode="before")
    @classmethod
    def _limpiar_cita_tipo(cls, v):
        return _validar_cadena_no_vacia(v)


class ClausulaExtraida(BaseModel):
    """
    Esquema v0.3.1 con evidencia granular por atributo, separación estricta entre
    etapa previa y mecanismo final, y modalidad arbitral desacoplada de institución.
    """
    model_config = ConfigDict(extra="forbid")

    mecanismo_final: Literal[
        "arbitraje",
        "jurisdiccion_estatal",
        "mediacion",
        "conciliacion",
        "negociacion",
        "ambiguo",
    ]
    cita_textual_clausula: str = Field(
        ...,
        min_length=15,
        description="Fragmento completo de la cláusula copiado literalmente del contrato.",
    )
    cita_mecanismo_final: Optional[str] = Field(
        default=None,
        description="Subfragmento específico que acredita el mecanismo final.",
    )
    modalidad_arbitraje: Literal["institucional", "ad_hoc", "no_determinado", "no_aplicable"] = "no_determinado"
    etapa_previa: Optional[EtapaPrevia] = None
    institucion_administradora: DatoTexto = Field(default_factory=DatoTexto)
    reglamento_arbitral: DatoTexto = Field(default_factory=DatoTexto)
    sede_arbitraje: DatoTexto = Field(default_factory=DatoTexto)
    idioma: DatoTexto = Field(default_factory=DatoTexto)
    ley_aplicable_fondo: DatoTexto = Field(default_factory=DatoTexto)
    numero_arbitros: DatoNumeroArbitros = Field(default_factory=DatoNumeroArbitros)
    somete_tambien_a_tribunales_locales: DatoBooleano = Field(default_factory=DatoBooleano)

    @field_validator("cita_textual_clausula", "cita_mecanismo_final", mode="before")
    @classmethod
    def _limpiar_citas_generales(cls, v):
        return _validar_cadena_no_vacia(v)


class ParametrosLitigioEMV(BaseModel):
    """Insumos financieros estrictos para modelado BATNA / EMV / VPN / ZOPA."""
    model_config = ConfigDict(extra="forbid")

    monto_reclamado_mxn: Decimal = Field(..., gt=0, le=MAX_MONTO_MXN)
    probabilidad_exito_actor: Decimal = Field(..., ge=0, le=1)
    costos_litigio_actor_mxn: Decimal = Field(..., ge=0, le=MAX_MONTO_MXN)
    costos_litigio_demandado_mxn: Decimal = Field(..., ge=0, le=MAX_MONTO_MXN)
    duracion_juicio_meses: StrictInt = Field(..., ge=1, le=120)
    tasa_descuento_anual: Decimal = Field(..., ge=0, le=1)
    costos_masc_actor_mxn: Decimal = Field(..., ge=0, le=MAX_MONTO_MXN)
    costos_masc_demandado_mxn: Decimal = Field(..., ge=0, le=MAX_MONTO_MXN)
    duracion_masc_meses: StrictInt = Field(default=1, ge=0, le=24)
    costas_reembolsables_entre_partes_mxn: Decimal = Field(
        default=Decimal("0.00"),
        ge=0,
        le=MAX_MONTO_MXN,
        description="Reembolso de costas del perdedor al vencedor en t=duracion_juicio_meses (arts. 1082 y 1084 CCom).",
    )
    costos_externos_perdedor_mxn: Decimal = Field(
        default=Decimal("0.00"),
        ge=0,
        le=MAX_MONTO_MXN,
        description="Gastos o sanciones procesales irrecuperables pagados a terceros en caso de derrota.",
    )
    actor_puede_desistir_sin_costo: StrictBool = Field(
        default=False,
        description="Si True, el BATNA del actor se acota inferiormente en 0.00 (opción de retirarse/no demandar).",
    )
    demandado_conoce_retiro_actor: StrictBool = Field(
        default=False,
        description="Solo aplica si el actor elige retirarse: si True, el costo sin convenio del demandado cae a 0.00.",
    )
    permitir_convenio_cero: StrictBool = Field(
        default=False,
        description="Si False, únicamente se consideran ofertas estrictamente positivas (>= $0.01).",
    )

    @field_validator(
        "monto_reclamado_mxn",
        "costos_litigio_actor_mxn",
        "costos_litigio_demandado_mxn",
        "costos_masc_actor_mxn",
        "costos_masc_demandado_mxn",
        "costas_reembolsables_entre_partes_mxn",
        "costos_externos_perdedor_mxn",
        mode="before",
    )
    @classmethod
    def _parsear_montos(cls, v):
        return _validar_decimal_finito(
            v, max_valor=MAX_MONTO_MXN, max_decimales=MAX_DECIMALES_MONTO
        )

    @field_validator("probabilidad_exito_actor", "tasa_descuento_anual", mode="before")
    @classmethod
    def _parsear_tasas(cls, v):
        return _validar_decimal_finito(
            v, max_valor=Decimal("1"), max_decimales=MAX_DECIMALES_TASA
        )


class ParametrosSensibilidadEMV(BaseModel):
    """Insumos para matriz de sensibilidad BATNA/ZOPA sobre múltiples probabilidades."""
    model_config = ConfigDict(extra="forbid")

    monto_reclamado_mxn: Decimal = Field(..., gt=0, le=MAX_MONTO_MXN)
    probabilidades_exito_actor: list[Decimal] = Field(
        default_factory=lambda: [
            Decimal("0.35"),
            Decimal("0.50"),
            Decimal("0.65"),
            Decimal("0.80"),
        ],
        min_length=1,
        max_length=10,
    )
    costos_litigio_actor_mxn: Decimal = Field(..., ge=0, le=MAX_MONTO_MXN)
    costos_litigio_demandado_mxn: Decimal = Field(..., ge=0, le=MAX_MONTO_MXN)
    duracion_juicio_meses: StrictInt = Field(..., ge=1, le=120)
    tasa_descuento_anual: Decimal = Field(..., ge=0, le=1)
    costos_masc_actor_mxn: Decimal = Field(..., ge=0, le=MAX_MONTO_MXN)
    costos_masc_demandado_mxn: Decimal = Field(..., ge=0, le=MAX_MONTO_MXN)
    duracion_masc_meses: StrictInt = Field(default=1, ge=0, le=24)
    costas_reembolsables_entre_partes_mxn: Decimal = Field(
        default=Decimal("0.00"), ge=0, le=MAX_MONTO_MXN
    )
    costos_externos_perdedor_mxn: Decimal = Field(
        default=Decimal("0.00"), ge=0, le=MAX_MONTO_MXN
    )
    actor_puede_desistir_sin_costo: StrictBool = Field(default=False)
    demandado_conoce_retiro_actor: StrictBool = Field(default=False)
    permitir_convenio_cero: StrictBool = Field(default=False)

    @field_validator(
        "monto_reclamado_mxn",
        "costos_litigio_actor_mxn",
        "costos_litigio_demandado_mxn",
        "costos_masc_actor_mxn",
        "costos_masc_demandado_mxn",
        "costas_reembolsables_entre_partes_mxn",
        "costos_externos_perdedor_mxn",
        mode="before",
    )
    @classmethod
    def _parsear_montos(cls, v):
        return _validar_decimal_finito(
            v, max_valor=MAX_MONTO_MXN, max_decimales=MAX_DECIMALES_MONTO
        )

    @field_validator("tasa_descuento_anual", mode="before")
    @classmethod
    def _parsear_tasa(cls, v):
        return _validar_decimal_finito(
            v, max_valor=Decimal("1"), max_decimales=MAX_DECIMALES_TASA
        )

    @field_validator("probabilidades_exito_actor", mode="before")
    @classmethod
    def _parsear_lista_probabilidades(cls, v):
        if not isinstance(v, list):
            raise ValueError("'probabilidades_exito_actor' debe ser una lista.")
        resultado = []
        for item in v:
            dec = _validar_decimal_finito(
                item, max_valor=Decimal("1"), max_decimales=MAX_DECIMALES_TASA
            )
            if dec < Decimal("0") or dec > Decimal("1"):
                raise ValueError("Cada probabilidad debe estar entre 0 y 1.")
            resultado.append(dec)
        return resultado