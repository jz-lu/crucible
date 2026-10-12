from typing import Annotated
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Credentials(Input):
    handle: str = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_]+$")
    password: str = Field(min_length=12, max_length=128)

    @field_validator("handle")
    @classmethod
    def normalize_handle(cls, value):
        return value.lower()


class NewChallenge(Input):
    profile_id: str | None = Field(default=None, min_length=1, max_length=100)
    scheme_id: str | None = Field(default=None, min_length=1, max_length=100)
    instance_size: int | None = Field(default=None, gt=0, strict=True)

    @model_validator(mode="after")
    def one_selector(self):
        if self.model_fields_set == {"profile_id"} and self.profile_id is not None:
            return self
        if (self.model_fields_set == {"scheme_id", "instance_size"} and
                self.scheme_id is not None and self.instance_size is not None):
            return self
        raise ValueError("Choose a profile identifier or a scheme with an integer instance size.")


class NewMethod(Input):
    name: str = Field(min_length=1, max_length=100)
    pseudocode: str = Field(min_length=1, max_length=15000)

    @field_validator("name", "pseudocode")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("This field must not be blank.")
        return value.strip()


class Compute(Input):
    hardware: str = Field(min_length=1, max_length=500)
    workers: int = Field(ge=1, le=1000000, strict=True)
    wall_seconds: float = Field(ge=0, le=315576000)
    precomputation: str = Field(min_length=1, max_length=2000)
    peak_memory: str = Field(min_length=1, max_length=300)
    notes: str = Field(default="", max_length=2000)

    @field_validator("hardware", "precomputation", "peak_memory")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("This field must not be blank.")
        return value.strip()


class NewSubmission(Input):
    candidate: Annotated[str, StringConstraints(min_length=2, max_length=8192, pattern=r"^(?:[0-9a-fA-F]{2})+$")]
    method_id: UUID
    compute: Compute
    idempotency_key: UUID

    @field_validator("candidate")
    @classmethod
    def canonical(cls, value):
        return value.lower()
