"""Containment tests for the model download destination.

⚠️ REGRESSION SUITE. The Hugging Face branch of POST /v1/node/models/download
performed no validation on `filename` whatsoever: it went straight into
`model_dir / filename`, and the worker ends with `tmp_path.rename(dest_path)`.
A caller holding the node API key could therefore write a file anywhere the
node process could write. It was found by probing the iOS node, which had the
same class of bug, and then reading this path to see whether it was shared.
"""

from pathlib import Path

import pytest

from mycellm.api.models import _safe_model_dest


@pytest.fixture
def model_dir(tmp_path: Path) -> Path:
    d = tmp_path / "models"
    d.mkdir()
    return d


class TestEscapesAreRefused:
    @pytest.mark.parametrize("filename", [
        "../evil.gguf",
        "../../evil.gguf",
        "../../../etc/cron.d/evil",
        "sub/../../evil.gguf",
        "..",
        ".",
        "",
    ])
    def test_traversal_is_refused(self, model_dir, filename):
        assert _safe_model_dest(model_dir, filename) is None

    def test_absolute_path_is_refused(self, model_dir):
        # `model_dir / "/etc/passwd"` is "/etc/passwd" — pathlib discards the
        # left side entirely when the right side is absolute, which is the
        # trap that makes this look safe when read quickly.
        assert _safe_model_dest(model_dir, "/etc/passwd") is None
        assert _safe_model_dest(model_dir, "/tmp/evil.gguf") is None

    def test_no_accepted_name_ever_escapes(self, model_dir):
        """The property, stated directly: whatever comes back is inside."""
        for filename in [
            "m.gguf", "sub/m.gguf", "../evil", "..", "/etc/passwd",
            "a/../b.gguf", "a/../../b.gguf", "./m.gguf", "x/./y.gguf",
        ]:
            dest = _safe_model_dest(model_dir, filename)
            if dest is None:
                continue
            resolved = dest.resolve()
            assert resolved.is_relative_to(model_dir.resolve()), (
                f"{filename} resolved to {resolved}, outside {model_dir}"
            )


class TestLegitimateNamesWork:
    def test_plain_filename(self, model_dir):
        dest = _safe_model_dest(model_dir, "Qwen2.5-3B-Instruct-Q4_K_M.gguf")
        assert dest == model_dir / "Qwen2.5-3B-Instruct-Q4_K_M.gguf"

    def test_repo_subdirectory_is_allowed(self, model_dir):
        # A Hugging Face repo may hold the GGUF inside a quant-named directory;
        # refusing these outright would break real downloads.
        dest = _safe_model_dest(model_dir, "Q4_K_M/model.gguf")
        assert dest == model_dir / "Q4_K_M" / "model.gguf"
        assert dest.resolve().is_relative_to(model_dir.resolve())

    def test_inner_relative_segment_that_stays_inside(self, model_dir):
        dest = _safe_model_dest(model_dir, "a/../m.gguf")
        assert dest is not None
        assert dest.resolve() == (model_dir / "m.gguf").resolve()

    def test_dots_in_the_name_are_fine(self, model_dir):
        for name in ["my-finetune.v2.gguf", "model.Q4_K_M.gguf"]:
            assert _safe_model_dest(model_dir, name) is not None


class TestMatchesDeleteFile:
    """delete-file already resolved-and-contained; download now agrees.

    Two endpoints on the same directory disagreeing about what a safe path is
    is how one of them ends up wrong again.
    """

    def test_same_verdict_on_traversal(self, model_dir):
        filename = "../evil.gguf"
        assert _safe_model_dest(model_dir, filename) is None

        # The delete-file containment test, inline, for comparison.
        target = model_dir / filename
        with pytest.raises(ValueError):
            target.resolve().relative_to(model_dir.resolve())
