from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path
from types import SimpleNamespace

from agent_framework import FileSkillsSource, MCPSkillsSource, SkillsProvider, SkillsSourceContext


SKILLS_ROOT = Path(__file__).parent / "skills" / "base"
EXPECTED_SKILLS = {
    "support-style": "STYLE-CANARY-3318",
    "escalation-policy": "ESC-CANARY-7742",
    "profile-update-policy": "PROFILE-CANARY-2209",
}


FRONTMATTER_RE = re.compile(r"\A---\n(?P<frontmatter>.*?)\n---\n", re.DOTALL)
FIELD_RE = re.compile(r"^(?P<key>[a-zA-Z0-9_-]+):\s*(?P<value>.+?)\s*$", re.MULTILINE)


def read_frontmatter(skill_file: Path) -> dict[str, str]:
    text = skill_file.read_text(encoding="utf-8")
    match = FRONTMATTER_RE.match(text)
    if not match:
        raise AssertionError(f"{skill_file} is missing YAML frontmatter.")
    return {
        field_match.group("key"): field_match.group("value").strip().strip("\"'")
        for field_match in FIELD_RE.finditer(match.group("frontmatter"))
    }


def validate_skill_files() -> None:
    for skill_name, canary in EXPECTED_SKILLS.items():
        skill_dir = SKILLS_ROOT / skill_name
        skill_file = skill_dir / "SKILL.md"
        if not skill_file.exists():
            raise AssertionError(f"Missing skill file: {skill_file}")

        frontmatter = read_frontmatter(skill_file)
        if frontmatter.get("name") != skill_name:
            raise AssertionError(f"{skill_file} name must be {skill_name}.")
        if not frontmatter.get("description"):
            raise AssertionError(f"{skill_file} must have a description.")
        if canary not in skill_file.read_text(encoding="utf-8"):
            raise AssertionError(f"{skill_file} must include {canary}.")

    matrix = SKILLS_ROOT / "escalation-policy" / "references" / "escalation-matrix.md"
    if "ESC-CANARY-7742" not in matrix.read_text(encoding="utf-8"):
        raise AssertionError("Escalation matrix must include ESC-CANARY-7742.")


async def validate_agent_framework_skills() -> None:
    provider = SkillsProvider.from_paths(skill_paths=SKILLS_ROOT)
    if provider is None:
        raise AssertionError("SkillsProvider.from_paths returned None.")

    source = FileSkillsSource(SKILLS_ROOT)
    skills = await source.get_skills(
        SkillsSourceContext(agent=SimpleNamespace(name="skill-smoke"))
    )
    discovered = {skill.frontmatter.name: skill for skill in skills}
    missing = EXPECTED_SKILLS.keys() - discovered.keys()
    if missing:
        raise AssertionError(f"Missing discovered skills: {sorted(missing)}")

    escalation = discovered["escalation-policy"]
    resource = await escalation.get_resource("references/escalation-matrix.md")
    if resource is None:
        raise AssertionError("Escalation resource was not discovered.")
    resource_content = await resource.read()
    if "ESC-CANARY-7742" not in resource_content:
        raise AssertionError("Escalation resource did not return expected canary.")

    content = await escalation.get_content()
    if "references/escalation-matrix.md" not in content:
        raise AssertionError("Escalation skill content should reference its resource.")

    if MCPSkillsSource is None:
        raise AssertionError("MCPSkillsSource is unavailable; toolbox skill spike cannot run.")


def main() -> int:
    validate_skill_files()
    asyncio.run(validate_agent_framework_skills())
    print("foundry showcase skills ok")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"skill smoke failed: {exc}", file=sys.stderr)
        raise
