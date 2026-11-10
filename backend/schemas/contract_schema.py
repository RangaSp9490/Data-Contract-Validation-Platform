"""Pydantic models for contracts (requests + responses)."""
import re
from datetime import datetime
from typing import Any, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from typing_extensions import Annotated

DataType = Literal["string", "integer", "float", "decimal", "boolean", "date", "timestamp"]
CompatibilityMode = Literal["BACKWARD", "STRICT"]
Scalar = Union[bool, int, float, str]
IDENT = r"^[A-Za-z_][A-Za-z0-9_]{0,99}$"


class ColumnDef(BaseModel):
    name: str = Field(pattern=IDENT)
    data_type: DataType
    required: bool = True
    nullable: bool = False
    is_primary_key: bool = False
    allowed_values: Optional[List[Scalar]] = Field(default=None, min_length=1)
    default_value: Optional[Scalar] = None
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    max_length: Optional[int] = Field(default=None, gt=0)
    pattern: Optional[str] = Field(default=None, max_length=255)
    normalize: Optional[Literal["lower", "upper"]] = None
    renamed_from: Optional[str] = Field(default=None, pattern=IDENT)
    description: Optional[str] = Field(default=None, max_length=255)

    @field_validator("pattern")
    @classmethod
    def _pattern_compiles(cls, value: Optional[str]) -> Optional[str]:
        if value is not None:
            try:
                re.compile(value)
            except re.error as exc:
                raise ValueError(f"invalid regular expression: {exc}") from exc
        return value

    @model_validator(mode="after")
    def _consistent(self) -> "ColumnDef":
        if self.is_primary_key and (self.nullable or not self.required):
            raise ValueError(f"primary key column '{self.name}' must be required and non-nullable")
        if self.min_value is not None and self.max_value is not None and self.min_value > self.max_value:
            raise ValueError(f"column '{self.name}': min_value > max_value")
        if self.allowed_values and self.default_value is not None and self.default_value not in self.allowed_values:
            raise ValueError(f"column '{self.name}': default_value must be one of allowed_values")
        if self.normalize and self.data_type != "string":
            raise ValueError(f"column '{self.name}': normalize only applies to string columns")
        if self.renamed_from == self.name:
            raise ValueError(f"column '{self.name}': renamed_from cannot equal the column name")
        return self


class ForeignKeyDef(BaseModel):
    column: str = Field(pattern=IDENT)
    ref_dataset: str = Field(pattern=IDENT)
    ref_column: str = Field(pattern=IDENT)


class CompareColumnsRule(BaseModel):
    type: Literal["compare_columns"]
    name: str = Field(min_length=1, max_length=100)
    left: str
    op: Literal[">=", ">", "<=", "<", "==", "!="]
    right: str
    description: Optional[str] = None


class ProductEqualsRule(BaseModel):
    """target == factor1 * factor2 * ...  (within tolerance)."""
    type: Literal["product_equals"]
    name: str = Field(min_length=1, max_length=100)
    target: str
    factors: List[str] = Field(min_length=2)
    tolerance: float = Field(default=0.01, ge=0)
    description: Optional[str] = None


class NotInFutureRule(BaseModel):
    type: Literal["not_in_future"]
    name: str = Field(min_length=1, max_length=100)
    column: str
    tolerance_minutes: int = Field(default=5, ge=0)
    description: Optional[str] = None


BusinessRule = Annotated[
    Union[CompareColumnsRule, ProductEqualsRule, NotInFutureRule], Field(discriminator="type")
]


def rule_columns(rule: Any) -> List[str]:
    if rule.type == "compare_columns":
        return [rule.left, rule.right]
    if rule.type == "product_equals":
        return [rule.target, *rule.factors]
    return [rule.column]


class VersionSpec(BaseModel):
    """Everything that defines one immutable contract version."""
    compatibility_mode: CompatibilityMode = "BACKWARD"
    event_time_column: Optional[str] = None
    columns: List[ColumnDef] = Field(min_length=1)
    unique_constraints: List[List[str]] = Field(default_factory=list)
    foreign_keys: List[ForeignKeyDef] = Field(default_factory=list)
    business_rules: List[BusinessRule] = Field(default_factory=list)
    change_notes: Optional[str] = None
    created_by: Optional[str] = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def _references_exist(self) -> "VersionSpec":
        names = [c.name for c in self.columns]
        if len(names) != len(set(names)):
            raise ValueError("column names must be unique")
        by_name = {c.name: c for c in self.columns}
        if not any(c.is_primary_key for c in self.columns):
            raise ValueError("at least one column must be marked is_primary_key")
        renamed = [c.renamed_from for c in self.columns if c.renamed_from]
        if set(renamed) & set(names):
            raise ValueError("renamed_from must not be the name of another current column")

        def need(col: str, where: str) -> None:
            if col not in by_name:
                raise ValueError(f"{where} references unknown column '{col}'")

        for group in self.unique_constraints:
            if not group:
                raise ValueError("unique constraint cannot be empty")
            for col in group:
                need(col, "unique constraint")
        for fk in self.foreign_keys:
            need(fk.column, "foreign key")
        for rule in self.business_rules:
            for col in rule_columns(rule):
                need(col, f"business rule '{rule.name}'")
        if self.event_time_column:
            need(self.event_time_column, "event_time_column")
            if by_name[self.event_time_column].data_type not in ("timestamp", "date"):
                raise ValueError("event_time_column must be a timestamp or date column")
        return self


class ContractCreate(VersionSpec):
    name: str = Field(min_length=1, max_length=150)
    dataset_name: str = Field(pattern=IDENT)
    description: Optional[str] = None
    owner: Optional[str] = Field(default=None, max_length=100)


class VersionCreate(VersionSpec):
    # Breaking changes are allowed only when the caller explicitly acknowledges them.
    acknowledge_breaking: bool = False


# ---------------------------------------------------------------- responses
class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ColumnOut(ORM):
    name: str
    ordinal: int
    data_type: str
    required: bool
    nullable: bool
    is_primary_key: bool
    allowed_values: Optional[List[Any]] = None
    default_value: Optional[Any] = None
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    max_length: Optional[int] = None
    pattern: Optional[str] = None
    normalize: Optional[str] = None
    renamed_from: Optional[str] = None
    description: Optional[str] = None


class ForeignKeyOut(BaseModel):
    column: str
    ref_dataset: str
    ref_column: str


class ChangeOut(ORM):
    change_type: str
    column_name: Optional[str] = None
    old_schema: Optional[Any] = None
    new_schema: Optional[Any] = None
    severity: str
    classification: str
    explanation: str


class CompatibilityReport(BaseModel):
    from_version: Optional[int] = None
    compatible: bool
    breaking_count: int
    non_breaking_count: int
    changes: List[ChangeOut]


class VersionOut(BaseModel):
    id: int
    contract_id: int
    version: int
    compatibility_mode: str
    event_time_column: Optional[str]
    primary_key: List[str]
    unique_constraints: List[List[str]]
    business_rules: List[dict]
    change_notes: Optional[str]
    created_by: Optional[str]
    created_at: datetime
    columns: List[ColumnOut]
    foreign_keys: List[ForeignKeyOut]
    schema_changes: List[ChangeOut] = Field(default_factory=list)


class ContractOut(BaseModel):
    id: int
    name: str
    dataset_name: str
    description: Optional[str]
    owner: Optional[str]
    created_at: datetime
    updated_at: datetime
    latest_version: Optional[int]
    versions: List[int]


class ContractDetail(ContractOut):
    current: Optional[VersionOut]


class VersionCreated(BaseModel):
    version: VersionOut
    compatibility: CompatibilityReport
