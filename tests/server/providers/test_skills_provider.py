"""Tests for SkillProvider, SkillsDirectoryProvider, and ClaudeSkillsProvider."""

import json
from pathlib import Path

import pytest
from mcp.types import TextResourceContents
from pydantic import AnyUrl

from fastmcp import Client, FastMCP
from fastmcp.server.providers.skills import (
    ClaudeSkillsProvider,
    SkillProvider,
    SkillsDirectoryProvider,
    SkillsProvider,
)
from fastmcp.server.providers.skills._common import parse_frontmatter


class TestParseFrontmatter:
    def test_no_frontmatter(self):
        content = "# Just markdown\n\nSome content."
        frontmatter, body = parse_frontmatter(content)
        assert frontmatter == {}
        assert body == content

    def test_basic_frontmatter(self):
        content = """---
description: A test skill
version: "1.0.0"
---

# Skill Content
"""
        frontmatter, body = parse_frontmatter(content)
        assert frontmatter["description"] == "A test skill"
        assert frontmatter["version"] == "1.0.0"
        assert body.strip().startswith("# Skill Content")

    def test_frontmatter_with_tags_list(self):
        content = """---
description: Test
tags: [tag1, tag2, tag3]
---

Content
"""
        frontmatter, body = parse_frontmatter(content)
        assert frontmatter["tags"] == ["tag1", "tag2", "tag3"]

    def test_frontmatter_with_quoted_strings(self):
        content = """---
description: "A skill with quotes"
version: '2.0.0'
---

Content
"""
        frontmatter, body = parse_frontmatter(content)
        assert frontmatter["description"] == "A skill with quotes"
        assert frontmatter["version"] == "2.0.0"


class TestSkillProvider:
    """Tests for SkillProvider - single skill folder."""

    @pytest.fixture
    def single_skill_dir(self, tmp_path: Path) -> Path:
        """Create a single skill directory with files."""
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            """---
description: A test skill
version: "1.0.0"
---

# My Skill

This is my skill content.
"""
        )
        (skill_dir / "reference.md").write_text("# Reference\n\nExtra docs.")
        (skill_dir / "scripts").mkdir()
        (skill_dir / "scripts" / "helper.py").write_text('print("helper")')
        return skill_dir

    def test_loads_skill_at_init(self, single_skill_dir: Path):
        provider = SkillProvider(skill_path=single_skill_dir)
        assert provider.skill_info.name == "my-skill"
        assert provider.skill_info.description == "A test skill"
        assert len(provider.skill_info.files) == 3

    def test_raises_if_directory_missing(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError, match="Skill directory not found"):
            SkillProvider(skill_path=tmp_path / "nonexistent")

    def test_raises_if_main_file_missing(self, tmp_path: Path):
        skill_dir = tmp_path / "no-main"
        skill_dir.mkdir()
        with pytest.raises(FileNotFoundError, match="Main skill file not found"):
            SkillProvider(skill_path=skill_dir)

    async def test_list_resources_default_template_mode(self, single_skill_dir: Path):
        """In template mode (default), only main file and manifest are resources."""
        provider = SkillProvider(skill_path=single_skill_dir)
        resources = await provider.list_resources()

        assert len(resources) == 2
        names = {r.name for r in resources}
        assert "my-skill/SKILL.md" in names
        assert "my-skill/_manifest" in names

    async def test_list_resources_supporting_files_as_resources(
        self, single_skill_dir: Path
    ):
        """In resources mode, supporting files are also exposed as resources."""
        provider = SkillProvider(
            skill_path=single_skill_dir, supporting_files="resources"
        )
        resources = await provider.list_resources()

        # 2 standard + 2 supporting files
        assert len(resources) == 4
        names = {r.name for r in resources}
        assert "my-skill/SKILL.md" in names
        assert "my-skill/_manifest" in names
        assert "my-skill/reference.md" in names
        assert "my-skill/scripts/helper.py" in names

    async def test_list_templates_default_mode(self, single_skill_dir: Path):
        """In template mode (default), one template is exposed."""
        provider = SkillProvider(skill_path=single_skill_dir)
        templates = await provider.list_resource_templates()

        assert len(templates) == 1
        assert templates[0].name == "my-skill_files"

    async def test_list_templates_resources_mode(self, single_skill_dir: Path):
        """In resources mode, no templates are exposed."""
        provider = SkillProvider(
            skill_path=single_skill_dir, supporting_files="resources"
        )
        templates = await provider.list_resource_templates()

        assert templates == []

    async def test_read_main_file(self, single_skill_dir: Path):
        mcp = FastMCP("Test")
        mcp.add_provider(SkillProvider(skill_path=single_skill_dir))

        async with Client(mcp) as client:
            result = await client.read_resource(AnyUrl("skill://my-skill/SKILL.md"))
            assert len(result) == 1
            assert isinstance(result[0], TextResourceContents)
            assert "# My Skill" in result[0].text

    async def test_read_manifest(self, single_skill_dir: Path):
        mcp = FastMCP("Test")
        mcp.add_provider(SkillProvider(skill_path=single_skill_dir))

        async with Client(mcp) as client:
            result = await client.read_resource(AnyUrl("skill://my-skill/_manifest"))
            manifest = json.loads(result[0].text)
            assert manifest["skill"] == "my-skill"
            assert len(manifest["files"]) == 3
            paths = {f["path"] for f in manifest["files"]}
            assert "SKILL.md" in paths
            assert "reference.md" in paths
            assert "scripts/helper.py" in paths

    async def test_read_supporting_file_via_template(self, single_skill_dir: Path):
        mcp = FastMCP("Test")
        mcp.add_provider(SkillProvider(skill_path=single_skill_dir))

        async with Client(mcp) as client:
            result = await client.read_resource(AnyUrl("skill://my-skill/reference.md"))
            assert "# Reference" in result[0].text

    async def test_read_supporting_file_via_resource_mode(self, single_skill_dir: Path):
        mcp = FastMCP("Test")
        mcp.add_provider(
            SkillProvider(skill_path=single_skill_dir, supporting_files="resources")
        )

        async with Client(mcp) as client:
            result = await client.read_resource(AnyUrl("skill://my-skill/reference.md"))
            assert "# Reference" in result[0].text


class TestSkillsDirectoryProvider:
    """Tests for SkillsDirectoryProvider - scans directory for skill folders."""

    @pytest.fixture
    def skills_dir(self, tmp_path: Path) -> Path:
        """Create a test skills directory with sample skills."""
        skills_root = tmp_path / "skills"
        skills_root.mkdir()

        # Create a simple skill
        simple_skill = skills_root / "simple-skill"
        simple_skill.mkdir()
        (simple_skill / "SKILL.md").write_text(
            """---
description: A simple test skill
version: "1.0.0"
---

# Simple Skill

This is a simple skill for testing.
"""
        )

        # Create a skill with supporting files
        complex_skill = skills_root / "complex-skill"
        complex_skill.mkdir()
        (complex_skill / "SKILL.md").write_text(
            """---
description: A complex skill with supporting files
---

# Complex Skill

See [reference](reference.md) for more details.
"""
        )
        (complex_skill / "reference.md").write_text(
            """# Reference

Additional documentation.
"""
        )
        (complex_skill / "scripts").mkdir()
        (complex_skill / "scripts" / "helper.py").write_text(
            'print("Hello from helper")'
        )

        return skills_root

    async def test_list_resources_discovers_skills(self, skills_dir: Path):
        provider = SkillsDirectoryProvider(roots=skills_dir)
        resources = await provider.list_resources()

        # Should have 2 resources per skill (main file + manifest)
        assert len(resources) == 4

        # Check resource names
        resource_names = {r.name for r in resources}
        assert "simple-skill/SKILL.md" in resource_names
        assert "simple-skill/_manifest" in resource_names
        assert "complex-skill/SKILL.md" in resource_names
        assert "complex-skill/_manifest" in resource_names

    async def test_list_resources_includes_descriptions(self, skills_dir: Path):
        provider = SkillsDirectoryProvider(roots=skills_dir)
        resources = await provider.list_resources()

        # Find the simple-skill main resource
        simple_skill = next(r for r in resources if r.name == "simple-skill/SKILL.md")
        assert simple_skill.description == "A simple test skill"

    async def test_read_main_skill_file(self, skills_dir: Path):
        mcp = FastMCP("Test")
        mcp.add_provider(SkillsDirectoryProvider(roots=skills_dir))

        async with Client(mcp) as client:
            result = await client.read_resource(AnyUrl("skill://simple-skill/SKILL.md"))
            assert len(result) == 1
            assert isinstance(result[0], TextResourceContents)
            assert "# Simple Skill" in result[0].text

    async def test_read_manifest(self, skills_dir: Path):
        mcp = FastMCP("Test")
        mcp.add_provider(SkillsDirectoryProvider(roots=skills_dir))

        async with Client(mcp) as client:
            result = await client.read_resource(
                AnyUrl("skill://complex-skill/_manifest")
            )
            assert len(result) == 1
            assert isinstance(result[0], TextResourceContents)

            manifest = json.loads(result[0].text)
            assert manifest["skill"] == "complex-skill"
            assert len(manifest["files"]) == 3  # SKILL.md, reference.md, helper.py

            # Check file paths
            paths = {f["path"] for f in manifest["files"]}
            assert "SKILL.md" in paths
            assert "reference.md" in paths
            assert "scripts/helper.py" in paths

            # Check hashes are present
            for file_info in manifest["files"]:
                assert file_info["hash"].startswith("sha256:")
                assert file_info["size"] > 0

    async def test_list_resource_templates(self, skills_dir: Path):
        provider = SkillsDirectoryProvider(roots=skills_dir)
        templates = await provider.list_resource_templates()

        # One template per skill
        assert len(templates) == 2

        template_names = {t.name for t in templates}
        assert "simple-skill_files" in template_names
        assert "complex-skill_files" in template_names

    async def test_read_supporting_file_via_template(self, skills_dir: Path):
        mcp = FastMCP("Test")
        mcp.add_provider(SkillsDirectoryProvider(roots=skills_dir))

        async with Client(mcp) as client:
            result = await client.read_resource(
                AnyUrl("skill://complex-skill/reference.md")
            )
            assert len(result) == 1
            assert isinstance(result[0], TextResourceContents)
            assert "# Reference" in result[0].text

    async def test_read_nested_file_via_template(self, skills_dir: Path):
        mcp = FastMCP("Test")
        mcp.add_provider(SkillsDirectoryProvider(roots=skills_dir))

        async with Client(mcp) as client:
            result = await client.read_resource(
                AnyUrl("skill://complex-skill/scripts/helper.py")
            )
            assert len(result) == 1
            assert isinstance(result[0], TextResourceContents)
            assert "Hello from helper" in result[0].text

    async def test_empty_skills_directory(self, tmp_path: Path):
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()

        provider = SkillsDirectoryProvider(roots=empty_dir)
        resources = await provider.list_resources()
        assert resources == []

        templates = await provider.list_resource_templates()
        assert templates == []

    async def test_nonexistent_skills_directory(self, tmp_path: Path):
        nonexistent = tmp_path / "does-not-exist"
        provider = SkillsDirectoryProvider(roots=nonexistent)

        resources = await provider.list_resources()
        assert resources == []

    async def test_reload_mode(self, skills_dir: Path):
        provider = SkillsDirectoryProvider(roots=skills_dir, reload=True)

        # Initial load
        resources = await provider.list_resources()
        assert len(resources) == 4

        # Add a new skill
        new_skill = skills_dir / "new-skill"
        new_skill.mkdir()
        (new_skill / "SKILL.md").write_text(
            """---
description: A new skill
---

# New Skill
"""
        )

        # Reload should pick up the new skill
        resources = await provider.list_resources()
        assert len(resources) == 6

    async def test_skill_without_frontmatter_uses_header_as_description(
        self, tmp_path: Path
    ):
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        skill = skills_dir / "no-frontmatter"
        skill.mkdir()
        (skill / "SKILL.md").write_text("# My Skill Title\n\nSome content.")

        provider = SkillsDirectoryProvider(roots=skills_dir)
        resources = await provider.list_resources()

        main_resource = next(
            r for r in resources if r.name == "no-frontmatter/SKILL.md"
        )
        assert main_resource.description == "My Skill Title"

    async def test_supporting_files_as_resources(self, skills_dir: Path):
        """Test that supporting_files='resources' shows all files."""
        provider = SkillsDirectoryProvider(
            roots=skills_dir, supporting_files="resources"
        )
        resources = await provider.list_resources()

        # 2 skills * 2 standard resources + complex skill has 2 supporting files
        # simple-skill: SKILL.md, _manifest (2)
        # complex-skill: SKILL.md, _manifest, reference.md, scripts/helper.py (4)
        assert len(resources) == 6

        names = {r.name for r in resources}
        assert "complex-skill/reference.md" in names
        assert "complex-skill/scripts/helper.py" in names

    async def test_supporting_files_as_resources_no_templates(self, skills_dir: Path):
        """In resources mode, no templates should be exposed."""
        provider = SkillsDirectoryProvider(
            roots=skills_dir, supporting_files="resources"
        )
        templates = await provider.list_resource_templates()
        assert templates == []


class TestMultiDirectoryProvider:
    """Tests for multi-directory support in SkillsDirectoryProvider."""

    @pytest.fixture
    def multi_skills_dirs(self, tmp_path: Path) -> tuple[Path, Path]:
        """Create two separate skills directories."""
        root1 = tmp_path / "skills1"
        root1.mkdir()
        skill1 = root1 / "skill-a"
        skill1.mkdir()
        (skill1 / "SKILL.md").write_text(
            """---
description: Skill A from root 1
---
# Skill A
"""
        )

        root2 = tmp_path / "skills2"
        root2.mkdir()
        skill2 = root2 / "skill-b"
        skill2.mkdir()
        (skill2 / "SKILL.md").write_text(
            """---
description: Skill B from root 2
---
# Skill B
"""
        )

        return root1, root2

    async def test_multiple_roots_discover_all_skills(self, multi_skills_dirs):
        """Test that skills from multiple roots are all discovered."""
        root1, root2 = multi_skills_dirs
        provider = SkillsDirectoryProvider(roots=[root1, root2])

        resources = await provider.list_resources()
        # 2 skills * 2 resources each = 4 total
        assert len(resources) == 4

        resource_names = {r.name for r in resources}
        assert "skill-a/SKILL.md" in resource_names
        assert "skill-a/_manifest" in resource_names
        assert "skill-b/SKILL.md" in resource_names
        assert "skill-b/_manifest" in resource_names

    async def test_duplicate_skill_names_first_wins(self, tmp_path: Path):
        """Test that if a skill appears in multiple roots, first one wins."""
        root1 = tmp_path / "root1"
        root1.mkdir()
        skill1 = root1 / "duplicate-skill"
        skill1.mkdir()
        (skill1 / "SKILL.md").write_text(
            """---
description: First occurrence
---
# First
"""
        )

        root2 = tmp_path / "root2"
        root2.mkdir()
        skill2 = root2 / "duplicate-skill"
        skill2.mkdir()
        (skill2 / "SKILL.md").write_text(
            """---
description: Second occurrence
---
# Second
"""
        )

        provider = SkillsDirectoryProvider(roots=[root1, root2])
        resources = await provider.list_resources()

        # Should only have one skill (first one)
        assert len(resources) == 2  # SKILL.md + _manifest

        # Should be the first one
        main_resource = next(
            r for r in resources if r.name == "duplicate-skill/SKILL.md"
        )
        assert main_resource.description == "First occurrence"

    async def test_single_path_as_list(self, multi_skills_dirs):
        """Test that single path can be passed as a list."""
        root1, _ = multi_skills_dirs
        provider = SkillsDirectoryProvider(roots=[root1])

        resources = await provider.list_resources()
        assert len(resources) == 2  # skill-a has 2 resources

    async def test_single_path_as_string(self, multi_skills_dirs):
        """Test that single path can be passed as string."""
        root1, _ = multi_skills_dirs
        provider = SkillsDirectoryProvider(roots=str(root1))

        resources = await provider.list_resources()
        assert len(resources) == 2

    async def test_nonexistent_roots_handled_gracefully(self, tmp_path: Path):
        """Test that non-existent roots don't cause errors."""
        existent = tmp_path / "exists"
        existent.mkdir()
        skill = existent / "test-skill"
        skill.mkdir()
        (skill / "SKILL.md").write_text("# Test\n\nContent")

        nonexistent = tmp_path / "does-not-exist"

        provider = SkillsDirectoryProvider(roots=[existent, nonexistent])
        resources = await provider.list_resources()

        # Should still find skills from existing root
        assert len(resources) == 2

    async def test_empty_roots_list(self, tmp_path: Path):
        """Test that empty roots list results in no skills."""
        provider = SkillsDirectoryProvider(roots=[])
        resources = await provider.list_resources()
        assert resources == []


class TestSkillsProviderAlias:
    """Test that SkillsProvider is a backwards-compatible alias."""

    def test_skills_provider_is_alias(self):
        assert SkillsProvider is SkillsDirectoryProvider


class TestClaudeSkillsProvider:
    def test_default_root_is_claude_skills_dir(self, tmp_path: Path, monkeypatch):
        # Mock Path.home() to return a temp path (use tmp_path for cross-platform compatibility)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        provider = ClaudeSkillsProvider()
        assert provider._roots == [tmp_path / ".claude" / "skills"]

    def test_main_file_name_is_skill_md(self):
        provider = ClaudeSkillsProvider()
        assert provider._main_file_name == "SKILL.md"

    def test_supporting_files_parameter(self):
        provider = ClaudeSkillsProvider(supporting_files="resources")
        assert provider._supporting_files == "resources"


class TestPathTraversalPrevention:
    async def test_path_traversal_blocked(self, tmp_path: Path):
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        skill = skills_dir / "test-skill"
        skill.mkdir()
        (skill / "SKILL.md").write_text("# Test\n\nContent")

        # Create a file outside the skill directory
        secret_file = tmp_path / "secret.txt"
        secret_file.write_text("SECRET DATA")

        mcp = FastMCP("Test")
        mcp.add_provider(SkillsDirectoryProvider(roots=skills_dir))

        async with Client(mcp) as client:
            # Path traversal attempts should fail (either normalized away or blocked)
            # The important thing is that SECRET DATA is never returned
            with pytest.raises(Exception):
                result = await client.read_resource(
                    AnyUrl("skill://test-skill/../../../secret.txt")
                )
                # If we somehow got here, ensure we didn't get the secret
                if result:
                    for content in result:
                        if hasattr(content, "text"):
                            assert "SECRET DATA" not in content.text
