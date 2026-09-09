"""Shared test fixture: a roles folder with six filled member profiles and
the conduct note, since the repository ships no members (spec 3.4)."""

from __future__ import annotations

from pathlib import Path

CLASSIC = ("Finance", "HW Engineering", "Mechanical Engineering", "Manufacturing", "SW Engineering")
PERSPECTIVES = {
    "Finance": "Cost, budget vs forecast vs actuals, cBOM impact",
    "HW Engineering": "Hardware feasibility, maturity, technical risk",
    "Mechanical Engineering": "Mechanical feasibility, packaging, tolerances",
    "Manufacturing": "Manufacturability, ramp-up, supplier and plant capability",
    "SW Engineering": "Software scope, integration and test effort",
}
ICONS = {"Finance": "dollar", "HW Engineering": "chip", "Mechanical Engineering": "gear",
         "Manufacturing": "factory", "SW Engineering": "code"}


def write_member(folder: Path, member: str, body: str = "", **meta) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    front = "\n".join(f"{key}: {value}" for key, value in {"member": member, **meta}.items())
    path = folder / f"{member}.md"
    text = body or (f"## Roles and responsibilities\n- {member} lead: owns the {member.lower()} view of every decision.\n"
                    f"- {member} working level: supplies the numbers and the risks.\n\n## What this member measures\n"
                    f"- The {member.lower()} KPI.\n\n## Vocabulary\nThe words of {member.lower()}.\n")
    path.write_text(f"---\n{front}\n---\n# {member}\n\n{text}", encoding="utf-8")
    return path


def make_roles(folder: Path, members=CLASSIC, *, conduct: bool = True) -> Path:
    for index, member in enumerate(members, start=1):
        write_member(folder, member, order=index,
                     perspective=PERSPECTIVES.get(member, f"The {member} view"), icon=ICONS.get(member, "person"))
    if conduct:
        (folder / "_Board member conduct.md").write_text(
            "---\nkind: conduct\n---\n# Conduct\n\nSceptical by default. Speak for every role under your wing.\n",
            encoding="utf-8")
    return folder
