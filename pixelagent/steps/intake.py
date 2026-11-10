"""Step 1: requirements (+ references) -> CharacterSpec. Gate A: the user confirms or edits spec.json."""
import os

from .. import prompts
from ..schemas import CharacterSpec


def run(ctx):
    inp = ctx.asset.state.inputs
    style = ctx.style
    refs = ctx.asset.references()

    def stub():
        return CharacterSpec(
            name=inp.get("name") or ctx.asset.id,
            asset_type=inp.get("asset_type", "character"),
            summary=inp.get("prompt") or "character from the reference image",
            view=inp.get("view") or "three_quarter",
            height_px=int(inp.get("height") or style["sprite_height"]),
            light=style["light"], outline=style["outline"], max_colors=int(style["max_colors"]),
        )

    text = "\n".join(filter(None, [inp.get("prompt"), *ctx.notes(),
                                   f"style guide: {dict(style)}", f"requested height: {inp.get('height')}"]))
    spec = ctx.backend.llm.structured("director", prompts.INTAKE, text, refs, CharacterSpec, stub)
    if inp.get("height"):  # explicit user values win over the model
        spec.height_px = int(inp["height"])
    elif spec.asset_type == "character":  # otherwise characters follow the project's style guide
        spec.height_px = int(style["sprite_height"])
    if spec.asset_type == "character" and style.get("look") == "soft" and not inp.get("keep_proportions"):
        spec.heads_tall = float(style.get("heads_tall", 4.5))   # the look sets the proportions (editable at gate A)
        spec.outline = "soft"
    if ctx.cfg.get("finish", {}).get("mode") == "reduce":
        # the reduce finish keeps soft shading: its colour budget is the reduction's, not the ramp palette's
        spec.max_colors = int(ctx.cfg["finish"].get("reduce_colors", 96))
    if inp.get("view"):
        spec.view = inp["view"]
    if inp.get("name"):  # a name the user typed is theirs; the director only names unnamed characters
        spec.name = inp["name"]
    p = ctx.save_json("spec", "spec.json", spec)
    ctx.info["edit_hint"] = f"edit {os.path.basename(p)} before approving gate A to change the spec"
    ctx.info["summary"] = f"{spec.name}: {spec.height_px}px {spec.view} {spec.asset_type}, {len(spec.materials)} materials"
