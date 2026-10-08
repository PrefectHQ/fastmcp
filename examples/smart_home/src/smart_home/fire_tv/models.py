from pydantic import BaseModel


class FireTVStatus(BaseModel):
    available: bool
    state: str | None
    current_app: str | None
    running_apps: list[str] | None
    hdmi_input: str | None
    manufacturer: str | None
    model: str | None


class CommandReceipt(BaseModel):
    command: str
    accepted: bool = True
    state_verified: bool = False
    next_step: str = "Read Fire TV status to verify the observed state."
