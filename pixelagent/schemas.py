"""Typed data passed between pipeline steps and returned by models (pydantic; usable as OpenAI structured outputs)."""
from typing import Literal, Optional

from pydantic import BaseModel, Field


class Material(BaseModel):
    name: str = Field(description="Short id, e.g. skin, hair, coat, metal, eyes")
    description: str = ""
    base_color: Optional[str] = Field(None, description="Hex colour hint, e.g. #c8553d")
    priority: int = Field(1, description="1 = normal, 2 = focal (face/eyes), 0 = minor")


class CharacterSpec(BaseModel):
    name: str
    asset_type: Literal["character", "prop", "tile", "scene"] = "character"
    summary: str = Field("", description="One-paragraph description of the subject")
    must_keep: list[str] = Field(default_factory=list, description="Identity features that must survive pixelization")
    view: Literal["front", "three_quarter", "side", "back"] = "three_quarter"
    height_px: int = Field(96, ge=8, le=512, description="Sprite height in pixels")
    heads_tall: Optional[float] = Field(None, description="Proportions in head-heights, if known")
    light: str = "top-left"
    outline: Literal["soft", "selout", "dark", "none"] = "soft"
    max_colors: int = 32
    materials: list[Material] = Field(default_factory=list)
    detail_priorities: list[str] = Field(default_factory=lambda: ["face", "hands"])
    animations: list[str] = Field(default_factory=list)
    notes: str = ""


class MaterialList(BaseModel):
    """Every distinct material visible in a concept image (used to build the material map)."""
    materials: list[Material] = Field(description="2-16 materials, each a visually separate region type")


class Region(BaseModel):
    name: str = Field(description="face, left_hand, crystal, belt_buckle ...")
    x: int
    y: int
    w: int
    h: int
    goal: str = Field(description="What a pixel artist should achieve in this region")


class RegionPlan(BaseModel):
    regions: list[Region]


class RegionEdit(BaseModel):
    rows: list[str] = Field(description="Exactly h strings of exactly w characters: palette keys, '.' transparent, '=' keep")
    rationale: str = ""


class RegionVariants(BaseModel):
    """Several alternative rewrites of one region from a single call (shared images and reasoning)."""
    variants: list[RegionEdit]


class FramePlan(BaseModel):
    index: int
    pose: str = Field(description="Concrete pose: body, each limb, held items, hair/cloth secondary motion")
    duration_ms: int = Field(110, ge=20, le=2000)


class AnimationSpec(BaseModel):
    """An animation planned from the user's prompt for an approved character."""
    name: str
    summary: str = ""
    frames: list[FramePlan] = Field(description="2-12 frames in order")
    loop: bool = True
    in_place: bool = Field(True, description="True: the character animates on the spot (walk cycles in place)")
    moves: list[str] = Field(default_factory=list, description="What moves between frames")
    constant: list[str] = Field(default_factory=list, description="What must stay identical in every frame")


class Pick(BaseModel):
    """A model's choice among numbered candidates."""
    index: int = Field(description="0-based index of the best candidate")
    reason: str = ""
    ranking: list[int] = Field(default_factory=list)


class ClusterLabel(BaseModel):
    cluster: int
    material: str = Field(description="Material name from the spec, or 'outline'/'background'")


class MaterialLabels(BaseModel):
    labels: list[ClusterLabel]


class Issue(BaseModel):
    area: str = Field(description="Where: 'face', 'left hand', 'x=10..20,y=4..12', 'global'")
    region: Optional[list[int]] = Field(None, description="[x, y, w, h] in sprite pixels if known")
    severity: Literal["high", "medium", "low"] = "medium"
    problem: str
    fix: str = ""


class Scores(BaseModel):
    """0-10 per rubric item (explicit fields: strict structured outputs do not allow free-form dicts)."""
    silhouette: float = 0
    proportions: float = 0
    fidelity: float = 0
    palette: float = 0
    lighting: float = 0
    lines: float = 0
    detail: float = 0
    cleanliness: float = 0


class Critique(BaseModel):
    scores: Scores = Field(default_factory=Scores)
    overall: float = Field(0, description="0-10")
    issues: list[Issue] = Field(default_factory=list)
    verdict: Literal["pass", "repair", "fail"] = "repair"


class GateDecision(BaseModel):
    gate: str
    approved: bool
    pick: Optional[int] = None
    note: str = ""


class StepRecord(BaseModel):
    name: str
    status: Literal["pending", "done", "skipped", "awaiting_gate", "failed"] = "pending"
    version: int = 0
    outputs: dict[str, str] = Field(default_factory=dict)
    info: dict = Field(default_factory=dict)
    error: str = ""


class AssetState(BaseModel):
    id: str
    # Display/folder IDs can be reused or renamed; accounting follows this identity instead.
    asset_uuid: str = ""
    legacy_costs: dict[str, float] = Field(default_factory=dict)
    inputs: dict = Field(default_factory=dict)
    steps: dict[str, StepRecord] = Field(default_factory=dict)
    gates: dict[str, GateDecision] = Field(default_factory=dict)
    status: str = "new"
