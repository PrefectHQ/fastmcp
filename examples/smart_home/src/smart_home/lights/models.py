"""The agent-facing lighting contract, independent of Hue's wire representation."""

from typing import Any, Literal

from phue import LightState
from phue.models import Light, ResourceIdentifier
from pydantic import BaseModel, ConfigDict, Field, model_validator


class RoomState(BaseModel):
    """Ordinary room controls. Native effects must target individual lights."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    on: bool | None = None
    brightness: float | None = Field(
        default=None, ge=0, le=100, description="Brightness percent."
    )
    temperature_kelvin: int | None = Field(
        default=None, ge=2000, le=6500, description="Color temperature in kelvin."
    )
    xy: tuple[float, float] | None = Field(
        default=None,
        description="CIE color coordinates [x, y], mutually exclusive with temperature.",
    )
    transition_seconds: float | None = Field(default=None, ge=0, le=3600)

    @model_validator(mode="after")
    def validate_update(self) -> "RoomState":
        self.to_light_state()
        return self

    def to_light_state(self) -> LightState:
        return LightState.model_validate(self.model_dump(exclude_none=True))


class ObservedState(BaseModel):
    on: bool | None
    brightness: float | None = Field(
        description="Observed brightness percent; an effect may vary it over time."
    )
    xy: tuple[float, float] | None
    temperature_kelvin: int | None = Field(
        description="Current valid color temperature, otherwise null."
    )
    effect: str | None
    effect_speed: float | None = Field(
        description="Native effect speed, 0–1; not flicker amplitude."
    )
    effect_parameters: dict[str, Any] = Field(
        description="Reported native effect parameters, including its color."
    )


class LightInfo(BaseModel):
    name: str
    state: ObservedState
    connectivity: str | None
    supported_effects: list[str]
    capabilities: dict[str, Any]
    hue_details: dict[str, Any] | None = Field(
        default=None, description="Full Hue resource, included only when details=true."
    )


def describe_light(light: Light, connectivity: str | None, details: bool) -> LightInfo:
    status = (light.effects_v2 or {}).get("status", {})
    parameters = status.get("parameters", {})
    xy = (light.color or {}).get("xy")
    temperature = light.color_temperature or {}
    mirek = temperature.get("mirek")
    return LightInfo(
        name=light.metadata.name,
        state=ObservedState(
            on=light.on.get("on"),
            brightness=light.dimming.get("brightness"),
            xy=(xy["x"], xy["y"]) if xy else None,
            temperature_kelvin=round(1_000_000 / mirek)
            if mirek and temperature.get("mirek_valid")
            else None,
            effect=status.get("effect"),
            effect_speed=parameters.get("speed"),
            effect_parameters=parameters,
        ),
        connectivity=connectivity,
        supported_effects=light.supported_effects
        if light.effects_v2 is not None
        else [],
        capabilities={
            "color": light.color is not None,
            "color_gamut": (light.color or {}).get("gamut"),
            "temperature_mirek_range": temperature.get("mirek_schema"),
            "minimum_brightness": light.dimming.get("min_dim_level"),
        },
        hue_details=light.model_dump(exclude_none=True) if details else None,
    )


class WriteReceipt(BaseModel):
    target_id: str
    status: Literal["accepted"] = "accepted"
    accepted: list[ResourceIdentifier]
    state_verified: Literal[False] = False
    next_step: str = "Read lights in the affected room after any transition to verify observed state."
