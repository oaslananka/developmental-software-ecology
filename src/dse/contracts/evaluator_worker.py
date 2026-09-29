from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class WorkerEvaluationAggregate(StrictModel):
    passed_cases: int = Field(ge=0)
    failed_cases: int = Field(ge=0)
    total_cases: int = Field(ge=1)
    duration_ms: int = Field(ge=0, le=3_600_000)

    @model_validator(mode="after")
    def validate_case_totals(self):
        if self.passed_cases + self.failed_cases != self.total_cases:
            raise ValueError(
                "passed_cases + failed_cases must equal total_cases"
            )
        return self
